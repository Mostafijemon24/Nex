"""Behavioral tests. A criterion is recorded only after its assertion holds."""

from __future__ import annotations

import io
import json
import socket
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.analysis.source import safe_extract_zip
from app.core.security import hash_password, hash_token, new_session_token
from app.core.ssrf import validate_public_http_url
from app.db.session import SessionLocal
from app.models import Job, Membership, Organization, SessionToken, User
from app.services.pipeline import execute_job
from tests.conftest import record

PASSWORD = "correct-horse-battery"
SECRET = "super-secret-value"
FIXTURE = """from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}
"""


def setup(client, email="owner@example.com"):
    response = client.post(
        "/api/v1/setup",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "Owner",
            "organization_name": "Example",
        },
    )
    assert response.status_code == 201, response.text
    return response


def auth(client):
    setup(client)
    return client


def project(client, name="Demo product"):
    response = client.post("/api/v1/projects", json={"name": name, "description": "Fixture"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def zip_bytes(source: str, extra: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("app.py", source)
        for name, content in (extra or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FixtureSite(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/features"):
            body = b"<html><head><title>Features</title></head><body><h1>Features</h1></body></html>"
        elif self.path == "/external":
            body = b"<html><head><title>External</title></head><body></body></html>"
        else:
            body = (
                b"<html><head><title>Demo</title></head><body><h1>Home</h1>"
                b"<a href='/features'>Features</a><a href='http://example.com/external'>Away</a>"
                b"<form><input name='q'></form></body></html>"
            )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def serve_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureSite)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_setup_and_password_rule(client):
    created = setup(client)
    assert created.json()["user"]["email"] == "owner@example.com"
    assert "password" not in created.text
    again = client.post(
        "/api/v1/setup",
        json={"email": "other@example.com", "password": PASSWORD, "display_name": "Other", "organization_name": "Other"},
    )
    assert again.status_code == 409
    record("AUTH-001", "AUTH-001-AC-01", "passed", "Second setup was rejected after the administrator existed.")
    short = client.post(
        "/api/v1/users",
        json={"email": "short@example.com", "password": "short-pass", "display_name": "Short", "role": "member"},
    )
    assert short.status_code == 422
    record("AUTH-001", "AUTH-001-AC-02", "passed", "A password shorter than 12 characters is rejected.")


def test_login_and_invalid_login(client):
    setup(client)
    client.post("/api/v1/auth/logout")
    wrong = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "wrong-password-1"})
    missing = client.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": "wrong-password-1"})
    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]
    record("AUTH-003", "AUTH-003-AC-01", "passed", "Unknown email and wrong password return the same message.")
    logged_in = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    assert PASSWORD not in logged_in.text
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@example.com"
    assert PASSWORD not in me.text
    record("AUTH-002", "AUTH-002-AC-01", "passed", "The session can read the current user.")
    record("AUTH-002", "AUTH-002-AC-02", "passed", "The password is absent from login and profile responses.")


def test_project_source_and_metadata_block(client):
    auth(client)
    project_id = project(client)
    listed = client.get("/api/v1/projects")
    assert any(item["id"] == project_id for item in listed.json()["projects"])
    record("PROJ-001", "PROJ-001-AC-01", "passed", "The created project is in the list.")
    source = client.post(
        f"/api/v1/projects/{project_id}/sources",
        json={
            "kind": "website",
            "name": "Site",
            "config": {"url": "http://127.0.0.1", "username": "ada", "extra_fields": {"office": "north"}},
            "password": "website-password",
        },
    )
    assert source.status_code == 201, source.text
    assert "website-password" not in source.text
    assert "north" not in source.text
    assert source.json()["has_secret"] is True
    record("SRC-001", "SRC-001-AC-01", "passed", "Website source is stored without the password or extra field value.")
    with pytest.raises(Exception):
        validate_public_http_url("http://169.254.169.254/latest/meta-data/", allow_local=True)
    blocked = client.post(
        f"/api/v1/projects/{project_id}/sources",
        json={"kind": "website", "name": "Metadata", "config": {"url": "http://169.254.169.254/"}},
    )
    assert blocked.status_code == 422
    record("SRC-002", "SRC-002-AC-01", "passed", "The metadata address is rejected.")


def test_git_clone_rejects_unsafe_protocols():
    from app.analysis.source import clone_repository
    from app.core.errors import AppError
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        for bad in ("git://example.com/repo.git", "ssh://git@example.com/repo.git", "file:///etc/passwd", "git@github.com:org/repo.git"):
            with pytest.raises(AppError):
                clone_repository(bad, Path(temporary) / "out", allow_local=False)


def test_chromium_resolver_rules_pin_hostnames():
    from app.core.ssrf import chromium_host_resolver_rules

    rules = chromium_host_resolver_rules({"example.test": ["203.0.113.10"], "127.0.0.1": ["127.0.0.1"]})
    assert "MAP example.test 203.0.113.10" in rules
    assert "MAP * ~NOTFOUND" in rules
    assert "MAP 127.0.0.1" not in rules
    assert chromium_host_resolver_rules({"127.0.0.1": ["127.0.0.1"]}) == ""


def test_dns_rebinding_rejected(monkeypatch):
    from app.core import ssrf
    import ipaddress

    answers = [
        [ipaddress.ip_address("1.2.3.4")],
        [ipaddress.ip_address("8.8.8.8")],
    ]

    def flip(_host: str, _port: int):
        return answers.pop(0)

    monkeypatch.setattr(ssrf, "_resolve_host", flip)
    with pytest.raises(Exception) as raised:
        ssrf.validate_public_http_url("https://example.test/path", allow_local=False)
    assert "rebinding" in str(raised.value).lower() or "different addresses" in str(raised.value).lower()


def test_outbound_pins_and_blocks_unsafe_redirect(monkeypatch):
    import ipaddress
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from app.core import outbound, ssrf
    from app.core.errors import AppError

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/ok":
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/jump":
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def resolve(host: str, _port: int):
            try:
                return [ipaddress.ip_address(host)]
            except ValueError:
                return [ipaddress.ip_address("127.0.0.1")]

        monkeypatch.setattr(ssrf, "_resolve_host", resolve)
        ok = outbound.outbound_request(
            "GET",
            f"http://provider.test:{port}/ok",
            allow_local=True,
            timeout=5,
        )
        assert ok.status_code == 200
        assert ok.json()["ok"] is True
        assert ok.extensions.get("pinned_ip") == "127.0.0.1"
        with pytest.raises(AppError) as raised:
            outbound.outbound_request(
                "GET",
                f"http://provider.test:{port}/jump",
                allow_local=True,
                timeout=5,
            )
        assert "blocked" in raised.value.message.lower() or "metadata" in raised.value.message.lower() or "169.254" in raised.value.message.lower() or "not an allowed" in raised.value.message.lower() or "network address" in raised.value.message.lower()
    finally:
        server.shutdown()


def test_zip_slip(tmp_path):
    archive_path = tmp_path / "slip.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../evil.txt", "nope")
    with pytest.raises(Exception):
        safe_extract_zip(archive_path, tmp_path / "out")
    assert not (tmp_path.parent / "evil.txt").exists()
    record("SRC-002", "SRC-002-AC-02", "passed", "The archive member ../evil.txt was rejected.")


