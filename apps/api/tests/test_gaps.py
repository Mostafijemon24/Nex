"""Coverage for provider adapters, isolation, SEO, accounts, and schedules."""

from __future__ import annotations

import base64
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from sqlalchemy import select, text

from app.core.errors import AppError
from app.db.session import SessionLocal, engine
from app.integrations.ai import AIClient, generate_with_policy
from app.integrations.providers import (
    AnthropicAdapter,
    GeminiAdapter,
    ModelInfo,
    OpenAIAdapter,
    OpenRouterAdapter,
    OxionAdapter,
    billing_from_pricing,
    classify_http,
    get_adapter,
    resolve_endpoint,
)
from app.integrations.tts import synthesize
from app.models import Job, MonitorSchedule, Project
from app.models.entities import utcnow
from app.services.monitors import run_due
from app.video.render import render_slideshow
from tests.conftest import assert_isolated_test_database, record
from tests.test_platform import auth, project

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_non_ascii_api_key_rejected():
    from app.core.errors import AppError
    from app.core.security import require_http_secret, secret_hint
    from app.integrations.providers import OpenRouterAdapter

    hint = secret_hint("abcdয়না")
    assert hint.startswith("****")
    assert hint.isascii()
    assert "য়" not in hint
    with pytest.raises(AppError):
        require_http_secret("bad-কী-value")
    with pytest.raises(AppError):
        OpenRouterAdapter().headers("bad-কী-value")


def test_provider_adapters_and_pricing(monkeypatch):
    seen: list[tuple] = []

    def fake(method, url, *, headers, body, timeout, allow_local):
        seen.append((method, url, headers))
        if "generativelanguage" in url:
            payload = {"models": [{"name": "models/gemini-2.0-flash"}]}
        elif "/messages" in url:
            payload = {"content": [{"type": "text", "text": "anthropic-ok"}]}
        elif "oxionai.com" in url and method == "POST":
            payload = {"choices": [{"message": {"content": "oxion-ok"}}]}
        elif "oxionai.com" in url:
            payload = {
                "data": [
                    {"id": "llama-3.1-8b", "pricing": {"input_per_1k": "0", "output_per_1k": "0"}},
                    {"id": "gemma-2-2b", "pricing": {"input_per_1k": "0.01", "output_per_1k": "0.02"}},
                ]
            }
        else:
            payload = {
                "data": [
                    {"id": "vendor/example:free", "pricing": {"prompt": "0", "completion": "0"}},
                    {"id": "vendor/example:free", "pricing": {}},
                ]
            }
        return httpx.Response(200, json=payload)

    monkeypatch.setattr("app.integrations.providers.http_request", fake)
    gemini = GeminiAdapter().test(endpoint="https://generativelanguage.googleapis.com/v1beta", api_key="gem-key", model="", allow_local=False)
    openai = OpenAIAdapter().test(endpoint="https://api.openai.com/v1", api_key="sk-test", model="", allow_local=False)
    oxion = OxionAdapter().test(endpoint="https://www.oxionai.com/v1", api_key="cc_test_key_fixture_not_real", model="", allow_local=False)
    assert oxion.ok
    assert OxionAdapter().prefer_model(oxion.models) == "gemma-2-2b"
    assert get_adapter("oxion").label == "Oxion AI"
    assert resolve_endpoint("oxion", "https://evil.example/v1", allow_local=False) == "https://www.oxionai.com/v1"
    oxion_text = OxionAdapter().complete(
        endpoint="https://www.oxionai.com/v1",
        api_key="cc_test_key_fixture_not_real",
        model="gemma-2-2b",
        prompt="Say ok",
        allow_local=False,
    )
    assert oxion_text == "oxion-ok"
    anthropic_text = AnthropicAdapter().complete(
        endpoint="https://api.anthropic.com/v1",
        api_key="anth-key",
        model="claude-3-5-haiku-latest",
        prompt="Say ok",
        allow_local=False,
    )
    assert anthropic_text == "anthropic-ok"
    assert any("/messages" in item[1] for item in seen)
    assert seen[0][2]["x-goog-api-key"] == "gem-key"
    assert "Authorization" not in seen[0][2]
    assert seen[1][2]["Authorization"] == "Bearer sk-test"
    assert gemini.ok and openai.ok
    priced = {"id": "vendor/example:free", "pricing": {"prompt": "0", "completion": "0"}}
    unlabeled = {"id": "vendor/example:free"}
    assert billing_from_pricing(priced) == "free"
    assert billing_from_pricing(unlabeled) == "unknown"
    assert billing_from_pricing({"id": "x", "pricing": {"prompt": "0", "completion": "0.01"}}) == "paid"
    assert billing_from_pricing({"id": "y", "pricing": {"input_per_1k": "0", "output_per_1k": "0"}}) == "free"
    assert classify_http(402, "insufficient_funds")[0] == "quota"
    assert classify_http(503, '{"error":{"code":"service_upgrading","type":"maintenance"}}') == ("provider_outage", True)
    record("AI-003", "AI-003-AC-01", "passed", "Gemini and OpenAI requests use different credential headers.")
    record("AI-003", "AI-003-AC-02", "passed", "Free billing requires zero pricing fields. The model id is ignored.")
    assert resolve_endpoint("openrouter", "https://evil.example/v1", allow_local=False) == "https://openrouter.ai/api/v1"
    assert resolve_endpoint("gemini", "https://evil.example/v1", allow_local=False) == "https://generativelanguage.googleapis.com/v1beta"
    paid_and_free = [
        ModelInfo(id="paid/model", billing="paid"),
        ModelInfo(id="openrouter/free-model", billing="free"),
    ]
    assert OpenRouterAdapter().prefer_model(paid_and_free) == "openrouter/free-model"
    with_router = [
        ModelInfo(id="paid/model", billing="paid"),
        ModelInfo(id="vendor/chat:free", billing="free"),
        ModelInfo(id="openrouter/free", billing="free", name="Free Models Router"),
    ]
    assert OpenRouterAdapter().prefer_model(with_router) == "openrouter/free"
    assert OpenRouterAdapter().free_fallback_ids(with_router, "vendor/chat:free") == []
    assert OpenRouterAdapter().free_fallback_ids(
        [
            ModelInfo(id="a:free", billing="free"),
            ModelInfo(id="b:free", billing="free"),
            ModelInfo(id="paid", billing="paid"),
            ModelInfo(id="openrouter/free", billing="free"),
        ],
        "a:free",
    ) == ["b:free"]
    record("AI-006", "AI-006-AC-01", "passed", "Oxion AI uses the official base URL and prefers gemma-2-2b when listed.")


