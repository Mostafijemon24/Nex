"""Render the same structured document to Markdown, HTML, PDF, and DOCX."""

from __future__ import annotations

import hashlib
import html
import zipfile
from io import BytesIO
from pathlib import Path

from app.core.errors import AppError
from app.core.redact import redact

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/ttf-dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


def build_documents(project_name: str, run_summary: dict, requested_types: list[str]) -> list[dict]:
    pages = run_summary.get("pages", [])
    features = run_summary.get("features", [])
    code = run_summary.get("code", {})
    warnings = list(run_summary.get("warnings", []))
    documents: list[dict] = []

    def add(doc_type: str, title: str, sections: list[dict], status: str) -> None:
        if requested_types and doc_type not in requested_types:
            return
        documents.append(
            {
                "doc_type": doc_type,
                "title": title,
                "verification_status": status,
                "sections": sections,
            }
        )

    observed_pages = [page for page in pages if page.get("access_status") == "accessible"]
    add(
        "getting_started",
        f"Getting started with {project_name}",
        [
            section("Purpose", f"This guide describes what the analysis observed for {project_name}.", "observed"),
            section("Audience", "People setting up or evaluating the analyzed product.", "observed"),
            section(
                "Prerequisites",
                "A project with at least one website URL or source archive was analyzed.",
                "observed" if (pages or code) else "blocked",
            ),
            section("Observed pages", _page_lines(observed_pages) or "No accessible pages were captured.", "observed"),
            section("Sources", "Generated from the analysis run stored with this project.", "observed"),
        ],
        "partially_verified" if observed_pages or code else "needs_review",
    )
    add(
        "feature_guide",
        f"{project_name} features",
        [
            section("Purpose", "Features below were derived from observed pages or source references.", "observed"),
            section("Features", _feature_lines(features) or "No features were recorded.", "observed"),
        ],
        "partially_verified" if features else "needs_review",
    )
    if code:
        add(
            "technical",
            f"{project_name} technical notes",
            [
                section("Purpose", "Static source inspection. The archive was not executed.", "source_code"),
                section("Languages", _format_map(code.get("languages", {})), "source_code"),
                section("Frameworks", ", ".join(code.get("frameworks", [])) or "None identified from project files.", "source_code"),
                section("Routes and hooks", _route_lines(code.get("routes", [])) or "No routes were identified.", "source_code"),
                section("Entry points", "\n".join(code.get("entry_points", [])) or "None identified.", "source_code"),
            ],
            "partially_verified",
        )
    if "faq" in requested_types:
        warnings.append("FAQ was not generated because no question-and-answer source was observed.")
    if "troubleshooting" in requested_types:
        errors = run_summary.get("console_errors") or []
        if errors:
            add(
                "troubleshooting",
                f"{project_name} troubleshooting",
                [section("Console errors", "\n".join(errors[:20]), "observed")],
                "needs_review",
            )
        else:
            warnings.append("Troubleshooting guide was not generated because no runtime errors were captured.")
    observed = _page_lines(observed_pages) or "No accessible pages were captured."
    code_note = "Static source findings are included." if code else "No source archive was analyzed for this run."
    languages = _format_map(code.get("languages", {})) if code else "None identified."
    frameworks = ", ".join(code.get("frameworks", [])) if code and code.get("frameworks") else "None identified from project files."
    routes = _route_lines(code.get("routes", [])) if code else ""
    entry_points = "\n".join(code.get("entry_points", [])) if code else ""
    features_text = _feature_lines(features) or "No features were recorded."

    def optional(doc_type: str, title: str, sections: list[dict], status: str) -> None:
        add(doc_type, f"{project_name} {title}", sections, status)

    optional(
        "user_manual",
        "User manual",
        [
            section("Purpose", f"Observed user-facing behavior for {project_name}.", "observed"),
            section("Getting around", observed, "observed"),
            section("Features", features_text, "observed"),
            section("Limits", "This manual only includes pages and features recorded during analysis.", "observed"),
        ],
        "partially_verified" if observed_pages or features else "needs_review",
    )
    optional(
        "tutorial",
        "Tutorial",
        [
            section("Goal", f"Walk through what the analysis could open in {project_name}.", "observed"),
            section("Steps from observed pages", observed, "observed"),
            section("Related features", features_text, "observed"),
        ],
        "partially_verified" if observed_pages else "needs_review",
    )
    optional(
        "architecture",
        "Architecture notes",
        [
            section("Purpose", "Architecture notes limited to static inspection and observed pages.", "observed"),
            section("Languages", languages, "source_code" if code else "blocked"),
            section("Frameworks", frameworks, "source_code" if code else "blocked"),
            section("Entry points", entry_points or "None identified.", "source_code" if code else "blocked"),
            section("Observed pages", observed, "observed"),
        ],
        "partially_verified" if code or observed_pages else "needs_review",
    )
    optional(
        "api_reference",
        "API reference",
        [
            section("Purpose", "Routes and hooks identified from source files. Runtime contracts were not executed.", "source_code" if code else "blocked"),
            section("Routes and hooks", routes or "No routes were identified.", "source_code" if code else "blocked"),
            section("Observed pages", observed, "observed"),
        ],
        "partially_verified" if routes else "needs_review",
    )
    optional(
        "database",
        "Database notes",
        [
            section("Purpose", "Database notes from static source inspection only.", "source_code" if code else "blocked"),
            section("Source findings", code_note, "source_code" if code else "blocked"),
            section("Observed pages", observed, "observed"),
            section("Limit", "Schema dumps and live database access are outside this analysis.", "observed"),
        ],
        "needs_review",
    )
    optional(
        "installation",
        "Installation notes",
        [
            section("Purpose", f"Installation clues observed for {project_name}.", "observed"),
            section("Entry points", entry_points or "None identified.", "source_code" if code else "blocked"),
            section("Languages and frameworks", f"{languages}\n{frameworks}", "source_code" if code else "blocked"),
            section("Observed pages", observed, "observed"),
        ],
        "partially_verified" if code or observed_pages else "needs_review",
    )
    optional(
        "configuration",
        "Configuration notes",
        [
            section("Purpose", "Configuration notes limited to files and pages in this run.", "observed"),
            section("Source", code_note, "source_code" if code else "blocked"),
            section("Observed pages", observed, "observed"),
        ],
        "needs_review",
    )
    optional(
        "developer",
        "Developer notes",
        [
            section("Purpose", "Developer-oriented notes from static analysis.", "source_code" if code else "blocked"),
            section("Languages", languages, "source_code" if code else "blocked"),
            section("Frameworks", frameworks, "source_code" if code else "blocked"),
            section("Routes and hooks", routes or "No routes were identified.", "source_code" if code else "blocked"),
            section("Entry points", entry_points or "None identified.", "source_code" if code else "blocked"),
        ],
        "partially_verified" if code else "needs_review",
    )
    for doc_type, title in (
        ("wordpress_plugin", "WordPress plugin notes"),
        ("wordpress_theme", "WordPress theme notes"),
        ("integration", "Integration notes"),
        ("release_notes", "Release notes"),
    ):
        optional(
            doc_type,
            title,
            [
                section("Purpose", f"{title} for {project_name}, limited to this analysis run.", "observed"),
                section("Observed pages", observed, "observed"),
                section("Source", code_note, "source_code" if code else "blocked"),
                section("Features", features_text, "observed"),
            ],
            "needs_review",
        )
    run_summary["warnings"] = warnings
    return documents