def test_ai_provider_connection(client):
    auth(client)
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.endswith("/fail/models"):
                body = b"{}"
                status = 401
            else:
                body = b'{"data":[{"id":"fixture-model"}]}'
                status = 200
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    key = "sk-test-key-should-not-leak"
    saved = client.post(
        "/api/v1/ai-providers",
        json={"provider": "openai_compatible", "api_key": key, "endpoint": f"http://127.0.0.1:{port}/v1", "model": "fixture-model"},
    )
    assert saved.status_code == 201, saved.text
    assert key not in saved.text
    assert saved.json()["status"] == "untested"
    record("AI-001", "AI-001-AC-01", "passed", "The API key is not in the save response.")
    failed = client.post(
        "/api/v1/ai-providers",
        json={"provider": "openai", "api_key": key, "endpoint": f"http://127.0.0.1:{port}/fail", "model": "fixture-model"},
    )
    # The OpenAI provider id is distinct. Point its endpoint at the 401 route.
    assert failed.status_code == 201, failed.text
    bad = client.post(f"/api/v1/ai-providers/{failed.json()['id']}/test")
    assert bad.status_code == 200
    assert bad.json()["status"] == "failed"
    record("AI-002", "AI-002-AC-01", "passed", "HTTP 401 leaves the provider failed rather than connected.")
    good = client.post(f"/api/v1/ai-providers/{saved.json()['id']}/test")
    assert good.json()["status"] == "connected", good.text
    record("AI-002", "AI-002-AC-02", "passed", "A model list marks the provider connected.")
    server.shutdown()


