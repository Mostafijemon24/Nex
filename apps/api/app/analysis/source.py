"""Static source inspection. Uploaded code is never executed."""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.redact import redact
from app.core.ssrf import validate_public_http_url

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".php": "PHP",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".css": "CSS",
    ".html": "HTML",
    ".vue": "Vue",
    ".sql": "SQL",
}

ROUTE_PATTERNS = (
    ("python", "decorator", r"@(?:app|router|bp)\.(get|post|put|patch|delete|route)\(\s*['\"]([^'\"]+)"),
    ("javascript", "call", r"\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)"),
    ("php", "wp_hook", r"add_(action|filter)\(\s*['\"]([^'\"]+)"),
)

TEXT_LIMIT = 1_000_000
SKIP_DIRS = {".git", "node_modules", "vendor", "venv", ".venv", "__pycache__", "dist", "build"}


def safe_extract_zip(zip_path: Path, destination: Path) -> int:
    settings = get_settings()
    destination.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise AppError(400, "file_processing_error", "The upload is not a valid ZIP archive.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > settings.max_archive_files:
            raise AppError(400, "file_processing_error", "The archive contains too many files.")
        total = 0
        for info in infos:
            name = info.filename
            parts = Path(name).parts
            unix_mode = info.external_attr >> 16
            if name.startswith("/") or name.startswith("\\") or ".." in parts or (unix_mode & 0o170000) == 0o120000:
                raise AppError(400, "file_processing_error", "The archive contains an unsafe path.")
            total += info.file_size
            if total > settings.max_archive_uncompressed_bytes:
                raise AppError(400, "file_processing_error", "The archive expands beyond the configured size limit.")
            if info.compress_size and info.file_size / max(info.compress_size, 1) > 100:
                raise AppError(400, "file_processing_error", "The archive compression ratio is not allowed.")
            target = (destination / name).resolve()
            if not str(target).startswith(str(destination.resolve())):
                raise AppError(400, "file_processing_error", "The archive contains an unsafe path.")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, target.open("wb") as output:
                remaining = settings.max_archive_uncompressed_bytes - (total - info.file_size)
                written = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > remaining:
                        raise AppError(400, "file_processing_error", "The archive expands beyond the configured size limit.")
                    output.write(chunk)
    return total


def _read_text(path: Path) -> str | None:
    if path.stat().st_size > TEXT_LIMIT:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _frameworks(root: Path, languages: set[str]) -> list[str]:
    found: list[str] = []
    package = root / "package.json"
    if package.exists():
        text = _read_text(package) or ""
        for name in ("next", "react", "express", "vue", "fastify"):
            if f'"{name}"' in text:
                found.append(name)
    if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists():
        blob = " ".join(
            _read_text(path) or ""
            for path in (root / "pyproject.toml", root / "requirements.txt")
            if path.exists()
        )
        for name in ("fastapi", "django", "flask", "celery"):
            if name in blob.lower():
                found.append(name)
    if (root / "composer.json").exists() or "PHP" in languages:
        if any(root.rglob("*.php")):
            found.append("php")
    if (root / "wp-content").exists() or any(root.glob("*.php")):
        header = ""
        for candidate in list(root.glob("*.php"))[:20]:
            header += _read_text(candidate) or ""
        if "Plugin Name:" in header:
            found.append("wordpress-plugin")
        style = root / "style.css"
        if style.exists() and "Theme Name:" in (_read_text(style) or ""):
            found.append("wordpress-theme")
    return sorted(set(found))


def _routes(root: Path) -> list[dict]:
    import re

    routes: list[dict] = []
    compiled = [(label, kind, re.compile(pattern)) for label, kind, pattern in ROUTE_PATTERNS]
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".js", ".ts", ".php"}:
            continue
        # Next.js App Router file routes are observed from the path itself.
        relative = path.relative_to(root).as_posix()
        if relative.startswith("app/") and path.name in {"page.tsx", "page.jsx", "route.ts", "route.js"}:
            url = "/" + "/".join(part for part in Path(relative).parts[1:-1] if not part.startswith("("))
            routes.append(
                {
                    "path": url or "/",
                    "file": relative,
                    "line_start": 1,
                    "line_end": 1,
                    "symbol": path.name,
                    "fact_type": "source_code",
                }
            )
        text = _read_text(path)
        if text is None:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            redacted_line = redact(line)
            for _label, kind, pattern in compiled:
                match = pattern.search(line)
                if not match:
                    continue
                routes.append(
                    {
                        "path": match.group(2),
                        "method": match.group(1),
                        "file": relative,
                        "line_start": index,
                        "line_end": index,
                        "symbol": kind,
                        "excerpt": redacted_line.strip()[:240],
                        "fact_type": "source_code",
                    }
                )
    return routes[:500]