def section(heading: str, body: str, fact_type: str) -> dict:
    return {"heading": heading, "body": redact(body), "fact_type": fact_type}


def render_markdown(document: dict) -> str:
    lines = [f"# {document['title']}", "", f"Verification status: {document['verification_status']}", ""]
    for item in document["sections"]:
        lines.append(f"## {item['heading']}")
        lines.append("")
        lines.append(f"Fact type: {item['fact_type']}")
        lines.append("")
        lines.append(item["body"])
        lines.append("")
    return "\n".join(lines)


def render_html(document: dict) -> str:
    parts = [
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'><title>",
        html.escape(document["title"]),
        "</title></head><body><h1>",
        html.escape(document["title"]),
        "</h1><p>Verification status: ",
        html.escape(document["verification_status"]),
        "</p>",
    ]
    for item in document["sections"]:
        parts.append("<h2>")
        parts.append(html.escape(item["heading"]))
        parts.append("</h2><p><em>Fact type: ")
        parts.append(html.escape(item["fact_type"]))
        parts.append("</em></p><pre>")
        parts.append(html.escape(item["body"]))
        parts.append("</pre>")
    parts.append("</body></html>")
    return "".join(parts)


def render_pdf(document: dict) -> bytes:
    from fpdf import FPDF

    font = next((path for path in FONT_CANDIDATES if Path(path).is_file()), "")
    if not font:
        raise AppError(
            500,
            "rendering_error",
            "PDF generation needs a Unicode font such as fonts-dejavu-core. It is installed in the API container.",
        )
    pdf = FPDF()
    pdf.add_font("Body", "", font)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    def write(text: str, size: int, height: int) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Body", size=size)
        pdf.multi_cell(0, height, _soft_wrap(text), new_x="LMARGIN", new_y="NEXT")

    write(document["title"], 16, 10)
    write(f"Verification status: {document['verification_status']}", 11, 8)
    for item in document["sections"]:
        pdf.ln(2)
        write(item["heading"], 13, 8)
        write(f"Fact type: {item['fact_type']}", 11, 6)
        write(item["body"], 11, 6)
    raw = pdf.output()
    data = bytes(raw)
    if not data.startswith(b"%PDF"):
        raise AppError(500, "rendering_error", "PDF renderer did not produce a PDF file.")
    return data


