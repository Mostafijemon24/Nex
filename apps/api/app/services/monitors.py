"""Scheduled rescans. A disabled row or an unfinished job does not start another run."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Job, MonitorSchedule, Project, User
from app.models.entities import utcnow
from app.services.jobs import create_job


def run_due() -> int:
    db = SessionLocal()
    started = 0
    try:
        now = utcnow()
        rows = list(
            db.scalars(
                select(MonitorSchedule).where(MonitorSchedule.enabled.is_(True), MonitorSchedule.next_run_at.is_not(None), MonitorSchedule.next_run_at <= now)
            ).all()
        )
        for row in rows:
            project = db.get(Project, row.project_id)
            user = db.get(User, project.created_by) if project else None
            if project is None or user is None or project.organization_id != row.organization_id:
                row.last_status = "failed"
                row.last_error = "The project or owner no longer exists."
                row.next_run_at = now + timedelta(minutes=row.interval_minutes)
                continue
            active = db.scalar(select(Job).where(Job.project_id == project.id, Job.status.in_(["queued", "running", "retrying", "paused"])))
            if active is not None:
                row.last_status = "skipped_duplicate"
                row.last_error = "A job for this project is already unfinished."
                row.next_run_at = now + timedelta(minutes=row.interval_minutes)
                continue
            try:
                create_job(db, project, user, "rescan", "")
                row.last_status = "queued"
                row.last_error = ""
                started += 1
            except Exception as exc:
                row.last_status = "failed"
                row.last_error = exc.__class__.__name__
            row.last_run_at = now
            row.next_run_at = now + timedelta(minutes=max(row.interval_minutes, 15))
        db.commit()
        return started
    finally:
        db.close()
