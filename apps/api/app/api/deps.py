"""Authentication and organization checks. Authorization happens on the server."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request, Response
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import hash_token
from app.models import Membership, Project, SessionToken, User
from app.models.entities import utcnow

ADMIN_ROLES = {"owner", "admin"}


def client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def rate_limit(bucket: str, limit: int, window_seconds: int) -> None:
    from redis import Redis

    settings = get_settings()
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        count = int(client.incr(bucket))
        if count == 1:
            client.expire(bucket, window_seconds)
    except RedisError as exc:
        raise AppError(
            503,
            "configuration_error",
            "Redis is not reachable, so the request was not processed. Check REDIS_URL.",
            retryable=True,
        ) from exc
    if count > limit:
        raise AppError(429, "validation_error", "Too many requests. Wait a moment and try again.", retryable=True)


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_ttl_seconds,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie("session", path="/")


def current_user(request: Request, db: Session) -> User:
    token = request.cookies.get("session", "")
    if not token:
        raise AppError(401, "authentication_error", "Sign in required.")
    row = db.scalar(
        select(SessionToken).where(
            SessionToken.token_hash == hash_token(token),
            SessionToken.revoked_at.is_(None),
            SessionToken.expires_at > utcnow(),
        )
    )
    if row is None:
        raise AppError(401, "authentication_error", "Sign in required.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AppError(401, "authentication_error", "Sign in required.")
    return user


def active_membership(request: Request, db: Session, user: User) -> Membership:
    rows = list(db.scalars(select(Membership).where(Membership.user_id == user.id)).all())
    if not rows:
        raise AppError(403, "permission_error", "Your account is not a member of an organization.")
    requested = request.headers.get("x-organization-id", "")
    if requested:
        match = next((row for row in rows if row.organization_id == requested), None)
        if match is None:
            raise AppError(403, "permission_error", "You do not belong to that organization.")
        return match
    if len(rows) > 1:
        raise AppError(422, "validation_error", "Choose an organization with the X-Organization-Id header.")
    return rows[0]


def require_project(db: Session, membership: Membership, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.deleted_at is not None or project.organization_id != membership.organization_id:
        raise AppError(404, "permission_error", "Project not found.")
    return project


def require_admin(membership: Membership) -> None:
    if membership.role not in ADMIN_ROLES:
        raise AppError(403, "permission_error", "This action requires an organization admin.")


def session_expiry():
    return utcnow() + timedelta(seconds=get_settings().access_token_ttl_seconds)