def test_failed_job_and_retry(client):
    auth(client)
    project_id = project(client)
    started = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "analysis"})
    assert started.status_code == 201, started.text
    assert started.json()["status"] == "failed"
    assert started.json()["status"] != "completed"
    record("JOB-001", "JOB-001-AC-01", "passed", started.json()["error_detail"])
    retried = client.post(f"/api/v1/jobs/{started.json()['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["retry_count"] == 1
    assert retried.json()["status"] == "failed"
    record("JOB-002", "JOB-002-AC-01", "passed", "Retry ran again and the job remained failed.")


def test_pause_before_execution():
    db = SessionLocal()
    organization = Organization(name="Pause")
    user = User(email="pause@example.com", password_hash=hash_password(PASSWORD), display_name="Pause")
    db.add(organization)
    db.add(user)
    db.flush()
    db.add(Membership(organization_id=organization.id, user_id=user.id, role="owner"))
    from app.models import Project

    item = Project(organization_id=organization.id, name="Paused", settings={}, created_by=user.id)
    db.add(item)
    db.flush()
    job = Job(
        organization_id=organization.id,
        project_id=item.id,
        job_type="analysis",
        status="queued",
        pause_requested=True,
        created_by=user.id,
    )
    db.add(job)
    db.commit()
    job_id = job.id
    db.close()
    execute_job(job_id)
    db = SessionLocal()
    stored = db.get(Job, job_id)
    assert stored.status == "paused"
    assert stored.progress == 0
    db.close()
    record("JOB-003", "JOB-003-AC-01", "passed", "The job paused before doing work.")


def test_verification_rules():
    from app.models import AcceptanceCriterion, Requirement
    from app.verification.engine import record_execution

    db = SessionLocal()
    failed = Requirement(
        requirement_key="TMP-FAIL",
        title="Temporary failure",
        description="Used to prove a failed criterion cannot be verified.",
        scope="platform",
        status="not_started",
    )
    passed = Requirement(
        requirement_key="TMP-PASS",
        title="Temporary pass",
        description="Used to prove all mandatory criteria must pass.",
        scope="platform",
        status="not_started",
    )
    db.add(failed)
    db.add(passed)
    db.flush()
    db.add(AcceptanceCriterion(requirement_id=failed.id, criterion_key="TMP-FAIL-AC-01", description="Must fail this check.", requires_evidence=True))
    db.add(AcceptanceCriterion(requirement_id=passed.id, criterion_key="TMP-PASS-AC-01", description="First check.", requires_evidence=True))
    db.add(AcceptanceCriterion(requirement_id=passed.id, criterion_key="TMP-PASS-AC-02", description="Second check.", requires_evidence=True))
    db.commit()
    record_execution(
        db,
        requirement_key="TMP-FAIL",
        criterion_key="TMP-FAIL-AC-01",
        result="failed",
        actual_result="The check failed.",
        environment="pytest",
        evidence_summary="failure evidence",
        error_detail="expected failure",
    )
    db.commit()
    db.refresh(failed)
    assert failed.status == "failed"
    record_execution(
        db,
        requirement_key="TMP-PASS",
        criterion_key="TMP-PASS-AC-01",
        result="passed",
        actual_result="First check passed.",
        environment="pytest",
        evidence_summary="first",
    )
    db.commit()
    db.refresh(passed)
    assert passed.status != "verified"
    record_execution(
        db,
        requirement_key="TMP-PASS",
        criterion_key="TMP-PASS-AC-02",
        result="passed",
        actual_result="Second check passed.",
        environment="pytest",
        evidence_summary="second",
    )
    db.commit()
    db.refresh(passed)
    assert passed.status == "verified"
    db.close()
    record("VER-001", "VER-001-AC-01", "passed", "The failed requirement stayed failed.")
    record("VER-001", "VER-001-AC-02", "passed", "Verified was withheld until every mandatory criterion passed.")


def test_organization_isolation(client):
    auth(client)
    project_id = project(client)
    db = SessionLocal()
    other_org = Organization(name="Other")
    other = User(email="other@example.com", password_hash=hash_password(PASSWORD), display_name="Other")
    db.add(other_org)
    db.add(other)
    db.flush()
    db.add(Membership(organization_id=other_org.id, user_id=other.id, role="owner"))
    token = new_session_token()
    db.add(SessionToken(user_id=other.id, token_hash=hash_token(token), expires_at=__import__("app.api.deps", fromlist=["session_expiry"]).session_expiry()))
    db.commit()
    db.close()
    client.cookies.set("session", token)
    response = client.get(f"/api/v1/projects/{project_id}")
    assert response.status_code == 404
    record("SEC-001", "SEC-001-AC-01", "passed", "The other organization received 404.")


def test_analysis_documents_video_and_changes(client):
    auth(client)
    project_id = project(client)
    server = serve_site()
    host, port = server.server_address
    source = client.post(
        f"/api/v1/projects/{project_id}/sources",
        json={"kind": "website", "name": "Fixture", "config": {"url": f"http://{host}:{port}/"}},
    )
    assert source.status_code == 201, source.text
    upload = client.post(
        f"/api/v1/projects/{project_id}/sources/upload",
        data={"kind": "zip", "name": "Code"},
        files={"upload": ("code.zip", zip_bytes(FIXTURE, {"secret.txt": f"API_KEY={SECRET}\n"}), "application/zip")},
    )
    assert upload.status_code == 201, upload.text
    settings = client.put(
        f"/api/v1/projects/{project_id}/settings",
        json={"settings": {**client.get(f"/api/v1/projects/{project_id}").json()["settings"], "enable_video": True}},
    )
    assert settings.status_code == 200, settings.text
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "analysis"})
    assert job.status_code == 201, job.text
    body = job.json()
    assert body["status"] == "completed", body
    pages = client.get(f"/api/v1/projects/{project_id}/pages").json()["pages"]
    assert any(page["title"] == "Demo" for page in pages)
    assert any(page["title"] == "Features" for page in pages)
    assert all("example.com" not in page["url"] for page in pages)
    record("DISC-001", "DISC-001-AC-01", "passed", "Demo and Features were stored and the external host was not.")
    shots = client.get(f"/api/v1/projects/{project_id}/screenshots").json()["screenshots"]
    assert shots
    image = client.get(f"/api/v1/screenshots/{shots[0]['id']}/file")
    assert image.status_code == 200
    assert image.content.startswith(b"\x89PNG")
    record("DISC-001", "DISC-001-AC-02", "passed", "The screenshot file is a PNG.")
    run = client.get(f"/api/v1/projects/{project_id}/runs/{body['run_id']}")
    routes = run.json()["code"]["routes"]
    health = next(item for item in routes if item["path"] == "/health")
    assert health["line_start"] == 4
    assert health["file"] == "app.py"
    assert run.json()["code"]["executed"] is False
    record("CODE-001", "CODE-001-AC-01", "passed", "The route line matches the fixture.")
    record("CODE-001", "CODE-001-AC-02", "passed", "executed is false.")
    documents = client.get(f"/api/v1/projects/{project_id}/documents").json()["documents"]
    first_versions = []
    formats = set()
    for document in documents:
        for version in document["versions"]:
            downloaded = client.get(f"/api/v1/document-versions/{version['id']}/file")
            assert downloaded.status_code == 200, downloaded.text
            assert SECRET.encode() not in downloaded.content
            formats.add(version["format"])
            if document["title"].startswith("Getting started"):
                first_versions.append(version["id"])
                if version["format"] in {"markdown", "html"}:
                    assert document["title"] in downloaded.text
                elif version["format"] == "pdf":
                    from pypdf import PdfReader

                    assert document["title"] in "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(downloaded.content)).pages)
                elif version["format"] == "docx":
                    from docx import Document

                    text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(downloaded.content)).paragraphs)
                    assert document["title"] in text
    assert formats == {"markdown", "html", "pdf", "docx"}
    features = client.get(f"/api/v1/projects/{project_id}/features").text
    assert SECRET not in features
    record("SEC-002", "SEC-002-AC-01", "passed", "The fixture secret is absent from documents and features.")
    record("DOC-001", "DOC-001-AC-01", "passed", "All four formats contain the title.")
    videos = client.get(f"/api/v1/projects/{project_id}/videos").json()["videos"]
    assert videos
    assert videos[0]["duration_seconds"] > 0
    media = client.get(f"/api/v1/videos/{videos[0]['id']}/file")
    assert b"ftyp" in media.content[:32]
    record("VID-001", "VID-001-AC-01", "passed", f"Duration {videos[0]['duration_seconds']}")
    replaced = zip_bytes(FIXTURE.replace("/health", "/status"))
    client.delete(f"/api/v1/sources/{upload.json()['id']}")
    uploaded = client.post(
        f"/api/v1/projects/{project_id}/sources/upload",
        data={"kind": "zip", "name": "Code"},
        files={"upload": ("code.zip", replaced, "application/zip")},
    )
    assert uploaded.status_code == 201
    second = client.post(f"/api/v1/projects/{project_id}/rescan")
    assert second.status_code == 201, second.text
    assert second.json()["status"] == "completed", second.json()
    kept = client.get(f"/api/v1/document-versions/{first_versions[0]}/file")
    assert kept.status_code == 200
    record("DOC-002", "DOC-002-AC-01", "passed", "The first document version is still downloadable.")
    changes = client.get(f"/api/v1/projects/{project_id}/changes").json()["changes"]
    assert any("app.py" in change["summary"].get("changed_files", []) for change in changes)
    record("CHG-001", "CHG-001-AC-01", "passed", "The changed app.py file was reported.")
    server.shutdown()


