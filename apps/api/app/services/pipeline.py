"""Analysis pipeline. Progress comes from finished steps, and old documents are kept."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analysis.source import analyze_tree, clone_repository, directory_size, remove_tree, safe_extract_zip
from app.browser.discovery import discover_site
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import job_id_var, project_id_var
from app.core.security import decrypt_secret
from app.db.session import SessionLocal
from app.documentation.render import (
    build_documents,
    render_docx,
    render_html,
    render_markdown,
    render_pdf,
    sha256,
    validate_output,
)
from app.integrations.ai import AIClient, deterministic_plan
from app.integrations.storage import Storage
from app.models import (
    AIProvider,
    AnalysisRun,
    ChangeRecord,
    Checkpoint,
    Credential,
    DiscoveredPage,
    Document,
    DocumentVersion,
    Feature,
    InputSource,
    Job,
    JobStep,
    Organization,
    Project,
    Repository,
    Requirement,
    Screenshot,
    Video,
    Workflow,
    AcceptanceCriterion,
)
from app.models.entities import utcnow
from app.verification.engine import record_execution
from app.video.render import render_slideshow

STEPS = ["validate", "discover", "analyze_code", "features", "changes", "documents", "video", "finalize"]
CODE_KINDS = {"zip", "wp_plugin", "wp_theme", "github", "git"}


def execute_job(job_id: str) -> None:
    db = SessionLocal()
    token_job = job_id_var.set(job_id)
    try:
        try:
            _execute(db, job_id)
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if job is not None and job.status not in {"completed", "completed_with_warnings", "cancelled"}:
                job.status = "failed"
                job.error_category = "job_execution_error"
                job.error_detail = f"The job failed ({exc.__class__.__name__})."
                job.finished_at = utcnow()
                job.updated_at = utcnow()
                db.commit()
            if get_settings().app_env == "test":
                raise
    finally:
        job_id_var.reset(token_job)
        db.close()


def _execute(db: Session, job_id: str) -> None:
    job = db.get(Job, job_id)
    if job is None or job.status in {"cancelled", "completed", "completed_with_warnings"}:
        return
    project_token = project_id_var.set(job.project_id)
    try:
        job.status = "running"
        job.started_at = job.started_at or utcnow()
        job.error_detail = ""
        job.error_category = ""
        db.commit()
        run = _ensure_run(db, job)
        completed = {
            item.step_name
            for item in db.scalars(select(Checkpoint).where(Checkpoint.job_id == job.id)).all()
        }
        warnings: list[str] = []
        for name in STEPS:
            db.refresh(job)
            if job.cancel_requested:
                _finish(db, job, "cancelled", "Cancelled by the user.")
                return
            if job.pause_requested:
                _finish(db, job, "paused", "Paused by the user.")
                return
            if name in completed:
                continue
            step = _start_step(db, job, name)
            try:
                warning = _run_step(db, job, run, name)
            except AppError as exc:
                step.status = "failed"
                step.detail = exc.message
                step.finished_at = utcnow()
                job.status = "blocked" if exc.category == "browser_automation_error" and "waiting" in exc.message else "failed"
                if "waiting" in exc.message or "CAPTCHA" in exc.message or "one-time" in exc.message:
                    job.status = "blocked"
                job.error_category = exc.category
                job.error_detail = exc.message
                job.finished_at = utcnow()
                job.updated_at = utcnow()
                db.commit()
                return
            if warning == "__blocked__":
                step.status = "blocked"
                step.finished_at = utcnow()
                job.status = "blocked"
                job.finished_at = utcnow()
                db.commit()
                return
            step.status = "completed"
            step.detail = warning or ""
            step.finished_at = utcnow()
            if warning:
                warnings.append(warning)
            db.add(Checkpoint(job_id=job.id, step_name=name, payload={"warning": warning or ""}))
            completed.add(name)
            job.progress = int(len(completed) / len(STEPS) * 100)
            job.current_step = name
            job.updated_at = utcnow()
            db.commit()
        status = "completed_with_warnings" if warnings else "completed"
        _finish(db, job, status, "; ".join(warnings))
    finally:
        project_id_var.reset(project_token)


def _finish(db: Session, job: Job, status: str, detail: str) -> None:
    job.status = status
    job.finished_at = utcnow() if status != "paused" else None
    job.updated_at = utcnow()
    if detail and status in {"failed", "blocked", "cancelled", "completed_with_warnings"}:
        job.error_detail = detail
    db.commit()


def _ensure_run(db: Session, job: Job) -> AnalysisRun:
    if job.run_id:
        run = db.get(AnalysisRun, job.run_id)
        if run is not None:
            return run
    run = AnalysisRun(project_id=job.project_id, job_id=job.id, summary={"warnings": []})
    db.add(run)
    db.flush()
    job.run_id = run.id
    db.commit()
    return run


def _start_step(db: Session, job: Job, name: str) -> JobStep:
    step = JobStep(job_id=job.id, position=STEPS.index(name), name=name, status="running", started_at=utcnow())
    db.add(step)
    job.current_step = name
    job.status = "running"
    db.commit()
    db.refresh(step)
    return step


def _run_step(db: Session, job: Job, run: AnalysisRun, name: str) -> str:
    if name == "validate":
        return _validate(db, job, run)
    if name == "discover":
        return _discover(db, job, run)
    if name == "analyze_code":
        return _analyze(db, job, run)
    if name == "features":
        return _features(db, job, run)
    if name == "changes":
        return _changes(db, job, run)
    if name == "documents":
        return _documents(db, job, run)
    if name == "video":
        return _video(db, job, run)
    if name == "finalize":
        summary = dict(run.summary or {})
        summary["finished_at"] = utcnow().isoformat()
        run.summary = summary
        db.commit()
        return ""
    return ""


def _login_secret(ciphertext: str) -> tuple[str, dict]:
    raw = decrypt_secret(ciphertext)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, {}
    if isinstance(payload, dict) and "password" in payload:
        fields = payload.get("extra_fields") if isinstance(payload.get("extra_fields"), dict) else {}
        return str(payload.get("password") or ""), fields
    return raw, {}


def _project(db: Session, job: Job) -> tuple[Project, Organization]:
    project = db.get(Project, job.project_id)
    organization = db.get(Organization, job.organization_id)
    if project is None or organization is None:
        raise AppError(404, "job_execution_error", "Project no longer exists.")
    return project, organization


def _storage(db: Session, organization: Organization) -> Storage:
    credential = db.get(Credential, organization.storage_credential_id) if organization.storage_credential_id else None
    return Storage(organization, credential)


def _validate(db: Session, job: Job, run: AnalysisRun) -> str:
    project, _organization = _project(db, job)
    sources = list(db.scalars(select(InputSource).where(InputSource.project_id == project.id)).all())
    if not sources:
        raise AppError(422, "validation_error", "Add a website or source archive before starting analysis.")
    settings = get_settings()
    provider = db.scalar(select(AIProvider).where(AIProvider.organization_id == project.organization_id))
    plan_source = deterministic_plan(
        any(source.kind == "website" for source in sources),
        any(source.kind in CODE_KINDS for source in sources),
        bool((project.settings or {}).get("enable_video")),
    )
    warning = ""
    if provider and provider.status == "connected" and provider.credential_id:
        credential = db.get(Credential, provider.credential_id)
        if credential is not None:
            client = AIClient(
                provider.provider,
                decrypt_secret(credential.ciphertext),
                provider.endpoint,
                provider.model,
                allow_local=settings.allow_local_targets,
            )
            try:
                plan_source = client.refine_plan([step["tool"] for step in plan_source["steps"]], project.name)
            except AppError as exc:
                warning = f"AI plan was not used: {exc.message}"
    summary = dict(run.summary or {})
    summary["plan"] = plan_source
    summary["warnings"] = list(summary.get("warnings", []))
    run.summary = summary
    run.source_version = hashlib.sha256(",".join(sorted(source.id for source in sources)).encode()).hexdigest()[:16]
    db.commit()
    return warning


def _discover(db: Session, job: Job, run: AnalysisRun) -> str:
    project, organization = _project(db, job)
    settings = get_settings()
    storage = _storage(db, organization)
    if (project.settings or {}).get("analysis_mode") == "code_only":
        return ""
    sources = list(
        db.scalars(select(InputSource).where(InputSource.project_id == project.id, InputSource.kind == "website")).all()
    )
    if not sources:
        return ""
    limits = project.settings or {}
    summary = dict(run.summary or {})
    pages_out: list[dict] = []
    blocked = ""
    for source in sources:
        accounts = source.config.get("accounts") or [
            {
                "username": source.config.get("username", ""),
                "role": source.config.get("role", "default"),
                "credential_id": source.credential_id,
            }
        ]
        for account in accounts[:10]:
            password = ""
            extra_fields = dict(source.config.get("extra_fields") or {})
            credential_id = account.get("credential_id") or source.credential_id
            if credential_id:
                credential = db.get(Credential, credential_id)
                if credential is not None:
                    password, stored_fields = _login_secret(credential.ciphertext)
                    extra_fields.update(stored_fields)
            captured: list[tuple[bytes, str]] = []

            def on_screenshot(data: bytes, url: str, kind: str, _captured=captured) -> None:
                _captured.append((data, url))

            result = discover_site(
                start_url=source.config["url"],
                login_url=source.config.get("login_url", ""),
                username=account.get("username", ""),
                password=password,
                extra_fields=extra_fields,
                allowed_hosts=limits.get("allowed_domains") or [],
                max_pages=int(limits.get("max_pages") or 10),
                max_depth=int(limits.get("max_depth") or 2),
                max_seconds=int(limits.get("max_seconds") or 90),
                allow_local=settings.allow_local_targets,
                on_screenshot=on_screenshot,
            )
            page_rows: dict[str, DiscoveredPage] = {}
            for page in result["pages"]:
                pages_out.append({**page, "role": account.get("role", "default"), "source_id": source.id})
                row = DiscoveredPage(
                    project_id=project.id,
                    run_id=run.id,
                    url=page["url"],
                    title=page.get("title") or "",
                    access_status=page.get("access_status") or "unknown",
                    depth=page.get("depth") or 0,
                    details={**page, "role": account.get("role", "default")},
                    verification_status="partially_verified" if page.get("access_status") == "accessible" else "blocked",
                )
                db.add(row)
                db.flush()
                page_rows[page["url"]] = row
            for data, url in captured:
                if not data.startswith(b"\x89PNG"):
                    raise AppError(500, "browser_automation_error", "Screenshot capture did not produce a PNG.")
                digest = sha256(data)
                key = f"{organization.id}/{project.id}/{run.id}/screenshots/{digest}.png"
                storage.put_bytes(key, data, "image/png")
                db.add(
                    Screenshot(
                        project_id=project.id,
                        run_id=run.id,
                        page_id=page_rows[url].id if url in page_rows else None,
                        job_id=job.id,
                        kind="viewport",
                        storage_key=key,
                        sha256=digest,
                        details={"url": url, "role": account.get("role", "default")},
                    )
                )
            source.status = "connected" if not result.get("blocked") else "blocked"
            source.status_detail = result.get("blocked") or "Discovery completed."
            source.last_checked_at = utcnow()
            if result.get("blocked"):
                blocked = result["blocked"]
                break
        if blocked:
            break
    summary["pages"] = pages_out
    summary["console_errors"] = []
    for page in pages_out:
        summary["console_errors"].extend(page.get("console_errors") or [])
    run.summary = summary
    db.commit()
    if blocked:
        job.error_detail = blocked
        job.error_category = "browser_automation_error"
        return "__blocked__"
    return ""


def _analyze(db: Session, job: Job, run: AnalysisRun) -> str:
    project, organization = _project(db, job)
    settings = get_settings()
    storage = _storage(db, organization)
    sources = list(db.scalars(select(InputSource).where(InputSource.project_id == project.id)).all())
    if (project.settings or {}).get("analysis_mode") == "website_only":
        return ""
    code_sources = [source for source in sources if source.kind in CODE_KINDS]
    if not code_sources:
        return ""
    combined: dict = {"languages": {}, "frameworks": [], "routes": [], "entry_points": [], "file_hashes": {}, "commits": [], "executed": False}
    for source in code_sources:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            root.mkdir()
            commit = ""
            if source.kind in {"github", "git"}:
                url = source.config.get("url") or ""
                commit = clone_repository(url, root, allow_local=settings.allow_local_targets)
            else:
                if not source.storage_key:
                    raise AppError(422, "file_processing_error", f"{source.name} has no uploaded archive.")
                blob = storage.get_bytes(source.storage_key)
                archive = Path(temporary) / "upload.zip"
                archive.write_bytes(blob)
                safe_extract_zip(archive, root)
            if directory_size(root) > settings.max_archive_uncompressed_bytes:
                raise AppError(400, "file_processing_error", "Source tree exceeds the size limit.")
            report = analyze_tree(root)
            for path in root.rglob("*"):
                if path.is_file() and ".git" not in path.parts:
                    relative = path.relative_to(root).as_posix()
                    combined["file_hashes"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
            remove_tree(root)
        for language, count in report["languages"].items():
            combined["languages"][language] = combined["languages"].get(language, 0) + count
        combined["frameworks"].extend(report["frameworks"])
        combined["routes"].extend(report["routes"])
        combined["entry_points"].extend(report["entry_points"])
        combined["package_metadata"] = report.get("package_metadata") or {}
        if commit:
            combined["commits"].append(commit)
        db.add(
            Repository(
                project_id=project.id,
                source_id=source.id,
                url=source.config.get("url", ""),
                commit_sha=commit,
                storage_key=source.storage_key or "",
            )
        )
        source.status = "connected"
        source.status_detail = "Static analysis completed. Source code was not executed."
        source.last_checked_at = utcnow()
    combined["frameworks"] = sorted(set(combined["frameworks"]))
    summary = dict(run.summary or {})
    summary["code"] = combined
    run.summary = summary
    run.source_version = hashlib.sha256(str(sorted(combined["file_hashes"].items())).encode()).hexdigest()[:16]
    db.commit()
    return ""


def _features(db: Session, job: Job, run: AnalysisRun) -> str:
    project, _organization = _project(db, job)
    summary = dict(run.summary or {})
    features: list[dict] = []
    for page in summary.get("pages", []):
        if page.get("access_status") != "accessible":
            continue
        for heading in page.get("headings") or []:
            features.append(
                {
                    "name": heading[:300],
                    "description": f"Heading observed on {page.get('url')}.",
                    "source": "observed",
                    "references": {"url": page.get("url"), "fact_type": "observed"},
                }
            )
        if page.get("forms"):
            features.append(
                {
                    "name": f"Form on {page.get('title') or page.get('url')}",
                    "description": "A form was present when the page was captured.",
                    "source": "observed",
                    "references": {"url": page.get("url"), "fact_type": "observed"},
                }
            )
    for route in (summary.get("code") or {}).get("routes", []):
        features.append(
            {
                "name": f"{route.get('method', 'route')} {route.get('path')}",
                "description": "Identified by static inspection.",
                "source": "source_code",
                "references": route,
            }
        )
    stored = []
    for feature in features[:40]:
        row = Feature(
            project_id=project.id,
            run_id=run.id,
            name=feature["name"][:300],
            description=feature["description"],
            source=feature["source"],
            references=feature["references"],
        )
        db.add(row)
        stored.append(feature)
    if summary.get("pages"):
        db.add(
            Workflow(
                project_id=project.id,
                run_id=run.id,
                name="Observed navigation",
                steps=[{"url": page.get("url"), "title": page.get("title")} for page in summary.get("pages", [])],
                verified=any(page.get("access_status") == "accessible" for page in summary.get("pages", [])),
            )
        )
    summary["features"] = stored
    run.summary = summary
    db.commit()
    _sync_feature_requirements(db, project, run, stored)
    return ""


def _sync_feature_requirements(db: Session, project: Project, run: AnalysisRun, features: list[dict]) -> None:
    for index, feature in enumerate(features[:20]):
        key = f"OBS-{run.id[:8]}-{index + 1}"
        requirement = db.scalar(select(Requirement).where(Requirement.requirement_key == key))
        if requirement is None:
            requirement = Requirement(
                organization_id=project.organization_id,
                project_id=project.id,
                requirement_key=key,
                title=feature["name"][:300],
                description=feature["description"],
                category="discovered_feature",
                scope="project",
                status="not_started",
            )
            db.add(requirement)
            db.flush()
            db.add(
                AcceptanceCriterion(
                    requirement_id=requirement.id,
                    criterion_key=f"{key}-AC-01",
                    description="The feature has an observed page or a source reference with a file and line.",
                    verification_method="automated_test",
                    requires_evidence=True,
                )
            )
            db.flush()
        references = feature.get("references") or {}
        has_ref = bool(references.get("url") or references.get("file"))
        if has_ref and references.get("file") and not references.get("line_start"):
            has_ref = False
        record_execution(
            db,
            requirement_key=key,
            criterion_key=f"{key}-AC-01",
            result="passed" if has_ref else "failed",
            actual_result="Reference stored." if has_ref else "No source or page reference was stored.",
            environment=f"analysis-run:{run.id}",
            evidence_summary=feature["description"],
            evidence_details=references,
            organization_id=project.organization_id,
            project_id=project.id,
        )
    db.commit()


def _changes(db: Session, job: Job, run: AnalysisRun) -> str:
    project, _organization = _project(db, job)
    previous = db.scalar(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id, AnalysisRun.id != run.id)
        .order_by(AnalysisRun.created_at.desc())
    )
    summary = dict(run.summary or {})
    current_hashes = (summary.get("code") or {}).get("file_hashes", {})
    current_pages = sorted({page.get("url") for page in summary.get("pages", []) if page.get("url")})
    if previous is None:
        summary["changes"] = {"baseline": True}
        run.summary = summary
        db.commit()
        return ""
    previous_summary = previous.summary or {}
    previous_hashes = (previous_summary.get("code") or {}).get("file_hashes", {})
    previous_pages = sorted({page.get("url") for page in previous_summary.get("pages", []) if page.get("url")})
    changed = sorted(path for path, digest in current_hashes.items() if previous_hashes.get(path) not in {None, digest})
    added = sorted(set(current_hashes) - set(previous_hashes))
    removed = sorted(set(previous_hashes) - set(current_hashes))
    pages_added = sorted(set(current_pages) - set(previous_pages))
    pages_removed = sorted(set(previous_pages) - set(current_pages))
    report = {
        "changed_files": changed,
        "added_files": added,
        "removed_files": removed,
        "pages_added": pages_added,
        "pages_removed": pages_removed,
    }
    db.add(ChangeRecord(project_id=project.id, from_run_id=previous.id, to_run_id=run.id, summary=report))
    if any(report.values()):
        versions = list(
            db.scalars(
                select(DocumentVersion).join(Document).where(Document.project_id == project.id, DocumentVersion.run_id == previous.id)
            ).all()
        )
        for version in versions:
            version.verification_status = "needs_review"
    summary["changes"] = report
    run.summary = summary
    db.commit()
    return ""


def _documents(db: Session, job: Job, run: AnalysisRun) -> str:
    project, organization = _project(db, job)
    storage = _storage(db, organization)
    summary = dict(run.summary or {})
    requested = (project.settings or {}).get("output_formats") or ["markdown", "html", "pdf", "docx"]
    doc_types = (project.settings or {}).get("doc_types") or ["getting_started", "feature_guide", "technical"]
    documents = build_documents(project.name, summary, doc_types)
    summary["ai_generation"] = _ai_generation_record(db, project)
    run.summary = summary
    renderers = {
        "markdown": lambda document: render_markdown(document).encode("utf-8"),
        "html": lambda document: render_html(document).encode("utf-8"),
        "pdf": render_pdf,
        "docx": render_docx,
    }
    content_types = {
        "markdown": "text/markdown",
        "html": "text/html",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    extensions = {"markdown": "md", "html": "html", "pdf": "pdf", "docx": "docx"}
    for document in documents:
        row = Document(project_id=project.id, doc_type=document["doc_type"], title=document["title"])
        db.add(row)
        db.flush()
        for fmt in requested:
            if fmt not in renderers:
                continue
            data = renderers[fmt](document)
            validate_output(fmt, data, document["title"])
            key = f"{organization.id}/{project.id}/{run.id}/documents/{row.id}.{extensions[fmt]}"
            digest = storage.put_bytes(key, data, content_types[fmt])
            db.add(
                DocumentVersion(
                    document_id=row.id,
                    run_id=run.id,
                    format=fmt,
                    storage_key=key,
                    content_sha256=digest,
                    verification_status=document["verification_status"],
                    fact_summary={"sections": [item["fact_type"] for item in document["sections"]]},
                )
            )
    db.commit()
    return ""


def _video(db: Session, job: Job, run: AnalysisRun) -> str:
    project, organization = _project(db, job)
    if not (project.settings or {}).get("enable_video"):
        return ""
    storage = _storage(db, organization)
    shots = list(db.scalars(select(Screenshot).where(Screenshot.run_id == run.id)).all())
    frames = []
    for shot in shots[:8]:
        frames.append((shot.details.get("url", "frame"), storage.get_bytes(shot.storage_key)))
    summary = dict(run.summary or {})
    script_lines = ["Observed workflow"]
    for page in summary.get("pages", []):
        if page.get("title"):
            script_lines.append(f"Opened {page['title']}")
    recording = _recording_bytes(db, project, storage)
    voice = _speech(db, project, "\n".join(script_lines)) if (project.settings or {}).get("enable_voiceover") else {"audio": None, "limitation": "", "voiceover": False}
    rendered = render_slideshow(
        frames,
        "\n".join(script_lines),
        voiceover_requested=bool((project.settings or {}).get("enable_voiceover")),
        audio=voice.get("audio"),
        source_mode="uploaded_recording" if recording else "screenshot_slideshow",
        recording=recording,
    )
    if voice.get("limitation"):
        rendered["limitations"].append(voice["limitation"])
    prefix = f"{organization.id}/{project.id}/{run.id}/video"
    video_key = prefix + "/tutorial.mp4"
    caption_key = prefix + "/captions.srt"
    thumb_key = prefix + "/thumbnail.png"
    script_key = prefix + "/script.txt"
    storage.put_bytes(video_key, rendered["video"], "video/mp4")
    storage.put_bytes(caption_key, rendered["captions"], "application/x-subrip")
    storage.put_bytes(thumb_key, rendered["thumbnail"], "image/png")
    storage.put_bytes(script_key, rendered["script"], "text/plain")
    workflow = db.scalar(select(Workflow).where(Workflow.run_id == run.id))
    db.add(
        Video(
            project_id=project.id,
            run_id=run.id,
            workflow_id=workflow.id if workflow else None,
            status="completed_with_warnings" if rendered["limitations"] else "completed",
            storage_key=video_key,
            caption_key=caption_key,
            thumbnail_key=thumb_key,
            script_key=script_key,
            duration_seconds=rendered["duration"],
            details=rendered["metadata"] | {"limitations": rendered["limitations"]},
        )
    )
    db.commit()
    return "; ".join(rendered["limitations"])


def _ai_generation_record(db: Session, project: Project) -> dict:
    settings = project.settings or {}
    if not settings.get("use_ai_documentation"):
        return {"mode": "template_fallback", "reason": "AI documentation is off for this project. The documents are template output from observed facts."}
    provider = None
    if settings.get("ai_provider_id"):
        provider = db.get(AIProvider, settings["ai_provider_id"])
    if provider is None or provider.organization_id != project.organization_id or provider.status != "connected":
        return {"mode": "template_fallback", "reason": "No connected AI provider is selected. Template documents were kept."}
    return {
        "mode": "template_fallback",
        "provider": provider.provider,
        "model": provider.model,
        "reason": "The selected provider is recorded. Document bodies stay tied to observed facts unless an SEO article is requested.",
    }


def _recording_bytes(db: Session, project: Project, storage: Storage) -> bytes | None:
    source = db.scalar(select(InputSource).where(InputSource.project_id == project.id, InputSource.kind == "recording"))
    if source is None or not source.storage_key:
        return None
    return storage.get_bytes(source.storage_key)


def _speech(db: Session, project: Project, script: str) -> dict:
    from app.integrations.tts import synthesize

    settings = project.settings or {}
    provider_name = settings.get("tts_provider") or ""
    provider = None
    if settings.get("ai_provider_id"):
        provider = db.get(AIProvider, settings["ai_provider_id"])
    if provider is None or provider.organization_id != project.organization_id:
        provider = db.scalar(
            select(AIProvider).where(AIProvider.organization_id == project.organization_id, AIProvider.provider == provider_name, AIProvider.status == "connected")
        )
    if provider is None or not provider.credential_id:
        return {"audio": None, "limitation": "No text-to-speech provider is configured. The video stays silent and is not marked as voice-enabled."}
    credential = db.get(Credential, provider.credential_id)
    if credential is None:
        return {"audio": None, "limitation": "The speech provider has no saved API key. No narration audio was created."}
    return synthesize(
        provider=provider_name or provider.provider,
        api_key=decrypt_secret(credential.ciphertext),
        endpoint=provider.endpoint,
        model=settings.get("tts_model") or "tts-1",
        voice=settings.get("tts_voice") or "alloy",
        text=script,
        allow_local=get_settings().allow_local_targets,
    )
