"""Text-to-speech boundary. Missing audio is reported; it is not replaced with silence labeled as speech."""

from __future__ import annotations

from app.core.errors import AppError
from app.integrations.providers import http_request, safe_endpoint

UNAVAILABLE = "No text-to-speech provider is configured. The video stays silent and is not marked as voice-enabled."


def synthesize(
    *,
    provider: str,
    api_key: str,
    endpoint: str,
    model: str,
    voice: str,
    text: str,
    allow_local: bool,
) -> dict:
    if provider not in {"openai", "openai_compatible"} or not api_key or not endpoint:
        return {"audio": None, "voiceover": False, "limitation": UNAVAILABLE, "provider": provider, "model": model, "live": False}
    url = safe_endpoint(endpoint, allow_local=allow_local).rstrip("/") + "/audio/speech"
    body = {"model": model or "tts-1", "voice": voice or "alloy", "input": text[:4000]}
    try:
        response = http_request(
            "POST",
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            body=body,
            timeout=45,
            allow_local=allow_local,
        )
    except AppError as exc:
        return {"audio": None, "voiceover": False, "limitation": exc.message, "provider": provider, "model": body["model"], "live": True}
    if response.status_code >= 400 or not response.content:
        return {
            "audio": None,
            "voiceover": False,
            "limitation": f"Speech synthesis failed with HTTP {response.status_code}. No narration audio was stored.",
            "provider": provider,
            "model": body["model"],
            "live": True,
        }
    return {"audio": response.content, "voiceover": True, "limitation": "", "provider": provider, "model": body["model"], "live": True}
