"""Health, storage, AI providers, publishing, requirements, and reports."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import active_membership, client_ip, current_user, rate_limit, require_admin, require_project
from app.api.routes.auth import store_secret
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import decrypt_secret
from app.db.session import get_db
from app.integrations.ai import client_for_row
from app.integrations.storage import Storage
from app.integrations.wordpress import create_draft, test_connection
from app.models import (
    AIProvider,
    AcceptanceCriterion,
    Credential,
    Document,
    DocumentVersion,
    Evidence,
    Organization,
    Project,
    PublicationRecord,
    PublishingDestination,
    Requirement,
    TestExecution,
    VerificationRun,
)
from app.models.entities import utcnow
from app.verification.engine import coverage, record_execution

router = APIRouter(tags=["system"])


class AIProviderRequest(BaseModel):
    provider: str
    api_key: str = ""
    endpoint: str = ""
    model: str = ""
    display_name: str = ""
    allow_paid_fallback: bool = False
    fallback_provider_id: str = ""


class AIConnectRequest(BaseModel):
    provider: str
    api_key: str
    model: str = ""
    display_name: str = ""
    allow_paid_fallback: bool = False
    fallback_provider_id: str = ""


class AIModelSelectRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class StorageRequest(BaseModel):
    backend: str
    endpoint: str = ""
    bucket: str = ""
    region: str = ""
    access_key: str = ""
    secret_key: str = ""


class DestinationRequest(BaseModel):
    label: str = "WordPress"
    site_url: str
    username: str
    application_password: str
    project_id: str | None = None


class PublishRequest(BaseModel):
    destination_id: str
    document_version_id: str
    status: str = "draft"
    idempotency_key: str = ""


class ReviewRequest(BaseModel):
    criterion_id: str
    result: str
    note: str = Field(min_length=3, max_length=2000)


def _provider_body(row: AIProvider, hint: str) -> dict:
    discovered = row.discovered_models or []
    free_models = [
        item
        for item in discovered
        if isinstance(item, dict) and item.get("billing") == "free" and item.get("chat", True)
    ]
    return {
        "id": row.id,
        "provider": row.provider,
        "display_name": row.display_name or row.provider,
        "endpoint": row.endpoint,
        "model": row.model,
        "status": row.status,
        "status_detail": row.status_detail,
        "error_category": row.error_category,
        "allow_paid_fallback": bool(row.allow_paid_fallback),
        "fallback_provider_id": row.fallback_provider_id or "",
        "capabilities": row.capabilities or {},
        "api_key_hint": hint,
        "last_tested_at": row.last_tested_at.isoformat() if row.last_tested_at else None,
        "discovered_models": discovered,
        "free_models": free_models,
        "free_model_count": len(free_models),
    }


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "api"}


@router.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "PostgreSQL is not reachable. Check DATABASE_URL and that the database service is running."
    try:
        Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).ping()
        checks["redis"] = "ok"
    except RedisError:
        checks["redis"] = "Redis is not reachable. Check REDIS_URL."
    try:
        Storage().probe()
        checks["storage"] = "ok"
    except Exception as exc:
        checks["storage"] = str(exc) if isinstance(exc, AppError) else "Storage is not writable."
    if settings.require_workers:
        try:
            client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
            missing = [name for name in ("default", "browser") if not client.get(f"worker-heartbeat:{name}")]
            checks["workers"] = "ok" if not missing else "No heartbeat from worker queues: " + ", ".join(missing)
        except RedisError:
            checks["workers"] = "Worker heartbeats could not be read because Redis is unavailable."
    else:
        checks["workers"] = "not_required"
    failed = {key: value for key, value in checks.items() if value not in {"ok", "not_required"}}
    body = {"status": "ok" if not failed else "unavailable", "checks": checks}
    if failed:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content=body)
    return body


@router.get("/api/v1/system/storage")
def storage_status(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    organization = db.get(Organization, membership.organization_id)
    storage = _org_storage(db, organization)
    return {"backend": storage.backend, "bucket": storage.bucket if storage.backend == "s3" else "", "writable": True}


@router.post("/api/v1/system/storage/test")
def storage_test(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    organization = db.get(Organization, membership.organization_id)
    return _org_storage(db, organization).probe()


@router.put("/api/v1/system/storage")
def storage_update(payload: StorageRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    if payload.backend not in {"local", "s3", "inherit"}:
        raise AppError(422, "validation_error", "Storage backend must be local, s3, or inherit.")
    organization = db.get(Organization, membership.organization_id)
    organization.storage_backend = payload.backend
    organization.s3_endpoint = payload.endpoint
    organization.s3_bucket = payload.bucket
    organization.s3_region = payload.region
    if payload.access_key or payload.secret_key:
        if not payload.access_key or not payload.secret_key:
            raise AppError(422, "validation_error", "Provide both the S3 access key and secret key.")
        credential = store_secret(
            db,
            organization.id,
            None,
            "s3",
            "S3",
            payload.access_key + "\n" + payload.secret_key,
        )
        organization.storage_credential_id = credential.id
    db.commit()
    return storage_status(request, db)


@router.get("/api/v1/ai-providers")
def list_providers(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    rows = db.scalars(select(AIProvider).where(AIProvider.organization_id == membership.organization_id)).all()
    result = []
    for row in rows:
        credential = db.get(Credential, row.credential_id) if row.credential_id else None
        result.append(_provider_body(row, credential.hint if credential else ""))
    return {"providers": result}


def _store_provider_row(
    db: Session,
    membership,
    *,
    provider: str,
    api_key: str,
    endpoint: str,
    model: str,
    display_name: str,
    allow_paid_fallback: bool,
    fallback_provider_id: str,
) -> tuple[AIProvider, Credential]:
    from app.core.security import require_http_secret
    from app.integrations.providers import get_adapter, resolve_endpoint

    adapter = get_adapter(provider)
    name = display_name.strip() or adapter.label
    resolve_endpoint(provider, endpoint, allow_local=get_settings().allow_local_targets)
    stored_endpoint = adapter.default_endpoint if adapter.managed else endpoint
    row = db.scalar(
        select(AIProvider).where(
            AIProvider.organization_id == membership.organization_id,
            AIProvider.provider == provider,
            AIProvider.display_name == name,
        )
    )
    if row is None and not api_key:
        raise AppError(422, "validation_error", "An API key is required when saving a new provider.")
    if api_key:
        credential = store_secret(
            db,
            membership.organization_id,
            None,
            "ai",
            name,
            require_http_secret(api_key),
        )
    elif row and row.credential_id:
        credential = db.get(Credential, row.credential_id)
    else:
        raise AppError(422, "validation_error", "An API key is required when saving a new provider.")
    if credential is None:
        raise AppError(422, "validation_error", "An API key is required when saving a new provider.")
    if row is None:
        row = AIProvider(organization_id=membership.organization_id, provider=provider, display_name=name)
        db.add(row)
    row.display_name = name
    row.endpoint = stored_endpoint
    row.model = model
    row.allow_paid_fallback = allow_paid_fallback
    row.fallback_provider_id = fallback_provider_id
    row.capabilities = adapter.capabilities()
    row.credential_id = credential.id
    row.status = "untested"
    row.status_detail = "Saved. Run the connection test before treating this provider as connected."
    row.error_category = ""
    db.commit()
    db.refresh(row)
    return row, credential


@router.post("/api/v1/ai-providers", status_code=201)
def save_provider(payload: AIProviderRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    row, credential = _store_provider_row(
        db,
        membership,
        provider=payload.provider,
        api_key=payload.api_key,
        endpoint=payload.endpoint,
        model=payload.model,
        display_name=payload.display_name,
        allow_paid_fallback=payload.allow_paid_fallback,
        fallback_provider_id=payload.fallback_provider_id,
    )
    return _provider_body(row, credential.hint)


def _sync_provider_models(row: AIProvider, adapter, client) -> str:
    """Persist discovered models and replace missing/invalid model IDs automatically."""
    from app.integrations.providers import _model_known, free_chat_models, prioritize_models_for_storage

    models = client.last_result.models or []
    if models:
        stored = prioritize_models_for_storage(models)
        row.discovered_models = [item.as_dict() for item in stored]
        chosen = (row.model or "").strip()
        if not chosen or not _model_known(chosen, models):
            row.model = adapter.prefer_model(models)
            client.model = row.model
            free_count = len(free_chat_models(models))
            if row.provider == "openrouter" and free_count:
                return (
                    f"Provider connection succeeded. Selected model: {row.model}. "
                    f"Discovered {free_count} free chat model(s)."
                )
            return f"Provider connection succeeded. Selected model: {row.model}."
        if row.provider == "openrouter":
            free_count = len(free_chat_models(models))
            if free_count:
                return f"Provider connection succeeded. Using {row.model}. Discovered {free_count} free chat model(s)."
    return client.last_result.message or "Provider connection succeeded."


@router.post("/api/v1/ai-providers/connect")
def connect_provider(payload: AIConnectRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Save credentials, validate the key, discover models, and auto-select a model when needed."""
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    from app.integrations.providers import get_adapter

    if not payload.api_key.strip():
        raise AppError(422, "validation_error", "An API key is required to connect.")
    rate_limit(f"ai-connect:{membership.organization_id}:{client_ip(request)}", 20, 60)
    adapter = get_adapter(payload.provider)
    # Managed providers ignore client-supplied model IDs — browsers often autofill an email address here.
    requested_model = "" if adapter.managed else payload.model
    row, credential = _store_provider_row(
        db,
        membership,
        provider=payload.provider,
        api_key=payload.api_key,
        endpoint="",
        model=requested_model,
        display_name=payload.display_name,
        allow_paid_fallback=payload.allow_paid_fallback,
        fallback_provider_id=payload.fallback_provider_id,
    )
    client = client_for_row(row, decrypt_secret(credential.ciphertext), allow_local=get_settings().allow_local_targets)
    ok, detail = client.test_connection()
    if client.last_result.category in {"success", "model_replaced"} and (ok or client.last_result.models):
        ok = True
        detail = _sync_provider_models(row, adapter, client)
    row.status = "connected" if ok else "failed"
    row.status_detail = detail
    row.error_category = "" if ok else client.last_result.category
    row.last_tested_at = utcnow()
    db.commit()
    body = _provider_body(row, credential.hint)
    body["models"] = row.discovered_models or []
    body["connected"] = ok
    return body


