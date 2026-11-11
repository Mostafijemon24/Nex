"""Setup, session, and organization users."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import (
    active_membership,
    clear_session_cookie,
    client_ip,
    current_user,
    rate_limit,
    require_admin,
    session_expiry,
    set_session_cookie,
)
from app.core.errors import AppError
from app.core.redact import redact
from app.core.security import encrypt_secret, hash_password, hash_token, new_session_token, secret_hint, verify_password
from app.db.session import get_db
from app.models import AuditLog, Credential, Membership, Organization, SessionToken, User
from app.models.entities import utcnow

router = APIRouter(prefix="/api/v1", tags=["auth"])


class SetupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserCreateRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    role: str = "member"


def _user_body(user: User, membership: Membership | None = None) -> dict:
    body = {"id": user.id, "email": user.email, "display_name": user.display_name}
    if membership is not None:
        body["organization_id"] = membership.organization_id
        body["role"] = membership.role
    return body


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    count = db.scalar(select(func.count()).select_from(User)) or 0
    return {"needs_setup": count == 0}


@router.post("/setup", status_code=201)
def setup(payload: SetupRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    rate_limit(f"setup:{client_ip(request)}", 5, 300)
    count = db.scalar(select(func.count()).select_from(User)) or 0
    if count:
        raise AppError(409, "validation_error", "An administrator already exists. Sign in instead.")
    user = User(email=str(payload.email).lower(), password_hash=hash_password(payload.password), display_name=payload.display_name)
    organization = Organization(name=payload.organization_name)
    db.add(user)
    db.add(organization)
    db.flush()
    membership = Membership(organization_id=organization.id, user_id=user.id, role="owner")
    db.add(membership)
    token = new_session_token()
    db.add(SessionToken(user_id=user.id, token_hash=hash_token(token), expires_at=session_expiry()))
    db.add(AuditLog(organization_id=organization.id, user_id=user.id, action="setup", target=organization.id, details={}))
    db.commit()
    set_session_cookie(response, token)
    return {"user": _user_body(user, membership), "organization": {"id": organization.id, "name": organization.name}}


@router.post("/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    rate_limit(f"login:{client_ip(request)}", 10, 60)
    email = str(payload.email).lower()
    user = db.scalar(select(User).where(User.email == email))
    valid = user is not None and user.is_active and verify_password(user.password_hash, payload.password)
    if not valid:
        raise AppError(401, "authentication_error", "Email or password is incorrect.")
    token = new_session_token()
    db.add(SessionToken(user_id=user.id, token_hash=hash_token(token), expires_at=session_expiry()))
    db.add(AuditLog(user_id=user.id, action="login", target=user.id, details={"ip": client_ip(request)}))
    db.commit()
    membership = db.scalar(select(Membership).where(Membership.user_id == user.id))
    set_session_cookie(response, token)
    return {"user": _user_body(user, membership)}


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    token = request.cookies.get("session", "")
    if token:
        row = db.scalar(select(SessionToken).where(SessionToken.token_hash == hash_token(token)))
        if row is not None:
            row.revoked_at = utcnow()
            db.commit()
    clear_session_cookie(response)
    return {"signed_out": True}


@router.get("/auth/me")
def me(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    organization = db.get(Organization, membership.organization_id)
    return {
        "user": _user_body(user, membership),
        "organization": {"id": organization.id, "name": organization.name} if organization else None,
    }


@router.get("/users")
def list_users(request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    rows = db.execute(
        select(User, Membership)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.organization_id == membership.organization_id)
    ).all()
    return {
        "users": [
            {"id": member.id, "email": member.email, "display_name": member.display_name, "role": link.role}
            for member, link in rows
        ]
    }


@router.post("/users", status_code=201)
def create_user(payload: UserCreateRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    user = current_user(request, db)
    membership = active_membership(request, db, user)
    require_admin(membership)
    if payload.role not in {"admin", "member"}:
        raise AppError(422, "validation_error", "Role must be admin or member.")
    email = str(payload.email).lower()
    if db.scalar(select(User).where(User.email == email)):
        raise AppError(409, "validation_error", "A user with that email already exists.")
    created = User(email=email, password_hash=hash_password(payload.password), display_name=payload.display_name)
    db.add(created)
    db.flush()
    db.add(Membership(organization_id=membership.organization_id, user_id=created.id, role=payload.role))
    db.add(
        AuditLog(
            organization_id=membership.organization_id,
            user_id=user.id,
            action="user.create",
            target=created.id,
            details=redact({"email": email, "role": payload.role}),
        )
    )
    db.commit()
    return {"id": created.id, "email": created.email, "display_name": created.display_name, "role": payload.role}


def store_secret(db: Session, organization_id: str, project_id: str | None, kind: str, label: str, secret: str) -> Credential:
    credential = Credential(
        organization_id=organization_id,
        project_id=project_id,
        kind=kind,
        label=label,
        ciphertext=encrypt_secret(secret),
        hint=secret_hint(secret),
    )
    db.add(credential)
    db.flush()
    return credential


def login_payload(password: str, extra_fields: dict) -> str:
    return json.dumps({"password": password, "extra_fields": extra_fields})
