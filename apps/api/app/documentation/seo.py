"""SEO articles from a supplied brief. Template output is never labeled as model output."""

from __future__ import annotations

import json
import re

from app.core.errors import AppError
from app.core.redact import redact
from app.integrations.ai import GenerationResult

REQUIRED = ("title", "seo_title", "meta_description", "slug", "h1", "body")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "article"


def template_article(brief: dict) -> dict:
    keyword = str(brief["primary_keyword"]).strip()
    topic = str(brief["topic"]).strip()
    notes = redact(str(brief.get("content_brief") or "").strip())
    secondary = [str(item).strip() for item in brief.get("secondary_keywords") or [] if str(item).strip()]
    intent = str(brief.get("search_intent") or "informational")
    title = topic[:180]
    return {
        "generation_mode": "template_fallback",
        "provider": "",
        "model": "",
        "fallback_provider": "",
        "fallback_model": "",
        "fallback_reason": "No connected AI provider was used.",
        "title": title,
        "primary_keyword": keyword,
        "secondary_keywords": secondary,
        "search_intent": intent,
        "seo_title": title[:70],
        "meta_description": (notes or f"Notes supplied for {keyword}.")[:160],
        "slug": slugify(keyword),
        "h1": title,
        "headings": [title],
        "body": "\n".join(
            [
                "This draft repeats only the brief that was submitted. It is not model-written content.",
                f"Topic: {topic}",
                f"Primary keyword: {keyword}",
                f"Brief: {notes or 'No additional brief was supplied.'}",
            ]
        ),
        "faqs": _faqs_from_brief(notes),
        "image_alt": "",
        "internal_links": [],
        "fact_type": "template_fallback",
    }


def article_from_model(result: GenerationResult, brief: dict) -> dict:
    try:
        payload = json.loads(_json_text(result.text))
    except json.JSONDecodeError as exc:
        raise AppError(502, "provider_error", "The model did not return a JSON article.") from exc
    if not isinstance(payload, dict):
        raise AppError(502, "provider_error", "The model article was not an object.")
    missing = [key for key in REQUIRED if not str(payload.get(key) or "").strip()]
    if missing:
        raise AppError(502, "provider_error", "The model article is missing: " + ", ".join(missing))
    keyword = str(brief["primary_keyword"]).strip()
    article = {
        "generation_mode": "ai",
        "provider": result.provider,
        "model": result.model,
        "fallback_provider": result.fallback_provider,
        "fallback_model": result.fallback_model,
        "fallback_reason": result.fallback_reason,
        "title": redact(str(payload["title"]))[:180],
        "primary_keyword": keyword,
        "secondary_keywords": [str(item) for item in payload.get("secondary_keywords") or brief.get("secondary_keywords") or []][:12],
        "search_intent": str(payload.get("search_intent") or brief.get("search_intent") or ""),
        "seo_title": redact(str(payload["seo_title"]))[:70],
        "meta_description": redact(str(payload["meta_description"]))[:160],
        "slug": slugify(str(payload.get("slug") or keyword)),
        "h1": redact(str(payload["h1"]))[:180],
        "headings": [str(item) for item in payload.get("headings") or []][:12],
        "body": redact(str(payload["body"]))[:20000],
        "faqs": payload.get("faqs") if isinstance(payload.get("faqs"), list) else [],
        "image_alt": redact(str(payload.get("image_alt") or ""))[:180],
        "internal_links": [str(item) for item in payload.get("internal_links") or []][:12],
        "fact_type": "inference",
    }
    article["body"] = article["body"] + "\n\nSources were not added beyond the supplied brief and observed project data."
    return article


def prompt_for(brief: dict, observed_links: list[str]) -> str:
    return json.dumps(
        {
            "task": "Write an SEO article JSON object. Do not invent sources, customers, or statistics.",
            "keys": list(REQUIRED) + ["secondary_keywords", "search_intent", "headings", "faqs", "image_alt", "internal_links"],
            "brief": {
                "primary_keyword": brief["primary_keyword"],
                "secondary_keywords": brief.get("secondary_keywords") or [],
                "topic": brief["topic"],
                "content_brief": str(brief.get("content_brief") or "")[:4000],
                "search_intent": brief.get("search_intent") or "",
            },
            "allowed_internal_links": observed_links[:20],
        }
    )


def _faqs_from_brief(notes: str) -> list[dict]:
    faqs = []
    for line in notes.splitlines():
        if line.lower().startswith("q:") and " a:" in line.lower():
            question, answer = line.split(" A:", 1) if " A:" in line else line.split(" a:", 1)
            faqs.append({"question": question[2:].strip(), "answer": answer.strip()})
    return faqs[:6]


def _json_text(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if "```" in text:
            text = text[: text.rfind("```")]
    return text.strip()