def render_docx(document: dict) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(document["title"], level=1)
    doc.add_paragraph(f"Verification status: {document['verification_status']}")
    for item in document["sections"]:
        doc.add_heading(item["heading"], level=2)
        doc.add_paragraph(f"Fact type: {item['fact_type']}")
        doc.add_paragraph(item["body"])
    buffer = BytesIO()
    doc.save(buffer)
    data = buffer.getvalue()
    _assert_docx(data, document["title"])
    return data


def validate_output(fmt: str, data: bytes, expected_text: str) -> None:
    if fmt == "markdown":
        text = data.decode("utf-8")
        if expected_text not in text:
            raise AppError(500, "rendering_error", "Markdown export is missing the document title.")
        return
    if fmt == "html":
        text = data.decode("utf-8")
        if expected_text not in text or "<html" not in text:
            raise AppError(500, "rendering_error", "HTML export is missing expected content.")
        return
    if fmt == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(data))
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        if expected_text not in extracted:
            raise AppError(500, "rendering_error", "PDF opened but did not contain the document title.")
        return
    if fmt == "docx":
        _assert_docx(data, expected_text)
        return
    raise AppError(500, "rendering_error", f"Unsupported document format: {fmt}")


def _assert_docx(data: bytes, expected_text: str) -> None:
    from docx import Document

    try:
        doc = Document(BytesIO(data))
    except Exception as exc:
        raise AppError(500, "rendering_error", "DOCX file could not be opened.") from exc
    text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    if expected_text not in text:
        raise AppError(500, "rendering_error", "DOCX opened but did not contain the document title.")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        if "word/document.xml" not in archive.namelist():
            raise AppError(500, "rendering_error", "DOCX archive is missing word/document.xml.")


def _soft_wrap(value: str) -> str:
    words = []
    for word in value.split(" "):
        if len(word) <= 60:
            words.append(word)
            continue
        words.extend(word[index : index + 60] for index in range(0, len(word), 60))
    return " ".join(words)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _page_lines(pages: list[dict]) -> str:
    return "\n".join(f"- {page.get('title') or 'Untitled'}: {page.get('url')}" for page in pages)


def _feature_lines(features: list[dict]) -> str:
    lines = []
    for feature in features:
        ref = feature.get("references") or {}
        location = ""
        if ref.get("file"):
            location = f" ({ref['file']}:{ref.get('line_start', '')})"
        lines.append(f"- {feature.get('name')} [{feature.get('source')}]{location}: {feature.get('description')}")
    return "\n".join(lines)


def _route_lines(routes: list[dict]) -> str:
    return "\n".join(
        f"- {route.get('method', '')} {route.get('path')} in {route.get('file')}:{route.get('line_start')}"
        for route in routes
    )


def _format_map(values: dict) -> str:
    if not values:
        return "None identified."
    return "\n".join(f"- {key}: {count} files" for key, count in sorted(values.items()))