@router.post("/api/v1/ai-providers/{provider_id}/test")
def test_provider(provider_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    from app.integrations.providers import get_adapter

    row = db.get(AIProvider, provider_id)
    if row is None or row.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "AI provider not found.")
    credential = db.get(Credential, row.credential_id) if row.credential_id else None
    if credential is None:
        raise AppError(422, "configuration_error", "This provider has no saved API key.")
    rate_limit(f"ai-test:{membership.organization_id}:{client_ip(request)}", 30, 60)
    adapter = get_adapter(row.provider)
    client = client_for_row(row, decrypt_secret(credential.ciphertext), allow_local=get_settings().allow_local_targets)
    ok, detail = client.test_connection()
    if client.last_result.category in {"success", "model_replaced"} and (ok or client.last_result.models):
        ok = True
        detail = _sync_provider_models(row, adapter, client)
    row.status = "connected" if ok else "failed"
    row.status_detail = detail
    row.error_category = "" if ok else client.last_result.category
    row.last_tested_at = utcnow()
    db.commit()
    return _provider_body(row, credential.hint)


@router.post("/api/v1/ai-providers/{provider_id}/models")
def discover_models(provider_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    rate_limit(f"ai-models:{membership.organization_id}:{client_ip(request)}", 30, 60)
    from app.integrations.providers import free_chat_models, prioritize_models_for_storage

    row = db.get(AIProvider, provider_id)
    if row is None or row.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "AI provider not found.")
    credential = db.get(Credential, row.credential_id) if row.credential_id else None
    if credential is None:
        raise AppError(422, "configuration_error", "This provider has no saved API key.")
    client = client_for_row(row, decrypt_secret(credential.ciphertext), allow_local=get_settings().allow_local_targets)
    result = client.discover_models()
    if result.models:
        stored = prioritize_models_for_storage(result.models)
        row.discovered_models = [item.as_dict() for item in stored]
        chosen = (row.model or "").strip()
        from app.integrations.providers import _model_known, get_adapter

        adapter = get_adapter(row.provider)
        if not chosen or not _model_known(chosen, result.models):
            row.model = adapter.prefer_model(result.models)
    if not result.ok:
        row.error_category = result.category
    db.commit()
    free = free_chat_models(result.models) if result.models else []
    message = result.message
    if result.ok and row.provider == "openrouter" and free:
        message = f"{result.message} Discovered {len(free)} free chat model(s)."
    return {
        "ok": result.ok,
        "error_category": result.category,
        "message": message,
        "model": row.model,
        "models": row.discovered_models or [],
        "free_models": [item.as_dict() for item in free],
        "free_model_count": len(free),
    }


