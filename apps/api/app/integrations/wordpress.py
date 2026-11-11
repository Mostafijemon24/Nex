"""WordPress REST publishing. The default status is draft."""

from __future__ import annotations

import base64

from app.core.errors import AppError
from app.core.outbound import outbound_request
from app.core.ssrf import validate_public_http_url


def _base(url: str, *, allow_local: bool) -> str:
    validate_public_http_url(url, allow_local=allow_local)
    return url.rstrip("/")


def _auth_header(username: str, application_password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{application_password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_connection(site_url: str, username: str, application_password: str, *, allow_local: bool) -> tuple[bool, str]:
    url = _base(site_url, allow_local=allow_local) + "/wp-json/wp/v2/users/me"
    try:
        response = outbound_request(
            "GET",
            url,
            headers=_auth_header(username, application_password),
            timeout=20.0,
            allow_local=allow_local,
        )
    except AppError as exc:
        return False, f"Could not reach WordPress: {exc.message}"
    if response.status_code in {401, 403}:
        return False, "WordPress rejected the application password."
    if response.status_code != 200:
        return False, f"WordPress returned HTTP {response.status_code}."
    try:
        payload = response.json()
    except ValueError:
        return False, "WordPress did not return JSON."
    if "id" not in payload:
        return False, "WordPress did not confirm the authenticated user."
    return True, "WordPress connection succeeded."


def create_draft(
    site_url: str,
    username: str,
    application_password: str,
    *,
    title: str,
    content: str,
    status: str,
    allow_local: bool,
) -> dict:
    if status not in {"draft", "publish"}:
        raise AppError(422, "validation_error", "Publication status must be draft or publish.")
    url = _base(site_url, allow_local=allow_local) + "/wp-json/wp/v2/posts"
    response = outbound_request(
        "POST",
        url,
        headers=_auth_header(username, application_password),
        body={"title": title, "content": content, "status": status},
        timeout=30.0,
        allow_local=allow_local,
    )
    if response.status_code not in {200, 201}:
        raise AppError(502, "publishing_error", f"WordPress returned HTTP {response.status_code}.", retryable=response.status_code >= 500)
    try:
        payload = response.json()
    except ValueError as exc:
        raise AppError(502, "publishing_error", "WordPress did not return a post payload.") from exc
    if not payload.get("id"):
        raise AppError(502, "publishing_error", "WordPress did not confirm a post id.")
    remote_status = payload.get("status") or ""
    if remote_status != status:
        raise AppError(502, "publishing_error", "WordPress confirmed a different publication status than requested.")
    return {
        "remote_id": str(payload.get("id")),
        "remote_url": payload.get("link") or "",
        "status": remote_status,
    }
