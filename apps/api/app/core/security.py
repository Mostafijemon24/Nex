"""Password hashing, session tokens, and credential encryption."""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import AppError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise AppError(422, "validation_error", "Password must be at least 12 characters.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    key = get_settings().credential_encryption_key.encode("utf-8")
    try:
        return Fernet(key)
    except (ValueError, TypeError) as exc:
        raise AppError(
            500,
            "configuration_error",
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key. "
            'Generate one with: python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"',
        ) from exc


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise AppError(
            500,
            "configuration_error",
            "Stored credential could not be decrypted. The encryption key does not match the key used when it was saved.",
        ) from exc


def secret_hint(value: str) -> str:
    raw = value[-4:] if len(value) >= 4 else ""
    # HTTP clients and logs expect ASCII-safe hints. Non-ASCII tails (from a bad paste) are dropped.
    tail = "".join(ch for ch in raw if 32 <= ord(ch) <= 126)
    return f"****{tail}" if tail else "****"


def require_http_secret(value: str, *, label: str = "API key") -> str:
    """Reject secrets that cannot be sent in an HTTP Authorization header."""
    cleaned = (value or "").strip()
    # Common paste mistakes: wrapping quotes, embedded newlines, leading "Bearer ".
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    cleaned = "".join(cleaned.split())
    if cleaned.lower().startswith("bearer"):
        cleaned = cleaned[6:].lstrip(":")
        cleaned = cleaned.strip()
    if not cleaned:
        raise AppError(422, "validation_error", f"A {label} is required.")
    if not cleaned.isascii():
        raise AppError(
            422,
            "validation_error",
            f"The {label} contains non-ASCII characters and cannot be used in an HTTP Authorization header. Paste the key exactly as issued by the provider.",
        )
    return cleaned
