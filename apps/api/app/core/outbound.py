"""Outbound HTTP that pins TCP to a validated resolved address.

User-controlled destinations are resolved and checked before connect. The socket
connects to that IP while TLS SNI and the Host header keep the original hostname.
Redirects are not followed by the TLS stack; each Location is revalidated first.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urljoin, urlparse

import httpx

from app.core.errors import AppError
from app.core.ssrf import prefer_ip, resolve_public_http_url

MAX_RESPONSE_BYTES = 1_000_000
MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def _host_header(parsed) -> str:
    host = parsed.hostname or ""
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def _path(parsed) -> str:
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def _connect(method: str, url: str, addresses: list[str], *, headers: dict[str, str], body: bytes | None, timeout: float) -> httpx.Response:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise AppError(422, "validation_error", "URL is missing a host.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    ip = prefer_ip(addresses)
    request_headers = {key: value for key, value in headers.items()}
    lowered = {key.lower() for key in request_headers}
    if "host" not in lowered:
        request_headers["Host"] = _host_header(parsed)

    class PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self_inner) -> None:  # noqa: N805
            self_inner.sock = socket.create_connection((ip, port), self_inner.timeout)

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self_inner) -> None:  # noqa: N805
            raw = socket.create_connection((ip, port), self_inner.timeout)
            context = self_inner._context or ssl.create_default_context()
            self_inner.sock = context.wrap_socket(raw, server_hostname=host)

    try:
        if parsed.scheme == "https":
            connection: http.client.HTTPConnection = PinnedHTTPSConnection(
                host,
                port=port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = PinnedHTTPConnection(host, port=port, timeout=timeout)
        connection.request(method.upper(), _path(parsed), body=body, headers=request_headers)
        raw_response = connection.getresponse()
        content = raw_response.read(MAX_RESPONSE_BYTES + 1)
        header_pairs = [(key, value) for key, value in raw_response.getheaders()]
        status = raw_response.status
        reason = raw_response.reason
        connection.close()
    except ssl.SSLError as exc:
        raise AppError(502, "network_error", f"TLS failed for {host}: {exc.__class__.__name__}.", retryable=True) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise AppError(504, "network_error", "The remote host timed out.", retryable=True) from exc
    except OSError as exc:
        raise AppError(502, "network_error", f"Could not reach the host: {exc.__class__.__name__}.", retryable=True) from exc

    if len(content) > MAX_RESPONSE_BYTES:
        raise AppError(502, "provider_error", "The response exceeded the size limit.")
    return httpx.Response(
        status,
        headers=header_pairs,
        content=content,
        request=httpx.Request(method.upper(), url),
        extensions={"reason": reason, "pinned_ip": ip},
    )


def outbound_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict | list | bytes | str | None = None,
    timeout: float = 20.0,
    allow_local: bool = False,
    max_redirects: int = MAX_REDIRECTS,
) -> httpx.Response:
    """Perform an HTTP request with DNS validation, IP pinning, and safe redirects."""
    current = url.strip()
    current_method = method.upper()
    request_headers = dict(headers or {})
    payload: bytes | None
    if body is None:
        payload = None
    elif isinstance(body, bytes):
        payload = body
    elif isinstance(body, (dict, list)):
        payload = json.dumps(body).encode("utf-8")
        if "content-type" not in {key.lower() for key in request_headers}:
            request_headers["Content-Type"] = "application/json"
    else:
        payload = str(body).encode("utf-8")

    for _ in range(max_redirects + 1):
        cleaned, addresses = resolve_public_http_url(current, allow_local=allow_local)
        response = _connect(current_method, cleaned, addresses, headers=request_headers, body=payload, timeout=timeout)
        if response.status_code not in REDIRECT_STATUSES:
            return response
        location = response.headers.get("location")
        if not location:
            return response
        next_url = urljoin(cleaned, location)
        parsed = urlparse(next_url)
        if parsed.scheme not in {"http", "https"}:
            raise AppError(422, "network_error", "Redirects must stay on http or https.")
        current = next_url
        if response.status_code in {301, 302, 303}:
            current_method = "GET"
            payload = None
        # 307/308 keep method and body
    raise AppError(502, "network_error", "Too many redirects while contacting the remote host.")
