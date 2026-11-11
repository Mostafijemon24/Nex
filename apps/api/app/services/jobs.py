"""Create and control analysis jobs."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Checkpoint, Job, Project, User
from app.models.entities import utcnow

DEFAULT_PROJECT_SETTINGS = {
    "analysis_mode": "standard",
    "documentation_language": "en",
    "target_audience": "end_users",
    "allowed_domains": [],
    "doc_types": ["getting_started", "feature_guide", "technical"],
    "output_formats": ["markdown", "html", "pdf", "docx"],
    "safety_level": "safe",
    "max_pages": 10,
    "max_depth": 2,
    "max_seconds": 90,
    "max_steps": 50,
    "max_retries": 2,
    "enable_video": False,
    "enable_voiceover": False,
    "use_ai_documentation": False,
    "ai_provider_id": "",
    "tts_provider": "",
    "tts_model": "tts-1",
    "tts_voice": "alloy",
}

DOC_TYPES = {
    "user_manual",
    "getting_started",
    "feature_guide",
    "tutorial",
    "technical",
    "architecture",
    "api_reference",
    "database",
    "installation",
    "configuration",
    "troubleshooting",
    "faq",
    "developer",
    "wordpress_plugin",
    "wordpress_theme",
    "integration",
    "release_notes",
}
FORMATS = {"markdown", "html", "pdf", "docx"}
JOB_TYPES = {"analysis", "rescan"}


def create_job(db: Session, project: Project, user: User, job_type: str, idempotency_key: str = "") -> Job:
    if job_type not in JOB_TYPES:
        raise AppError(422, "validation_error", "Unsupported job type.")
    if idempotency_key:
        existing = db.scalar(
            select(Job).where(Job.project_id == project.id, Job.idempotency_key == idempotency_key, Job.job_type == job_type)
        )
        if existing is not None:
            return existing
    active = db.scalar(
        select(Job).where(Job.project_id == project.id, Job.status.in_(["queued", "running", "retrying"]))
    )
    if active is not None:
        raise AppError(409, "job_execution_error", "This project already has a job that has not finished.")
    job = Job(
        organization_id=project.organization_id,
        project_id=project.id,
        job_type=job_type,
        status="queued",
        idempotency_key=idempotency_key,
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    from app.workers.tasks import dispatch

    task_id = dispatch(job.id)
    db.refresh(job)
    job.celery_task_id = task_id or ""
    job.updated_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def set_control(db: Session, job: Job, action: str) -> Job:
    if action == "pause":
        if job.status != "running":
            raise AppError(409, "job_execution_error", "Only a running job can be paused.")
        job.pause_requested = True
    elif action == "resume":
        if job.status != "paused":
            raise AppError(409, "job_execution_error", "Only a paused job can be resumed.")
        job.pause_requested = False
        job.status = "queued"
        db.commit()
        from app.workers.tasks import dispatch

        dispatch(job.id)
        db.refresh(job)
        return job
    elif action == "cancel":
        if job.status not in {"queued", "running", "paused", "retrying"}:
            raise AppError(409, "job_execution_error", "This job can no longer be cancelled.")
        job.cancel_requested = True
        if job.status in {"queued", "paused"}:
            job.status = "cancelled"
            job.finished_at = utcnow()
    elif action == "retry":
        if job.status not in {"failed", "blocked", "cancelled"}:
            raise AppError(409, "job_execution_error", "Only a failed, blocked, or cancelled job can be retried.")
        for checkpoint in db.scalars(select(Checkpoint).where(Checkpoint.job_id == job.id)).all():
            db.delete(checkpoint)
        job.retry_count += 1
        job.status = "queued"
        job.run_id = None
        job.progress = 0
        job.pause_requested = False
        job.cancel_requested = False
        job.error_category = ""
        job.error_detail = ""
        job.finished_at = None
        job.current_step = ""
        db.commit()
        from app.workers.tasks import dispatch

        dispatch(job.id)
        db.refresh(job)
        return job
    else:
        raise AppError(422, "validation_error", "Unknown job action.")
    job.updated_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def validate_settings(incoming: dict) -> dict:
    cleaned = dict(DEFAULT_PROJECT_SETTINGS)
    unknown = set(incoming) - set(DEFAULT_PROJECT_SETTINGS)
    if unknown:
        raise AppError(422, "validation_error", "Remove unsupported analysis settings: " + ", ".join(sorted(unknown)))
    cleaned.update(incoming)
    if cleaned["safety_level"] not in {"safe", "recorded"}:
        raise AppError(422, "validation_error", "Safety level must be safe or recorded.")
    if cleaned["analysis_mode"] not in {"standard", "code_only", "website_only"}:
        raise AppError(422, "validation_error", "Analysis mode must be standard, code_only, or website_only.")
    if not isinstance(cleaned["max_pages"], int) or not 1 <= cleaned["max_pages"] <= 30:
        raise AppError(422, "validation_error", "Maximum pages must be between 1 and 30.")
    if not isinstance(cleaned["max_depth"], int) or not 0 <= cleaned["max_depth"] <= 3:
        raise AppError(422, "validation_error", "Maximum depth must be between 0 and 3.")
    if not isinstance(cleaned["max_seconds"], int) or not 10 <= cleaned["max_seconds"] <= 180:
        raise AppError(422, "validation_error", "Maximum execution time must be between 10 and 180 seconds.")
    if not isinstance(cleaned["allowed_domains"], list) or not all(isinstance(item, str) for item in cleaned["allowed_domains"]):
        raise AppError(422, "validation_error", "Allowed domains must be a list of host names.")
    if any(item not in DOC_TYPES for item in cleaned["doc_types"]):
        raise AppError(422, "validation_error", "One or more documentation types are not supported.")
    if any(item not in FORMATS for item in cleaned["output_formats"]):
        raise AppError(422, "validation_error", "Output formats must be markdown, html, pdf, or docx.")
    if not cleaned["output_formats"]:
        raise AppError(422, "validation_error", "Choose at least one output format.")
    if not isinstance(cleaned["use_ai_documentation"], bool) or not isinstance(cleaned["enable_voiceover"], bool):
        raise AppError(422, "validation_error", "AI documentation and voiceover settings must be true or false.")
    if cleaned["tts_provider"] not in {"", "openai", "openai_compatible"}:
        raise AppError(422, "validation_error", "Speech provider must be empty, openai, or openai_compatible.")
    if not isinstance(cleaned["ai_provider_id"], str) or not isinstance(cleaned["tts_model"], str) or not isinstance(cleaned["tts_voice"], str):
        raise AppError(422, "validation_error", "Provider, speech model, and voice must be text.")
    return cleaned