def _plugin_or_theme(root: Path) -> dict:
    details: dict = {}
    for path in list(root.glob("*.php"))[:30]:
        text = _read_text(path) or ""
        if "Plugin Name:" not in text:
            continue
        for line in text.splitlines():
            if ":" in line and line.split(":", 1)[0].strip() in {
                "Plugin Name",
                "Description",
                "Version",
                "Author",
            }:
                key, value = line.split(":", 1)
                details[key.strip()] = redact(value.strip())
        details["header_file"] = path.relative_to(root).as_posix()
        break
    style = root / "style.css"
    if style.exists():
        text = _read_text(style) or ""
        if "Theme Name:" in text:
            for line in text.splitlines():
                if ":" in line and line.split(":", 1)[0].strip() in {"Theme Name", "Description", "Version"}:
                    key, value = line.split(":", 1)
                    details[key.strip()] = redact(value.strip())
            details["header_file"] = "style.css"
    readme = next(root.glob("readme.txt"), None)
    if readme is not None:
        details["readme"] = ( _read_text(readme) or "")[:4000]
    return details


def analyze_tree(root: Path) -> dict:
    languages: dict[str, int] = {}
    entry_points: list[str] = []
    dependencies: list[str] = []
    config_files: list[str] = []
    tests: list[str] = []
    deployment_files: list[str] = []
    file_count = 0
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        file_count += 1
        relative = path.relative_to(root).as_posix()
        language = LANGUAGE_BY_SUFFIX.get(path.suffix.lower())
        if language:
            languages[language] = languages.get(language, 0) + 1
        name = path.name.lower()
        if name in {"main.py", "app.py", "index.js", "index.ts", "manage.py", "functions.php"}:
            entry_points.append(relative)
        if name in {"package.json", "pyproject.toml", "requirements.txt", "composer.json", "go.mod", "cargo.toml"}:
            dependencies.append(relative)
        if name in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"} or name.startswith(".env"):
            deployment_files.append(relative)
        if "config" in name or name in {"alembic.ini", "next.config.ts", "next.config.js"}:
            config_files.append(relative)
        if name.startswith("test_") or name.endswith(".test.js") or name.endswith(".spec.ts") or "/tests/" in f"/{relative}":
            tests.append(relative)
    frameworks = _frameworks(root, set(languages))
    return {
        "fact_type": "source_code",
        "file_count": file_count,
        "languages": languages,
        "frameworks": frameworks,
        "entry_points": entry_points[:50],
        "dependencies": dependencies[:50],
        "config_files": config_files[:50],
        "deployment_files": deployment_files[:50],
        "tests": tests[:100],
        "routes": _routes(root),
        "package_metadata": _plugin_or_theme(root),
        "executed": False,
    }


def clone_repository(url: str, destination: Path, *, allow_local: bool) -> str:
    destination.mkdir(parents=True, exist_ok=True)
    local_path = Path(url)
    lowered = url.strip().lower()
    if lowered.startswith(("git://", "ssh://", "file://", "ftp://")) or url.strip().startswith("git@"):
        raise AppError(422, "validation_error", "Only http(s) repository URLs are allowed.")
    if url.startswith("http://") or url.startswith("https://"):
        from app.core.ssrf import resolve_public_http_url

        first_url, first_addrs = resolve_public_http_url(url, allow_local=allow_local)
        second_url, second_addrs = resolve_public_http_url(url, allow_local=allow_local)
        if first_url != second_url or set(first_addrs) != set(second_addrs):
            raise AppError(
                422,
                "network_error",
                "The repository host resolved inconsistently before clone. The request was rejected.",
            )
        cleaned = first_url
        # Git still performs its own DNS lookup for TLS/SNI. Redirects are disabled so a
        # post-resolve HTTP redirect cannot move the clone onto an unvalidated host.
        command = [
            "git",
            "-c",
            "http.followRedirects=false",
            "-c",
            "credential.helper=",
            "-c",
            "core.askPass=",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            cleaned,
            str(destination),
        ]
    elif allow_local and local_path.is_dir():
        command = ["git", "clone", "--depth", "1", str(local_path.resolve()), str(destination)]
    else:
        raise AppError(422, "validation_error", "Repository URLs must be http(s). Local paths are disabled.")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.hooksPath"
    env["GIT_CONFIG_VALUE_0"] = "/dev/null"
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AppError(504, "network_error", "Repository clone timed out.", retryable=True) from exc
    if completed.returncode != 0:
        message = redact((completed.stderr or completed.stdout or "git clone failed").strip())
        raise AppError(422, "network_error", f"Repository clone failed: {message[:500]}", retryable=True)
    sha = subprocess.run(
        ["git", "-C", str(destination), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (sha.stdout or "").strip()


def directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