@router.post("/api/v1/ai-providers/{provider_id}/model")
def select_provider_model(provider_id: str, payload: AIModelSelectRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    """Select a discovered model. OpenRouter keeps generation on free chat models by default."""
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    from app.integrations.providers import OpenRouterAdapter, _model_known
    from app.integrations.providers import ModelInfo

    row = db.get(AIProvider, provider_id)
    if row is None or row.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "AI provider not found.")
    model_id = payload.model.strip()
    discovered = row.discovered_models or []
    models = [
        ModelInfo(
            id=str(item.get("id") or ""),
            billing=str(item.get("billing") or "unknown"),
            name=str(item.get("name") or item.get("id") or ""),
            chat=bool(item.get("chat", True)),
        )
        for item in discovered
        if isinstance(item, dict) and item.get("id")
    ]
    if models and not _model_known(model_id, models):
        raise AppError(422, "validation_error", "Choose a model from the discovered provider list.")
    if row.provider == "openrouter" and models:
        match = next((item for item in models if item.id == model_id or item.id.endswith("/" + model_id)), None)
        if match is None or match.billing != "free" or not match.chat:
            raise AppError(
                422,
                "validation_error",
                "OpenRouter model selection is limited to free chat models (including openrouter/free).",
            )
    row.model = model_id
    if row.provider == "openrouter" and model_id == OpenRouterAdapter.FREE_ROUTER_ID:
        row.status_detail = "Using Free Models Router — OpenRouter picks among available free models."
    else:
        row.status_detail = f"Selected model: {model_id}."
    db.commit()
    credential = db.get(Credential, row.credential_id) if row.credential_id else None
    return _provider_body(row, credential.hint if credential else "")