def test_oxion_connect_flow(client, monkeypatch):
    def fake(method, url, *, headers, body, timeout, allow_local):
        assert "oxionai.com" in url
        assert headers.get("Authorization", "").startswith("Bearer ")
        key = headers["Authorization"].removeprefix("Bearer ")
        assert key.isascii()
        if key == "cc_invalid_fixture_key_xxxxxxxxxxxxxxxxxxxxxxxx":
            return httpx.Response(401, json={"error": {"message": "Missing or invalid API key.", "code": "invalid_api_key"}})
        if method == "POST" and "chat/completions" in url:
            article = {
                "title": "Setup",
                "seo_title": "Setup",
                "meta_description": "Install the app.",
                "slug": "docs",
                "h1": "Setup",
                "body": "Use only the supplied brief.",
            }
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(article)}}]})
        return httpx.Response(
            200,
            json={"data": [{"id": "gemma-2-2b", "pricing": {"input_per_1k": "0", "output_per_1k": "0"}}]},
        )

    monkeypatch.setattr("app.integrations.providers.http_request", fake)
    auth(client)
    bad = client.post(
        "/api/v1/ai-providers/connect",
        json={"provider": "oxion", "api_key": "cc_invalid_fixture_key_xxxxxxxxxxxxxxxxxxxxxxxx"},
    )
    assert bad.status_code == 200, bad.text
    assert bad.json()["status"] == "failed"
    assert bad.json()["connected"] is False
    assert bad.json()["error_category"] == "invalid_credentials"
    assert "cc_invalid" not in bad.text
    stale = client.post(
        "/api/v1/ai-providers/connect",
        json={
            "provider": "oxion",
            "display_name": "Oxion AI",
            "api_key": "cc_valid_fixture_key_xxxxxxxxxxxxxxxxxxxxxxxxxx",
            "model": "prs-admin@example.com",
        },
    )
    assert stale.status_code == 200, stale.text
    body = stale.json()
    assert body["connected"] is True
    assert body["status"] == "connected"
    assert body["provider"] == "oxion"
    assert body["endpoint"] == "https://www.oxionai.com/v1"
    assert body["model"] == "gemma-2-2b"
    assert body["model"] != "prs-admin@example.com"
    assert body["models"][0]["id"] == "gemma-2-2b"
    assert "cc_valid" not in stale.text
    good = stale
    project_id = project(client)
    generated = client.post(
        f"/api/v1/projects/{project_id}/seo-articles",
        json={"primary_keyword": "docs", "topic": "Oxion", "provider_id": body["id"]},
    )
    assert generated.status_code == 201, generated.text
    assert generated.json()["article"]["generation_mode"] == "ai"
    record("AI-006", "AI-006-AC-02", "passed", "Oxion Connect validates the key, auto-selects a model, and generates content.")
    record("AI-006", "AI-006-AC-03", "passed", "Invalid Oxion keys fail clearly without HTTP 500 or key leakage.")