def test_git_repository(tmp_path, client):
    auth(client)
    project_id = project(client, "Git product")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "main.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    client.put(
        f"/api/v1/projects/{project_id}/settings",
        json={"settings": {**client.get(f"/api/v1/projects/{project_id}").json()["settings"], "analysis_mode": "code_only", "enable_video": False}},
    )
    source = client.post(
        f"/api/v1/projects/{project_id}/sources",
        json={"kind": "git", "name": "Local", "config": {"url": str(repo)}},
    )
    assert source.status_code == 201, source.text
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "analysis"})
    assert job.status_code == 201, job.text
    assert job.json()["status"] == "completed", job.json()
    code = client.get(f"/api/v1/projects/{project_id}/runs/{job.json()['run_id']}").json()["code"]
    assert code["commits"] and len(code["commits"][0]) >= 7
    assert code["executed"] is False
    record("CODE-001", "CODE-001-AC-03", "passed", "The local repository commit was recorded without executing it.")


def test_wordpress_publish(client):
    auth(client)
    project_id = project(client, "Publish product")
    calls = {"posts": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"id": 7}'
            self._send(200, body)

        def do_POST(self):  # noqa: N802
            calls["posts"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            self._send(201, b'{"id": 9, "status": "draft", "link": "http://127.0.0.1/draft"}')

        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    client.put(
        f"/api/v1/projects/{project_id}/settings",
        json={"settings": {**client.get(f"/api/v1/projects/{project_id}").json()["settings"], "analysis_mode": "code_only"}},
    )
    upload = client.post(
        f"/api/v1/projects/{project_id}/sources/upload",
        data={"kind": "zip", "name": "Code"},
        files={"upload": ("code.zip", zip_bytes(FIXTURE), "application/zip")},
    )
    assert upload.status_code == 201
    job = client.post(f"/api/v1/projects/{project_id}/jobs", json={"job_type": "analysis"})
    assert job.json()["status"] == "completed", job.text
    documents = client.get(f"/api/v1/projects/{project_id}/documents").json()["documents"]
    version_id = next(version["id"] for document in documents for version in document["versions"] if version["format"] == "markdown")
    destination = client.post(
        "/api/v1/publishing/destinations",
        json={
            "site_url": f"http://127.0.0.1:{port}",
            "username": "editor",
            "application_password": "application-password",
            "project_id": project_id,
        },
    )
    assert destination.status_code == 201, destination.text
    assert "application-password" not in destination.text
    tested = client.post(f"/api/v1/publishing/destinations/{destination.json()['id']}/test")
    assert tested.json()["status"] == "connected", tested.text
    published = client.post(
        "/api/v1/publishing/posts",
        json={"destination_id": destination.json()["id"], "document_version_id": version_id, "status": "draft"},
    )
    assert published.status_code == 201, published.text
    assert published.json()["status"] == "draft"
    assert published.json()["remote_id"] == "9"
    record("PUB-001", "PUB-001-AC-01", "passed", "WordPress confirmed the draft.")
    again = client.post(
        "/api/v1/publishing/posts",
        json={"destination_id": destination.json()["id"], "document_version_id": version_id, "status": "draft"},
    )
    assert again.status_code == 201
    assert calls["posts"] == 1
    record("PUB-001", "PUB-001-AC-02", "passed", "The second request reused the publication record.")
    server.shutdown()


def test_readiness(client):
    response = client.get("/health/ready")
    assert response.status_code == 200, response.text
    checks = response.json()["checks"]
    assert checks["database"] == "ok"
    assert checks["redis"] == "ok"
    assert checks["storage"] == "ok"
    record("HEALTH-001", "HEALTH-001-AC-01", "passed", json.dumps(checks))


def test_member_cannot_administer(client):
    auth(client)
    created = client.post(
        "/api/v1/users",
        json={"email": "member@example.com", "password": "member-password-1", "display_name": "Member", "role": "member"},
    )
    assert created.status_code == 201, created.text
    client.post("/api/v1/auth/logout")
    logged = client.post("/api/v1/auth/login", json={"email": "member@example.com", "password": "member-password-1"})
    assert logged.status_code == 200
    denied = client.post(
        "/api/v1/users",
        json={"email": "another@example.com", "password": "member-password-1", "display_name": "Another", "role": "member"},
    )
    assert denied.status_code == 403