@router.delete("/api/v1/ai-providers/{provider_id}")
def delete_provider(provider_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    row = db.get(AIProvider, provider_id)
    if row is None or row.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "AI provider not found.")
    db.delete(row)
    db.commit()
    return {"deleted": True}


@router.get("/api/v1/publishing/destinations")
def list_destinations(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    rows = db.scalars(select(PublishingDestination).where(PublishingDestination.organization_id == membership.organization_id)).all()
    return {"destinations": [_destination_body(row) for row in rows]}


@router.post("/api/v1/publishing/destinations", status_code=201)
def create_destination(payload: DestinationRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    if payload.project_id:
        require_project(db, membership, payload.project_id)
    credential = store_secret(
        db,
        membership.organization_id,
        payload.project_id,
        "wordpress",
        payload.username,
        payload.application_password,
    )
    row = PublishingDestination(
        organization_id=membership.organization_id,
        project_id=payload.project_id,
        kind="wordpress",
        label=payload.label,
        config={"site_url": payload.site_url, "username": payload.username},
        credential_id=credential.id,
        status="untested",
        status_detail="Saved. Run the connection test before publishing.",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _destination_body(row)


@router.post("/api/v1/publishing/destinations/{destination_id}/test")
def test_destination(destination_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    row = _destination(db, membership.organization_id, destination_id)
    ok, detail = _wordpress_call(db, row)
    row.status = "connected" if ok else "failed"
    row.status_detail = detail
    row.last_tested_at = utcnow()
    db.commit()
    return _destination_body(row)


@router.post("/api/v1/publishing/posts", status_code=201)
def publish(payload: PublishRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    destination = _destination(db, membership.organization_id, payload.destination_id)
    if destination.status != "connected":
        raise AppError(422, "publishing_error", "Test the WordPress connection before creating a post.")
    version = db.get(DocumentVersion, payload.document_version_id)
    document = db.get(Document, version.document_id) if version else None
    project = db.get(Project, document.project_id) if document else None
    if version is None or project is None or project.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "Document version not found.")
    if version.format not in {"html", "markdown"}:
        raise AppError(422, "validation_error", "Publish an HTML or Markdown version.")
    key = payload.idempotency_key or f"{destination.id}:{version.id}:{payload.status}"
    existing = db.scalar(
        select(PublicationRecord).where(PublicationRecord.destination_id == destination.id, PublicationRecord.idempotency_key == key)
    )
    if existing is not None:
        return _publication_body(existing)
    storage = _org_storage(db, db.get(Organization, membership.organization_id))
    content = storage.get_bytes(version.storage_key).decode("utf-8")
    credential = db.get(Credential, destination.credential_id)
    result = create_draft(
        destination.config["site_url"],
        destination.config["username"],
        decrypt_secret(credential.ciphertext),
        title=document.title,
        content=content,
        status=payload.status,
        allow_local=get_settings().allow_local_targets,
    )
    record = PublicationRecord(
        destination_id=destination.id,
        document_version_id=version.id,
        idempotency_key=key,
        status=result["status"],
        remote_id=result["remote_id"],
        remote_url=result["remote_url"],
        log=f"WordPress confirmed status {result['status']}.",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return _publication_body(record)


@router.get("/api/v1/requirements")
def list_requirements(request: Request, scope: str = "platform", project_id: str = "", db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    query = select(Requirement)
    if scope == "platform":
        query = query.where(Requirement.scope == "platform")
    elif scope == "project":
        project = require_project(db, membership, project_id)
        query = query.where(Requirement.project_id == project.id)
    else:
        raise AppError(422, "validation_error", "Scope must be platform or project.")
    rows = db.scalars(query.order_by(Requirement.requirement_key)).all()
    return {"requirements": [_requirement_body(db, row) for row in rows]}


@router.get("/api/v1/requirements/{requirement_id}")
def get_requirement(requirement_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    row = db.get(Requirement, requirement_id)
    if row is None:
        row = db.scalar(select(Requirement).where(Requirement.requirement_key == requirement_id))
    if row is None or (row.organization_id and row.organization_id != membership.organization_id):
        raise AppError(404, "permission_error", "Requirement not found.")
    return _requirement_body(db, row)


@router.post("/api/v1/requirements/{requirement_id}/reviews", status_code=201)
def review_requirement(requirement_id: str, payload: ReviewRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    if payload.result not in {"passed", "failed"}:
        raise AppError(422, "validation_error", "Review result must be passed or failed.")
    row = db.get(Requirement, requirement_id)
    if row is None or (row.organization_id and row.organization_id != membership.organization_id):
        raise AppError(404, "permission_error", "Requirement not found.")
    criterion = db.get(AcceptanceCriterion, payload.criterion_id)
    if criterion is None or criterion.requirement_id != row.id:
        raise AppError(404, "permission_error", "Acceptance criterion not found.")
    if criterion.verification_method != "human_review":
        raise AppError(422, "validation_error", "Automated criteria are updated only by executed checks.")
    record_execution(
        db,
        requirement_key=row.requirement_key,
        criterion_key=criterion.criterion_key,
        result=payload.result,
        actual_result=payload.note,
        environment=f"human-review:{user.id}",
        evidence_summary=payload.note,
        organization_id=membership.organization_id,
        project_id=row.project_id,
    )
    db.commit()
    db.refresh(row)
    return _requirement_body(db, row)


@router.get("/api/v1/verification-runs")
def list_verification_runs(request: Request, db: Session = Depends(get_db)) -> dict:
    current_user(request, db)
    rows = db.scalars(select(VerificationRun).order_by(VerificationRun.created_at.desc())).all()
    current = _code_revision()
    return {
        "current_code_revision": current,
        "note": "A verification run is evidence from the database named in that run. It does not replace requirement status in this database.",
        "runs": [_verification_body(row, current) for row in rows],
    }


@router.post("/api/v1/verification-runs/import", status_code=201)
def import_verification_run(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    directory = get_settings().resolved_requirements_dir
    path = directory / "verification-report.json"
    if not path.is_file():
        raise AppError(404, "configuration_error", "verification-report.json was not found. Run the test suite before importing it.")
    summary = json.loads(path.read_text(encoding="utf-8"))
    summary["imported"] = True
    summary["requirement_rows_updated"] = False
    row = VerificationRun(
        source=str(summary.get("source") or "import"),
        database_name=str(summary.get("database_name") or ""),
        code_revision=str(summary.get("code_revision") or ""),
        environment=str(summary.get("environment") or ""),
        summary=summary,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _verification_body(row, _code_revision())


def _code_revision() -> str:
    import subprocess

    try:
        completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    revision = completed.stdout.strip()
    return revision or "unknown"


def _verification_body(row: VerificationRun, current: str) -> dict:
    matches = bool(row.code_revision) and row.code_revision == current
    return {
        "id": row.id,
        "source": row.source,
        "database_name": row.database_name,
        "code_revision": row.code_revision,
        "environment": row.environment,
        "created_at": row.created_at.isoformat(),
        "matches_current_revision": matches,
        "authoritative_for_current_code": False if not matches else bool((row.summary or {}).get("authoritative_for_current_code")),
        "verified_requirements": (row.summary or {}).get("verified_requirements"),
        "total_requirements": (row.summary or {}).get("total_requirements"),
        "note": "Historical evidence. Requirement rows in this database stay on their own status.",
    }


@router.get("/api/v1/reports/coverage")
def coverage_report(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    return coverage(db, membership.organization_id)


@router.get("/api/v1/reports/verification")
def verification_report(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    report = coverage(db, membership.organization_id)
    requirements = db.scalars(
        select(Requirement).where((Requirement.organization_id == None) | (Requirement.organization_id == membership.organization_id))  # noqa: E711
    ).all()
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["requirements"] = [
        {"id": row.requirement_key, "status": row.status, "blockers": row.blockers} for row in requirements
    ]
    return report


def _org_storage(db: Session, organization: Organization) -> Storage:
    credential = db.get(Credential, organization.storage_credential_id) if organization and organization.storage_credential_id else None
    return Storage(organization, credential)


def _destination(db: Session, organization_id: str, destination_id: str) -> PublishingDestination:
    row = db.get(PublishingDestination, destination_id)
    if row is None or row.organization_id != organization_id:
        raise AppError(404, "permission_error", "Publishing destination not found.")
    return row


def _destination_body(row: PublishingDestination) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "label": row.label,
        "site_url": row.config.get("site_url", ""),
        "username": row.config.get("username", ""),
        "status": row.status,
        "status_detail": row.status_detail,
        "project_id": row.project_id,
    }


def _wordpress_call(db: Session, row: PublishingDestination) -> tuple[bool, str]:
    credential = db.get(Credential, row.credential_id) if row.credential_id else None
    if credential is None:
        return False, "No application password is saved."
    return test_connection(
        row.config.get("site_url", ""),
        row.config.get("username", ""),
        decrypt_secret(credential.ciphertext),
        allow_local=get_settings().allow_local_targets,
    )


def _publication_body(row: PublicationRecord) -> dict:
    return {
        "id": row.id,
        "status": row.status,
        "remote_id": row.remote_id,
        "remote_url": row.remote_url,
        "log": row.log,
        "document_version_id": row.document_version_id,
    }


def _requirement_body(db: Session, row: Requirement) -> dict:
    criteria = db.scalars(select(AcceptanceCriterion).where(AcceptanceCriterion.requirement_id == row.id)).all()
    executions = db.scalars(select(TestExecution).where(TestExecution.requirement_id == row.id)).all()
    evidence = db.scalars(select(Evidence).where(Evidence.requirement_id == row.id)).all()
    return {
        "id": row.id,
        "key": row.requirement_key,
        "title": row.title,
        "description": row.description,
        "category": row.category,
        "priority": row.priority,
        "scope": row.scope,
        "status": row.status,
        "version": row.version,
        "blockers": row.blockers,
        "implementation_refs": row.implementation_refs,
        "project_id": row.project_id,
        "acceptance_criteria": [
            {
                "id": item.id,
                "key": item.criterion_key,
                "description": item.description,
                "verification_method": item.verification_method,
                "status": item.status,
                "mandatory": item.mandatory,
            }
            for item in criteria
        ],
        "test_executions": [
            {
                "id": item.id,
                "criterion_id": item.criterion_id,
                "result": item.result,
                "actual_result": item.actual_result,
                "environment": item.environment,
                "error_detail": item.error_detail,
                "executed_at": item.executed_at.isoformat(),
                "evidence_ids": item.evidence_ids,
            }
            for item in executions
        ],
        "evidence": [
            {"id": item.id, "kind": item.kind, "summary": item.summary, "storage_key": bool(item.storage_key)}
            for item in evidence
        ],
    }