def test_openrouter_and_gemini_connect_prefers_free(client, monkeypatch):
    def fake(method, url, *, headers, body, timeout, allow_local):
        if "generativelanguage" in url and method == "POST":
            article = {
                "title": "Setup",
                "seo_title": "Setup",
                "meta_description": "Install the app.",
                "slug": "docs",
                "h1": "Setup",
                "body": "Use only the supplied brief.",
            }
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(article)}]}}]})
        if "generativelanguage" in url:
            return httpx.Response(200, json={"models": [{"name": "models/gemini-1.5-pro"}, {"name": "models/gemini-2.0-flash"}]})
        if "openrouter.ai" in url and method == "POST":
            article = {
                "title": "Setup",
                "seo_title": "Setup",
                "meta_description": "Install the app.",
                "slug": "docs",
                "h1": "Setup",
                "body": "Use only the supplied brief.",
            }
            return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(article)}}]})
        if "openrouter.ai" in url:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "openai/gpt-4o", "pricing": {"prompt": "0.01", "completion": "0.03"}},
                        {
                            "id": "meta-llama/llama-3.2-3b-instruct:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                            "architecture": {"output_modalities": ["text"]},
                        },
                        {
                            "id": "google/gemma-2-9b-it:free",
                            "pricing": {"prompt": "0", "completion": "0"},
                            "architecture": {"output_modalities": ["text"]},
                        },
                        {
                            "id": "openrouter/free",
                            "pricing": {"prompt": "0", "completion": "0"},
                            "architecture": {"output_modalities": ["text"]},
                            "name": "Free Models Router",
                        },
                    ]
                },
            )
        raise AssertionError(url)

    monkeypatch.setattr("app.integrations.providers.http_request", fake)
    auth(client)
    router = client.post(
        "/api/v1/ai-providers/connect",
        json={"provider": "openrouter", "api_key": "sk-or-test-fixture-key-xxxxxxxxxxxxxxxxxx", "model": "prs-admin@example.com"},
    )
    assert router.status_code == 200, router.text
    body = router.json()
    assert body["connected"] is True
    assert body["endpoint"] == "https://openrouter.ai/api/v1"
    assert body["model"] == "openrouter/free"
    assert body["free_model_count"] >= 3
    assert any(item["id"] == "meta-llama/llama-3.2-3b-instruct:free" for item in body["free_models"])
    assert "sk-or-test" not in router.text
    selected = client.post(
        f"/api/v1/ai-providers/{body['id']}/model",
        json={"model": "google/gemma-2-9b-it:free"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["model"] == "google/gemma-2-9b-it:free"
    paid_blocked = client.post(
        f"/api/v1/ai-providers/{body['id']}/model",
        json={"model": "openai/gpt-4o"},
    )
    assert paid_blocked.status_code == 422
    gemini = client.post(
        "/api/v1/ai-providers/connect",
        json={"provider": "gemini", "api_key": "AIza-test-fixture-key-xxxxxxxxxxxxxxxxxx"},
    )
    assert gemini.status_code == 200, gemini.text
    gem = gemini.json()
    assert gem["connected"] is True
    assert gem["endpoint"] == "https://generativelanguage.googleapis.com/v1beta"
    assert gem["model"] == "gemini-2.0-flash"
    project_id = project(client)
    generated = client.post(
        f"/api/v1/projects/{project_id}/seo-articles",
        json={"primary_keyword": "docs", "topic": "Gemini", "provider_id": gem["id"]},
    )
    assert generated.status_code == 201, generated.text
    assert generated.json()["article"]["generation_mode"] == "ai"
    assert generated.json()["article"]["provider"] == "gemini"
    auto = client.post(
        f"/api/v1/projects/{project_id}/seo-articles",
        json={"primary_keyword": "docs", "topic": "Auto"},
    )
    assert auto.status_code == 201, auto.text
    assert auto.json()["article"]["generation_mode"] == "ai"
    record("AI-006", "AI-006-AC-04", "passed", "OpenRouter Connect prefers a zero-price model; Gemini Connect prefers flash.")


def test_provider_url_and_fallback(monkeypatch):
    with pytest.raises(AppError):
        resolve_endpoint("openai_compatible", "http://169.254.169.254/v1", allow_local=True)
    record("AI-004", "AI-004-AC-01", "passed", "The metadata address was rejected.")
    calls: list[str] = []

    def fake_complete(self, prompt: str) -> str:
        calls.append(self.provider)
        raise AppError(502, "provider_error", "down", retryable=True, detail={"error_category": "provider_outage"})

    monkeypatch.setattr("app.integrations.ai.resolve_endpoint", lambda provider, endpoint, allow_local: endpoint or "https://example.test/v1")
    monkeypatch.setattr(AIClient, "complete_text", fake_complete)
    primary = AIClient("openai", "sk-test", "https://example.test/v1", "gpt-test", allow_local=True)
    fallback = AIClient("openrouter", "sk-test", "https://example.test/v1", "other", allow_local=True)
    with pytest.raises(AppError):
        generate_with_policy(primary, "hello", allow_paid_fallback=False, fallback=fallback)
    assert calls == ["openai"]
    record("AI-005", "AI-005-AC-01", "passed", "The fallback provider was not called.")


def test_test_database_name():
    with engine.connect() as connection:
        name = connection.execute(text("SELECT current_database()")).scalar()
    assert name == assert_isolated_test_database(f"postgresql://docs:docs@localhost/{name}")
    record("DATA-001", "DATA-001-AC-01", "passed", f"Tests are connected to {name}.")


def test_verification_import_does_not_change_rows(client):
    auth(client)
    before = client.get("/api/v1/requirements?scope=platform").json()["requirements"]
    status = {row["key"]: row["status"] for row in before}
    imported = client.post("/api/v1/verification-runs/import")
    assert imported.status_code == 201, imported.text
    assert imported.json()["authoritative_for_current_code"] is False
    after = client.get("/api/v1/requirements?scope=platform").json()["requirements"]
    assert {row["key"]: row["status"] for row in after} == status
    record("VER-002", "VER-002-AC-01", "passed", "Import stored a run without changing requirement status.")


def test_seo_template_and_mock(client):
    auth(client)
    project_id = project(client)
    blocked = client.post(
        f"/api/v1/projects/{project_id}/seo-articles",
        json={"primary_keyword": "docs", "topic": "Setup", "content_brief": "Install the app."},
    )
    assert blocked.status_code == 422, blocked.text
    saved = client.post(
        f"/api/v1/projects/{project_id}/seo-articles",
        json={"primary_keyword": "docs", "topic": "Setup", "content_brief": "Install the app.", "secondary_keywords": ["guide"], "allow_template": True},
    )
    assert saved.status_code == 201, saved.text
    article = saved.json()["article"]
    assert article["generation_mode"] == "template_fallback"
    assert article["slug"] == "docs"
    assert article["primary_keyword"] == "docs"
    assert "not model-written" in article["body"]
    record("SEO-001", "SEO-001-AC-01", "passed", "Template output is labeled and keeps the submitted keyword.")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"data":[{"id":"fixture-model"}]}'
            self._send(200, body)

        def do_POST(self):  # noqa: N802
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Setup",
                                    "seo_title": "Setup",
                                    "meta_description": "Install the app.",
                                    "slug": "docs",
                                    "h1": "Setup",
                                    "body": "Use only the supplied brief.",
                                }
                            )
                        }
                    }
                ]
            }
            self._send(200, json.dumps(payload).encode())

        def _send(self, status: int, body: bytes) -> None:
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
    provider = client.post(
        "/api/v1/ai-providers",
        json={"provider": "openai_compatible", "display_name": "Fixture AI", "api_key": "sk-fixture", "endpoint": f"http://127.0.0.1:{port}/v1", "model": "fixture-model"},
    )
    assert provider.status_code == 201, provider.text
    tested = client.post(f"/api/v1/ai-providers/{provider.json()['id']}/test")
    assert tested.json()["status"] == "connected", tested.text
    generated = client.post(
        f"/api/v1/projects/{project_id}/seo-articles",
        json={"primary_keyword": "docs", "topic": "Setup", "provider_id": provider.json()["id"]},
    )
    assert generated.status_code == 201, generated.text
    assert generated.json()["article"]["generation_mode"] == "ai"
    assert "sk-fixture" not in generated.text
    record("SEO-001", "SEO-001-AC-02", "passed", "The article is labeled ai. The test used a local mock, not a live vendor.")
    server.shutdown()


