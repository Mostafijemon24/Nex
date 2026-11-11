"""Playwright discovery. Login stops when MFA or CAPTCHA needs a person."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

from app.core.errors import AppError
from app.core.ssrf import (
    assert_pinned_host,
    chromium_host_resolver_rules,
    pin_hosts,
    validate_public_http_url,
)

MASK_SCRIPT = """
() => {
  const selector = 'input[type=password], input[name*=secret i], input[name*=token i], input[autocomplete=current-password]';
  document.querySelectorAll(selector).forEach((element) => {
    element.value = '••••••••';
    element.style.filter = 'blur(8px)';
  });
}
"""


def _same_scope(url: str, allowed_hosts: set[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in allowed_hosts


def _blocked_page(page) -> str | None:
    captcha = page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], iframe[src*='turnstile']")
    otp = page.locator("input[autocomplete='one-time-code'], input[name*='otp' i]")
    if captcha.count() > 0:
        return "A CAPTCHA is visible. The job is waiting for a person and will not bypass it."
    if otp.count() > 0:
        return "A one-time-code field is visible. The job is waiting for a person and will not bypass MFA."
    return None


def _login(page, username: str, password: str, extra_fields: dict, login_url: str) -> str | None:
    if login_url:
        page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
    for selector in ("input[type=email]", "input[name=email]", "input[name=username]", "input[name=user]", "input[type=text]"):
        locator = page.locator(selector).first
        if locator.count() > 0:
            locator.fill(username)
            break
    password_box = page.locator("input[type=password]").first
    if password_box.count() == 0:
        return "No password field was found on the login page."
    password_box.fill(password)
    for name, value in extra_fields.items():
        field = page.locator(f"input[name='{name}']").first
        if field.count() > 0:
            field.fill(str(value))
    blocker = _blocked_page(page)
    if blocker:
        return blocker
    submit = page.locator("button[type=submit], input[type=submit]").first
    if submit.count() == 0:
        return "No submit control was found on the login page."
    before = page.url
    submit.click()
    page.wait_for_timeout(1000)
    blocker = _blocked_page(page)
    if blocker:
        return blocker
    still_on_form = page.locator("input[type=password]").count() > 0 and page.url == before
    if still_on_form:
        return "Login did not leave the password form."
    return None


def discover_site(
    *,
    start_url: str,
    login_url: str = "",
    username: str = "",
    password: str = "",
    extra_fields: dict | None = None,
    allowed_hosts: list[str] | None = None,
    max_pages: int = 10,
    max_depth: int = 2,
    max_seconds: int = 90,
    allow_local: bool = False,
    on_screenshot: Callable[[bytes, str, str], None] | None = None,
) -> dict:
    host = (urlparse(start_url).hostname or "").lower()
    hosts = {item.lower() for item in (allowed_hosts or [host]) if item}
    if host not in hosts:
        hosts.add(host)
    pin_urls = [start_url]
    if login_url:
        pin_urls.append(login_url)
    # Only pin hosts that appear in the start/login URLs. Extra allowed_hosts entries
    # are scope checks only; they are not resolved up-front (they may be unavailable).
    pinned = pin_hosts(pin_urls, allow_local=allow_local)

    import os

    from playwright.sync_api import sync_playwright

    started = time.monotonic()
    pages: list[dict] = []
    console_errors: list[str] = []
    blocked_reason = ""
    chromium_args = ["--disable-dev-shm-usage"]
    # Containers usually cannot use Chromium's setuid sandbox. Keep the default
    # opt-out explicit so operators can require the sandbox outside Docker.
    if os.environ.get("CHROMIUM_NO_SANDBOX", "true").lower() in {"1", "true", "yes"}:
        chromium_args.append("--no-sandbox")
    resolver_rules = chromium_host_resolver_rules(pinned)
    if resolver_rules:
        chromium_args.append(f"--host-resolver-rules={resolver_rules}")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=chromium_args,
            )
            context = browser.new_context(accept_downloads=False, viewport={"width": 1280, "height": 720})

            def guard(route):
                target = route.request.url
                if target.startswith(("data:", "blob:", "about:")):
                    route.continue_()
                    return
                try:
                    assert_pinned_host(target, pinned, allow_local=allow_local)
                except AppError:
                    route.abort()
                    return
                if route.request.resource_type == "document" and not _same_scope(target, hosts):
                    route.abort()
                    return
                route.continue_()

            context.route("**/*", guard)
            page = context.new_page()
            page.on("console", lambda message: console_errors.append(message.text[:300]) if message.type == "error" else None)
            page.goto(assert_pinned_host(start_url, pinned, allow_local=allow_local), wait_until="domcontentloaded", timeout=30000)
            if username and password:
                if login_url:
                    assert_pinned_host(login_url, pinned, allow_local=allow_local)
                blocked_reason = _login(page, username, password, extra_fields or {}, login_url) or ""
                if blocked_reason:
                    pages.append(_capture(page, start_url, 0, "blocked", console_errors, on_screenshot))
                    browser.close()
                    return {"pages": pages, "blocked": blocked_reason, "console_errors": console_errors[:20], "pinned_hosts": sorted(pinned)}

            queue: list[tuple[str, int]] = [(page.url, 0)]
            seen: set[str] = set()
            while queue and len(pages) < max_pages:
                if time.monotonic() - started > max_seconds:
                    break
                current, depth = queue.pop(0)
                if current in seen or depth > max_depth or not _same_scope(current, hosts):
                    continue
                seen.add(current)
                try:
                    current = assert_pinned_host(current, pinned, allow_local=allow_local)
                    if page.url != current:
                        page.goto(current, wait_until="domcontentloaded", timeout=20000)
                except Exception as exc:
                    pages.append(
                        {
                            "url": current,
                            "title": "",
                            "access_status": "inaccessible",
                            "depth": depth,
                            "error": str(exc)[:300],
                            "console_errors": [],
                            "forms": 0,
                            "buttons": 0,
                            "headings": [],
                        }
                    )
                    continue
                pages.append(_capture(page, current, depth, "accessible", console_errors, on_screenshot))
                if depth == max_depth:
                    continue
                hrefs = page.eval_on_selector_all("a[href]", "elements => elements.map(element => element.href)")
                for href in hrefs:
                    absolute = urljoin(current, href).split("#", 1)[0]
                    if absolute not in seen and _same_scope(absolute, hosts):
                        try:
                            assert_pinned_host(absolute, pinned, allow_local=allow_local)
                        except AppError:
                            continue
                        queue.append((absolute, depth + 1))
            browser.close()
    except AppError:
        raise
    except Exception as exc:
        raise AppError(500, "browser_automation_error", f"Browser discovery failed: {exc.__class__.__name__}.", retryable=True) from exc
    return {"pages": pages, "blocked": blocked_reason, "console_errors": console_errors[:20], "pinned_hosts": sorted(pinned)}


def _capture(page, url: str, depth: int, status: str, console_errors: list[str], on_screenshot) -> dict:
    title = ""
    headings: list[str] = []
    forms = 0
    buttons = 0
    try:
        title = page.title()
        headings = page.eval_on_selector_all("h1, h2", "elements => elements.map(element => element.innerText.trim()).filter(Boolean).slice(0, 12)")
        forms = page.locator("form").count()
        buttons = page.locator("button, a, input[type=submit]").count()
        page.evaluate(MASK_SCRIPT)
    except Exception:
        status = "inaccessible"
    screenshot = b""
    try:
        screenshot = page.screenshot(full_page=False, type="png")
    except Exception:
        screenshot = b""
    if on_screenshot and screenshot.startswith(b"\x89PNG"):
        on_screenshot(screenshot, url, "viewport")
    return {
        "url": url,
        "title": title,
        "access_status": status,
        "depth": depth,
        "forms": forms,
        "buttons": buttons,
        "headings": headings,
        "console_errors": console_errors[-5:],
        "fact_type": "observed",
    }
