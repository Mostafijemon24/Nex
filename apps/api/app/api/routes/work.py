"""Projects, inputs, jobs, and analysis results."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.source import clone_repository, safe_extract_zip
from app.api.deps import active_membership, current_user, require_admin, require_project
from app.api.routes.auth import login_payload, store_secret
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.ssrf import validate_public_http_url
from app.db.session import get_db
from app.integrations.storage import Storage
from app.models import (
    AIProvider,
    AnalysisRun,
    ChangeRecord,
    Credential,
    DiscoveredPage,
    Document,
    DocumentVersion,
    Feature,
    InputSource,
    Job,
    JobStep,
    MonitorSchedule,
    Organization,
    Project,
    Screenshot,
    Video,
    Workflow,
)
from app.models.entities import new_id, utcnow
from app.services.jobs import DEFAULT_PROJECT_SETTINGS, create_job, set_control, validate_settings

router = APIRouter(prefix="/api/v1", tags=["work"])
UPLOAD_KINDS = {"zip", "wp_plugin", "wp_theme", "documentation", "screenshots", "recording", "api_spec"}
SOURCE_KINDS = UPLOAD_KINDS | {"website", "github", "git"}


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class SettingsRequest(BaseModel):
    settings: dict


class SourceRequest(BaseModel):
    kind: str
    name: str = Field(min_length=1, max_length=200)
    config: dict = {}
    password: str = ""


class JobRequest(BaseModel):
    job_type: str = "analysis"


class AccountRequest(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = ""
    role: str = "default"
    extra_fields: dict = {}


class SeoRequest(BaseModel):
    primary_keyword: str = Field(min_length=1, max_length=120)
    topic: str = Field(min_length=1, max_length=180)
    content_brief: str = ""
    secondary_keywords: list[str] = []
    search_intent: str = "informational"
    allow_template: bool = False
    provider_id: str = ""


class MonitorRequest(BaseModel):
    enabled: bool
    interval_minutes: int = 60


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _project_body(project: Project) -> dict:
    settings = dict(DEFAULT_PROJECT_SETTINGS)
    settings.update(project.settings or {})
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "settings": settings,
        "created_at": _iso(project.created_at),
        "updated_at": _iso(project.updated_at),
    }


def _source_body(source: InputSource) -> dict:
    config = dict(source.config or {})
    config.pop("password", None)
    config.pop("extra_fields", None)
    return {
        "id": source.id,
        "kind": source.kind,
        "name": source.name,
        "config": config,
        "status": source.status,
        "status_detail": source.status_detail,
        "has_secret": bool(source.credential_id),
        "last_checked_at": _iso(source.last_checked_at),
    }


def _job_body(db: Session, job: Job) -> dict:
    steps = db.scalars(select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.position)).all()
    return {
        "id": job.id,
        "project_id": job.project_id,
        "run_id": job.run_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "current_step": job.current_step,
        "error_category": job.error_category,
        "error_detail": job.error_detail,
        "retry_count": job.retry_count,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "steps": [{"name": step.name, "status": step.status, "detail": step.detail} for step in steps],
        "actions": {
            "pause": job.status == "running",
            "resume": job.status == "paused",
            "cancel": job.status in {"queued", "running", "paused", "retrying"},
            "retry": job.status in {"failed", "blocked", "cancelled"},
        },
    }


def _storage_for(db: Session, organization_id: str) -> Storage:
    organization = db.get(Organization, organization_id)
    credential = None
    if organization and organization.storage_credential_id:
        credential = db.get(Credential, organization.storage_credential_id)
    return Storage(organization, credential)


@router.get("/projects")
def list_projects(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    rows = db.scalars(
        select(Project).where(Project.organization_id == membership.organization_id, Project.deleted_at.is_(None)).order_by(Project.created_at)
    ).all()
    return {"projects": [_project_body(row) for row in rows]}


@router.post("/projects", status_code=201)
def create_project(payload: ProjectRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = Project(
        organization_id=membership.organization_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        settings=dict(DEFAULT_PROJECT_SETTINGS),
        created_by=user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return _project_body(project)


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    sources = db.scalars(select(InputSource).where(InputSource.project_id == project.id)).all()
    return {**_project_body(project), "sources": [_source_body(source) for source in sources]}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, payload: ProjectRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    project.name = payload.name.strip()
    project.description = payload.description.strip()
    project.updated_at = utcnow()
    db.commit()
    return _project_body(project)


@router.put("/projects/{project_id}/settings")
def update_settings(project_id: str, payload: SettingsRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    project.settings = validate_settings(payload.settings)
    project.updated_at = utcnow()
    db.commit()
    return _project_body(project)


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    project = require_project(db, membership, project_id)
    storage = _storage_for(db, project.organization_id)
    keys = [source.storage_key for source in db.scalars(select(InputSource).where(InputSource.project_id == project.id))]
    keys += [row.storage_key for row in db.scalars(select(Screenshot).where(Screenshot.project_id == project.id))]
    keys += [row.storage_key for row in db.scalars(select(DocumentVersion).join(Document).where(Document.project_id == project.id))]
    for row in db.scalars(select(Video).where(Video.project_id == project.id)):
        keys.extend([row.storage_key, row.caption_key, row.thumbnail_key, row.script_key])
    for key in keys:
        if key:
            storage.delete(key)
    db.delete(project)
    db.commit()
    return {"deleted": True}


@router.post("/projects/{project_id}/sources", status_code=201)
def create_source(project_id: str, payload: SourceRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    if payload.kind not in {"website", "github", "git"}:
        raise AppError(422, "validation_error", "Use the upload endpoint for file sources.")
    config = _clean_source_config(payload.kind, payload.config)
    source = InputSource(project_id=project.id, kind=payload.kind, name=payload.name.strip(), config=config, status="untested")
    db.add(source)
    db.flush()
    extra = payload.config.get("extra_fields") if isinstance(payload.config.get("extra_fields"), dict) else {}
    if payload.password or extra:
        credential = store_secret(
            db,
            project.organization_id,
            project.id,
            "website_login",
            payload.name,
            login_payload(payload.password, extra),
        )
        source.credential_id = credential.id
        source.config = {**config, "extra_field_names": sorted(str(key) for key in extra)}
    db.commit()
    db.refresh(source)
    return _source_body(source)


@router.post("/projects/{project_id}/sources/upload", status_code=201)
def upload_source(
    project_id: str,
    request: Request,
    kind: str = Form(...),
    name: str = Form(...),
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    if kind not in UPLOAD_KINDS:
        raise AppError(422, "validation_error", "Unsupported upload kind.")
    data = upload.file.read()
    _validate_upload(kind, upload.filename or "upload.bin", data)
    storage = _storage_for(db, project.organization_id)
    key = f"{project.organization_id}/{project.id}/sources/{new_id()}"
    storage.put_bytes(key, data)
    source = InputSource(
        project_id=project.id,
        kind=kind,
        name=name.strip() or (upload.filename or kind),
        config={"original_filename": Path(upload.filename or "upload.bin").name[:200]},
        status="untested",
        storage_key=key,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_body(source)


@router.post("/sources/{source_id}/test")
def test_source(source_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    source = _owned_source(db, membership.organization_id, source_id)
    settings = get_settings()
    try:
        if source.kind == "website":
            from app.core.outbound import outbound_request

            url = validate_public_http_url(source.config["url"], allow_local=settings.allow_local_targets)
            response = outbound_request("GET", url, timeout=15.0, allow_local=settings.allow_local_targets)
            if response.status_code >= 400:
                raise AppError(422, "network_error", f"The website returned HTTP {response.status_code}.", retryable=True)
            source.status = "connected"
            source.status_detail = f"HTTP {response.status_code}."
        elif source.kind in {"github", "git"}:
            if source.config.get("url", "").startswith("http"):
                validate_public_http_url(source.config["url"], allow_local=settings.allow_local_targets)
            import tempfile

            with tempfile.TemporaryDirectory() as temporary:
                commit = clone_repository(source.config["url"], Path(temporary) / "repo", allow_local=settings.allow_local_targets)
            source.status = "connected"
            source.status_detail = f"Repository is reachable at {commit[:12]}." if commit else "Repository is reachable."
        else:
            storage = _storage_for(db, membership.organization_id)
            if not source.storage_key or not storage.exists(source.storage_key):
                raise AppError(422, "file_processing_error", "The uploaded file is not in storage.")
            source.status = "connected"
            source.status_detail = "Uploaded file is stored and readable."
    except AppError as exc:
        source.status = "failed"
        source.status_detail = exc.message
        source.last_checked_at = utcnow()
        db.commit()
        raise
    source.last_checked_at = utcnow()
    db.commit()
    return _source_body(source)


@router.delete("/sources/{source_id}")
def delete_source(source_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    source = _owned_source(db, membership.organization_id, source_id)
    if source.storage_key:
        _storage_for(db, membership.organization_id).delete(source.storage_key)
    db.delete(source)
    db.commit()
    return {"deleted": True}


@router.post("/projects/{project_id}/jobs", status_code=201)
def start_job(project_id: str, payload: JobRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    job = create_job(db, project, user, payload.job_type, request.headers.get("idempotency-key", ""))
    return _job_body(db, job)


@router.post("/projects/{project_id}/rescan", status_code=201)
def rescan(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    return start_job(project_id, JobRequest(job_type="rescan"), request, db)


@router.get("/projects/{project_id}/jobs")
def list_jobs(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    project = require_project(db, membership, project_id)
    rows = db.scalars(select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc())).all()
    return {"jobs": [_job_body(db, row) for row in rows]}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    job = _owned_job(db, membership.organization_id, job_id)
    return _job_body(db, job)


@router.post("/jobs/{job_id}/{action}")
def control_job(job_id: str, action: str, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    job = _owned_job(db, membership.organization_id, job_id)
    updated = set_control(db, job, action)
    return _job_body(db, updated)


@router.get("/projects/{project_id}/pages")
def pages(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(DiscoveredPage).where(DiscoveredPage.project_id == project.id).order_by(DiscoveredPage.discovered_at)).all()
    return {
        "pages": [
            {
                "id": row.id,
                "run_id": row.run_id,
                "url": row.url,
                "title": row.title,
                "access_status": row.access_status,
                "verification_status": row.verification_status,
                "discovered_at": _iso(row.discovered_at),
            }
            for row in rows
        ]
    }


@router.get("/projects/{project_id}/features")
def features(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(Feature).where(Feature.project_id == project.id)).all()
    return {
        "features": [
            {"id": row.id, "name": row.name, "description": row.description, "source": row.source, "references": row.references}
            for row in rows
        ]
    }


@router.get("/projects/{project_id}/workflows")
def workflows(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(Workflow).where(Workflow.project_id == project.id)).all()
    return {"workflows": [{"id": row.id, "name": row.name, "steps": row.steps, "verified": row.verified} for row in rows]}


@router.get("/projects/{project_id}/screenshots")
def screenshots(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(Screenshot).where(Screenshot.project_id == project.id)).all()
    return {
        "screenshots": [
            {"id": row.id, "page_id": row.page_id, "kind": row.kind, "sha256": row.sha256, "url": row.details.get("url", "")}
            for row in rows
        ]
    }


@router.get("/screenshots/{screenshot_id}/file")
def screenshot_file(screenshot_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    row = db.get(Screenshot, screenshot_id)
    project = db.get(Project, row.project_id) if row else None
    if row is None or project is None or project.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "Screenshot not found.")
    data = _storage_for(db, project.organization_id).get_bytes(row.storage_key)
    if not data.startswith(b"\x89PNG"):
        raise AppError(500, "file_processing_error", "Stored screenshot is not a PNG.")
    return Response(content=data, media_type="image/png")


@router.get("/projects/{project_id}/documents")
def documents(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(Document).where(Document.project_id == project.id)).all()
    payload = []
    for row in rows:
        versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == row.id).order_by(DocumentVersion.created_at)).all()
        payload.append(
            {
                "id": row.id,
                "doc_type": row.doc_type,
                "title": row.title,
                "versions": [
                    {
                        "id": version.id,
                        "run_id": version.run_id,
                        "format": version.format,
                        "verification_status": version.verification_status,
                        "sha256": version.content_sha256,
                        "created_at": _iso(version.created_at),
                    }
                    for version in versions
                ],
            }
        )
    return {"documents": payload}


@router.get("/document-versions/{version_id}/file")
def document_file(version_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    version = db.get(DocumentVersion, version_id)
    document = db.get(Document, version.document_id) if version else None
    project = db.get(Project, document.project_id) if document else None
    if version is None or project is None or project.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "Document not found.")
    data = _storage_for(db, project.organization_id).get_bytes(version.storage_key)
    media = {
        "markdown": "text/markdown",
        "html": "text/html",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }[version.format]
    return Response(content=data, media_type=media)


@router.get("/projects/{project_id}/videos")
def videos(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(Video).where(Video.project_id == project.id)).all()
    return {
        "videos": [
            {
                "id": row.id,
                "status": row.status,
                "duration_seconds": row.duration_seconds,
                "limitations": (row.details or {}).get("limitations", []),
            }
            for row in rows
        ]
    }


@router.get("/videos/{video_id}/file")
def video_file(video_id: str, request: Request, db: Session = Depends(get_db)) -> Response:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    row = db.get(Video, video_id)
    project = db.get(Project, row.project_id) if row else None
    if row is None or project is None or project.organization_id != membership.organization_id or not row.storage_key:
        raise AppError(404, "permission_error", "Video not found.")
    data = _storage_for(db, project.organization_id).get_bytes(row.storage_key)
    return Response(content=data, media_type="video/mp4")


@router.get("/projects/{project_id}/changes")
def changes(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    rows = db.scalars(select(ChangeRecord).where(ChangeRecord.project_id == project.id).order_by(ChangeRecord.created_at)).all()
    return {"changes": [{"id": row.id, "from_run_id": row.from_run_id, "to_run_id": row.to_run_id, "summary": row.summary} for row in rows]}


@router.get("/projects/{project_id}/runs/{run_id}")
def run_detail(project_id: str, run_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    run = db.get(AnalysisRun, run_id)
    if run is None or run.project_id != project.id:
        raise AppError(404, "permission_error", "Analysis run not found.")
    summary = dict(run.summary or {})
    code = dict(summary.get("code") or {})
    code.pop("file_hashes", None)
    return {
        "id": run.id,
        "source_version": run.source_version,
        "plan": summary.get("plan"),
        "warnings": summary.get("warnings", []),
        "changes": summary.get("changes"),
        "code": code,
        "created_at": _iso(run.created_at),
    }


@router.post("/sources/{source_id}/accounts", status_code=201)
def add_account(source_id: str, payload: AccountRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    source = _owned_source(db, membership.organization_id, source_id)
    if source.kind != "website":
        raise AppError(422, "validation_error", "Accounts can only be added to a website source.")
    accounts = list((source.config or {}).get("accounts") or [])
    if len(accounts) >= 10:
        raise AppError(422, "validation_error", "A website source can store up to 10 accounts.")
    project = db.get(Project, source.project_id)
    entry = {"username": payload.username.strip(), "role": payload.role.strip() or "default"}
    extra = payload.extra_fields if isinstance(payload.extra_fields, dict) else {}
    if payload.password or extra:
        credential = store_secret(
            db,
            project.organization_id,
            project.id,
            "website_login",
            payload.username,
            login_payload(payload.password, extra),
        )
        entry["credential_id"] = credential.id
        entry["extra_field_names"] = sorted(str(key) for key in extra)
    accounts.append(entry)
    source.config = {**source.config, "accounts": accounts}
    db.commit()
    db.refresh(source)
    return _source_body(source)


@router.delete("/sources/{source_id}/accounts/{index}")
def delete_account(source_id: str, index: int, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    source = _owned_source(db, membership.organization_id, source_id)
    accounts = list((source.config or {}).get("accounts") or [])
    if index < 0 or index >= len(accounts):
        raise AppError(404, "permission_error", "Account not found.")
    accounts.pop(index)
    source.config = {**source.config, "accounts": accounts}
    db.commit()
    return _source_body(source)


@router.put("/projects/{project_id}/monitor")
def save_monitor(project_id: str, payload: MonitorRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    project = require_project(db, membership, project_id)
    if payload.interval_minutes < 15 or payload.interval_minutes > 1440:
        raise AppError(422, "validation_error", "The monitoring interval must be between 15 and 1440 minutes.")
    row = db.scalar(select(MonitorSchedule).where(MonitorSchedule.project_id == project.id))
    if row is None:
        row = MonitorSchedule(organization_id=project.organization_id, project_id=project.id)
        db.add(row)
    row.enabled = payload.enabled
    row.interval_minutes = payload.interval_minutes
    row.next_run_at = utcnow() + timedelta(minutes=payload.interval_minutes) if payload.enabled else None
    row.last_error = ""
    if not payload.enabled:
        row.last_status = "disabled"
    db.commit()
    db.refresh(row)
    return _monitor_body(row)


@router.get("/projects/{project_id}/monitor")
def get_monitor(project_id: str, request: Request, db: Session = Depends(get_db)) -> dict:
    project, _membership = _project(request, db, project_id)
    row = db.scalar(select(MonitorSchedule).where(MonitorSchedule.project_id == project.id))
    if row is None:
        return {"enabled": False, "interval_minutes": 60, "next_run_at": None, "last_status": "", "last_error": ""}
    return _monitor_body(row)


@router.post("/projects/{project_id}/seo-articles", status_code=201)
def create_seo_article(project_id: str, payload: SeoRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    from app.api.deps import client_ip, rate_limit
    from app.core.security import decrypt_secret
    from app.documentation.seo import article_from_model, prompt_for, template_article
    from app.integrations.ai import client_for_row, generate_with_policy, load_fallback

    user = current_user(request, db)
    membership = active_membership(request, db, user)
    rate_limit(f"seo:{membership.organization_id}:{client_ip(request)}", 20, 60)
    project = require_project(db, membership, project_id)
    brief = payload.model_dump()
    pages = db.scalars(select(DiscoveredPage).where(DiscoveredPage.project_id == project.id)).all()
    links = sorted({page.url for page in pages})[:20]
    provider = None
    if payload.provider_id:
        provider = db.get(AIProvider, payload.provider_id)
        if provider is None or provider.organization_id != membership.organization_id:
            raise AppError(404, "permission_error", "AI provider not found.")
    elif not payload.allow_template:
        provider = db.scalar(
            select(AIProvider).where(
                AIProvider.organization_id == membership.organization_id,
                AIProvider.status == "connected",
            ).order_by(AIProvider.last_tested_at.desc())
        )
    if provider is None or provider.status != "connected" or not provider.credential_id:
        if not payload.allow_template:
            raise AppError(422, "configuration_error", "Connect an AI provider or explicitly allow template output. Template text is not AI-written.")
        article = template_article(brief)
    else:
        credential = db.get(Credential, provider.credential_id)
        primary = client_for_row(provider, decrypt_secret(credential.ciphertext), allow_local=get_settings().allow_local_targets)
        fallback_row = load_fallback(db, provider)
        fallback = None
        if fallback_row is not None and fallback_row.credential_id:
            other = db.get(Credential, fallback_row.credential_id)
            fallback = client_for_row(fallback_row, decrypt_secret(other.ciphertext), allow_local=get_settings().allow_local_targets)
        result = generate_with_policy(primary, prompt_for(brief, links), allow_paid_fallback=provider.allow_paid_fallback, fallback=fallback)
        article = article_from_model(result, brief)
    document = Document(project_id=project.id, doc_type="seo_article", title=article["title"])
    db.add(document)
    db.flush()
    from app.documentation.render import render_markdown, sha256

    body = render_markdown(
        {
            "title": article["title"],
            "verification_status": "needs_review",
            "sections": [
                {"heading": key, "body": json.dumps(value) if isinstance(value, (list, dict)) else str(value), "fact_type": article["fact_type"]}
                for key, value in article.items()
                if key not in {"title", "fact_type"}
            ],
        }
    )
    storage = _storage_for(db, project.organization_id)
    key = f"{project.organization_id}/{project.id}/documents/{document.id}.md"
    data = body.encode("utf-8")
    storage.put_bytes(key, data, "text/markdown")
    version = DocumentVersion(
        document_id=document.id,
        format="markdown",
        storage_key=key,
        content_sha256=sha256(data),
        verification_status="needs_review",
        fact_summary={"generation_mode": article["generation_mode"], "provider": article["provider"], "model": article["model"]},
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return {"document_id": document.id, "version_id": version.id, "article": article}


def _monitor_body(row: MonitorSchedule) -> dict:
    return {
        "id": row.id,
        "enabled": row.enabled,
        "interval_minutes": row.interval_minutes,
        "next_run_at": _iso(row.next_run_at),
        "last_run_at": _iso(row.last_run_at),
        "last_status": row.last_status,
        "last_error": row.last_error,
    }


def _project(request: Request, db: Session, project_id: str):
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    return require_project(db, membership, project_id), membership


def _owned_source(db: Session, organization_id: str, source_id: str) -> InputSource:
    source = db.get(InputSource, source_id)
    project = db.get(Project, source.project_id) if source else None
    if source is None or project is None or project.organization_id != organization_id:
        raise AppError(404, "permission_error", "Input source not found.")
    return source


def _owned_job(db: Session, organization_id: str, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None or job.organization_id != organization_id:
        raise AppError(404, "permission_error", "Job not found.")
    return job


def _clean_source_config(kind: str, config: dict) -> dict:
    settings = get_settings()
    cleaned = {key: value for key, value in config.items() if key not in {"password", "extra_fields"}}
    if kind == "website":
        url = str(cleaned.get("url") or "")
        validate_public_http_url(url, allow_local=settings.allow_local_targets)
        cleaned["url"] = url
        if cleaned.get("login_url"):
            cleaned["login_url"] = validate_public_http_url(str(cleaned["login_url"]), allow_local=settings.allow_local_targets)
        host = (urlparse(url).hostname or "").lower()
        allowed = [item.lower() for item in cleaned.get("allowed_domains", []) if isinstance(item, str)]
        if allowed and host not in allowed:
            raise AppError(422, "validation_error", "The website host must be included in the allowed domains.")
    if kind in {"github", "git"}:
        url = str(cleaned.get("url") or "")
        if url.startswith("http://") or url.startswith("https://"):
            validate_public_http_url(url, allow_local=settings.allow_local_targets)
        elif not (settings.allow_local_targets and Path(url).is_dir()):
            raise AppError(422, "validation_error", "Repository URLs must be http(s).")
        cleaned["url"] = url
    return cleaned


def _validate_upload(kind: str, filename: str, data: bytes) -> None:
    settings = get_settings()
    if len(data) > settings.max_upload_bytes:
        raise AppError(413, "file_processing_error", "The file exceeds the configured upload limit.")
    if not data:
        raise AppError(422, "file_processing_error", "The file is empty.")
    suffix = Path(filename).suffix.lower()
    if kind in {"zip", "wp_plugin", "wp_theme"}:
        if suffix != ".zip" or not data.startswith(b"PK"):
            raise AppError(422, "file_processing_error", "Plugin, theme, and source uploads must be ZIP files.")
        return
    if kind == "screenshots" and not (data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8")):
        raise AppError(422, "file_processing_error", "Screenshot uploads must be PNG or JPEG.")
    if kind == "recording" and b"ftyp" not in data[:32] and not data.startswith(b"\x1aE\xdf\xa3"):
        raise AppError(422, "file_processing_error", "Recordings must be MP4 or WebM.")
    if kind in {"documentation", "api_spec"}:
        if b"\x00" in data[:200]:
            raise AppError(422, "file_processing_error", "This upload must be a text specification or document.")
        try:
            data[:2000].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AppError(422, "file_processing_error", "This upload must be UTF-8 text.") from exc
