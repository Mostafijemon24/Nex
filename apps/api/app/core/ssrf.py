"""URL checks for browser automation, publishing, and repository fetch.

Private networks, link-local addresses, and cloud metadata hosts are rejected.
ALLOW_LOCAL_TARGETS permits loopback and RFC1918 addresses for local development
and tests. Link-local and metadata addresses stay blocked in every mode.

Hostname targets are resolved twice before they are accepted. Differing answers
are treated as DNS rebinding. Application HTTP clients in `app.core.outbound`
pin the TCP connection to a validated address and revalidate every redirect.
Browser automation and `git clone` still use their own resolvers after URL checks.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from app.core.errors import AppError

METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
}
LINK_LOCAL_V4 = ipaddress.ip_network("169.254.0.0/16")


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_local: bool) -> bool:
    if isinstance(ip, ipaddress.IPv4Address) and ip in LINK_LOCAL_V4:
        return True
    if ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    if ip.is_loopback or ip.is_private:
        return not allow_local
    return False


def _resolve_host(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        literal = ipaddress.ip_address(host)
        return [literal]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port)
    except socket.gaierror as exc:
        raise AppError(422, "network_error", f"Could not resolve {host}.", retryable=True) from exc
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not addresses:
        raise AppError(422, "network_error", f"Could not resolve {host}.", retryable=True)
    return addresses


def resolve_public_http_url(url: str, *, allow_local: bool = False) -> tuple[str, list[str]]:
    """Validate a URL and return it with the resolved IP strings that were allowed."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise AppError(422, "validation_error", "Only http and https URLs are allowed.")
    if parsed.username or parsed.password:
        raise AppError(422, "validation_error", "URLs must not embed usernames or passwords.")
    host = parsed.hostname
    if not host:
        raise AppError(422, "validation_error", "URL is missing a host.")
    lowered = host.lower().rstrip(".")
    if lowered in METADATA_HOSTS or lowered.endswith(".internal") or lowered.endswith(".local"):
        raise AppError(422, "browser_automation_error", "This host is not an allowed automation target.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    first = _resolve_host(lowered, port)
    try:
        literal = ipaddress.ip_address(lowered)
        is_literal = True
        _ = literal
    except ValueError:
        is_literal = False
    if not is_literal:
        second = _resolve_host(lowered, port)
        if {str(item) for item in first} != {str(item) for item in second}:
            raise AppError(
                422,
                "browser_automation_error",
                "The target hostname resolved to different addresses across lookups. The request was rejected to reduce DNS rebinding risk.",
            )
        first = second
    for ip in first:
        if _blocked_ip(ip, allow_local):
            raise AppError(
                422,
                "browser_automation_error",
                "The target resolves to a blocked network address. Private, loopback, and metadata addresses are not allowed.",
            )
    return url.strip(), [str(item) for item in first]


def validate_public_http_url(url: str, *, allow_local: bool = False) -> str:
    cleaned, _addresses = resolve_public_http_url(url, allow_local=allow_local)
    return cleaned


def prefer_ip(addresses: list[str]) -> str:
    ipv4: list[str] = []
    ipv6: list[str] = []
    for item in addresses:
        parsed = ipaddress.ip_address(item)
        if parsed.version == 4:
            ipv4.append(item)
        else:
            ipv6.append(item)
    if ipv4:
        return ipv4[0]
    if ipv6:
        return ipv6[0]
    raise AppError(422, "network_error", "No usable address remained after validation.")


def pin_hosts(urls: list[str], *, allow_local: bool = False) -> dict[str, list[str]]:
    """Resolve each URL's hostname twice-safe and return host → allowed IP strings."""
    pinned: dict[str, list[str]] = {}
    for url in urls:
        if not url:
            continue
        _cleaned, addresses = resolve_public_http_url(url, allow_local=allow_local)
        host = (urlparse(url).hostname or "").lower().rstrip(".")
        if not host:
            continue
        if host in pinned and set(pinned[host]) != set(addresses):
            raise AppError(
                422,
                "browser_automation_error",
                f"Host {host} resolved to inconsistent addresses while building the network pin map.",
            )
        pinned[host] = addresses
    return pinned


def chromium_host_resolver_rules(pinned: dict[str, list[str]]) -> str:
    """Chromium `--host-resolver-rules` that force pinned hosts onto validated IPs."""
    rules: list[str] = []
    for host, addresses in pinned.items():
        try:
            ipaddress.ip_address(host)
            continue
        except ValueError:
            pass
        rules.append(f"MAP {host} {prefer_ip(addresses)}")
    if not rules:
        return ""
    # Unmapped names fail closed inside the browser process for this job.
    rules.append("MAP * ~NOTFOUND")
    return ", ".join(rules)


def assert_pinned_host(url: str, pinned: dict[str, list[str]], *, allow_local: bool = False) -> str:
    """Re-resolve a URL and require it to match the launch-time pin map."""
    cleaned, addresses = resolve_public_http_url(url, allow_local=allow_local)
    host = (urlparse(cleaned).hostname or "").lower().rstrip(".")
    expected = pinned.get(host)
    if expected is None:
        raise AppError(422, "browser_automation_error", f"Host {host} is outside the pinned destination set.")
    if set(addresses) != set(expected):
        raise AppError(
            422,
            "browser_automation_error",
            f"Host {host} no longer resolves to the addresses pinned for this job.",
        )
    return cleaned