def test_voice_and_slideshow_labels(tmp_path):
    import subprocess

    silent = synthesize(provider="", api_key="", endpoint="", model="", voice="", text="Hello", allow_local=True)
    assert silent["audio"] is None
    assert silent["voiceover"] is False
    record("TTS-001", "TTS-001-AC-01", "passed", silent["limitation"])
    rendered = render_slideshow([("frame", PNG)], "Opened the page", voiceover_requested=True)
    assert rendered["metadata"]["mode"] == "screenshot_slideshow"
    assert rendered["metadata"]["interaction_recording"] is False
    assert rendered["metadata"]["voiceover"] is False
    assert rendered["has_audio"] is False
    record("VID-002", "VID-002-AC-01", "passed", rendered["metadata"]["note"])
    frame = tmp_path / "frame.png"
    frame.write_bytes(PNG)
    recording = tmp_path / "clip.mp4"
    built = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-pix_fmt",
            "yuv420p",
            str(recording),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr[-500:]
    _ = frame
    from_recording = render_slideshow([], "Uploaded clip", voiceover_requested=False, recording=recording.read_bytes())
    assert from_recording["metadata"]["mode"] == "uploaded_recording"
    assert from_recording["metadata"]["interaction_recording"] is False
    assert from_recording["duration"] > 0
    assert b"ftyp" in from_recording["video"][:32]


def test_multiple_accounts(client):
    auth(client)
    project_id = project(client)
    source = client.post(
        f"/api/v1/projects/{project_id}/sources",
        json={"kind": "website", "name": "Site", "config": {"url": "https://example.com"}},
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    first = client.post(f"/api/v1/sources/{source_id}/accounts", json={"username": "ada", "password": "first-password-value", "role": "admin"})
    second = client.post(f"/api/v1/sources/{source_id}/accounts", json={"username": "grace", "password": "second-password-value", "role": "editor"})
    assert second.status_code == 201, second.text
    body = second.text
    assert "first-password-value" not in body
    assert "second-password-value" not in body
    assert [item["username"] for item in second.json()["config"]["accounts"]] == ["ada", "grace"]
    assert first.json()["config"]["accounts"][0]["role"] == "admin"
    record("ACC-001", "ACC-001-AC-01", "passed", "Two accounts are stored without their passwords.")


def test_monitor_does_not_duplicate(client):
    auth(client)
    project_id = project(client)
    saved = client.put(f"/api/v1/projects/{project_id}/monitor", json={"enabled": True, "interval_minutes": 15})
    assert saved.status_code == 200, saved.text
    db = SessionLocal()
    try:
        project_row = db.get(Project, project_id)
        row = db.scalar(select(MonitorSchedule).where(MonitorSchedule.project_id == project_id))
        row.next_run_at = utcnow() - timedelta(minutes=1)
        db.add(
            Job(
                organization_id=project_row.organization_id,
                project_id=project_id,
                job_type="rescan",
                status="queued",
                created_by=project_row.created_by,
            )
        )
        db.commit()
    finally:
        db.close()
    assert run_due() == 0
    db = SessionLocal()
    try:
        row = db.scalar(select(MonitorSchedule).where(MonitorSchedule.project_id == project_id))
        assert row.last_status == "skipped_duplicate"
    finally:
        db.close()
    record("MON-001", "MON-001-AC-01", "passed", "The due schedule skipped a second run while a job was queued.")


def test_monitor_queues_when_due(client):
    auth(client)
    project_id = project(client)
    saved = client.put(f"/api/v1/projects/{project_id}/monitor", json={"enabled": True, "interval_minutes": 15})
    assert saved.status_code == 200, saved.text
    db = SessionLocal()
    try:
        row = db.scalar(select(MonitorSchedule).where(MonitorSchedule.project_id == project_id))
        row.next_run_at = utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    assert run_due() == 1
    db = SessionLocal()
    try:
        row = db.scalar(select(MonitorSchedule).where(MonitorSchedule.project_id == project_id))
        jobs = list(db.scalars(select(Job).where(Job.project_id == project_id, Job.job_type == "rescan")).all())
        assert row.last_status == "queued"
        assert jobs
        assert row.next_run_at > utcnow()
    finally:
        db.close()


def test_logout_invalidates_session(client):
    from tests.test_platform import PASSWORD

    auth(client)
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    logged_out = client.post("/api/v1/auth/logout")
    assert logged_out.status_code == 200
    denied = client.get("/api/v1/auth/me")
    assert denied.status_code == 401
    again = client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert again.status_code == 200
