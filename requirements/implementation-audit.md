# Implementation audit

Date: 2026-10-27. No GitHub push and no production deployment were performed.

## What changed (this verification pass)

- Dual DNS resolution before accepting outbound/automation hostnames (rebinding mismatch rejected). Chromium `--no-sandbox` is gated by `CHROMIUM_NO_SANDBOX` (default true in Compose containers).
- Application HTTP uses `app.core.outbound`: TCP pin to validated IP, Host/SNI for the original hostname, redirect Location revalidated (providers, WordPress, website source test).
- Anthropic `/v1/messages` generation implemented (mocked automated coverage; live call still needs an owner key).
- Optional document types emit longer observed-fact sections when selected. Catalog DOC-001/002 require export formats and version retention, not full product manuals.
- TTS-001 is the missing-speech boundary; live TTS is not a catalog mandatory.
- Tests cover uploaded-recording video, monitor `run_due`, logout, DNS rebinding, and pinned outbound + unsafe-redirect rejection.
- Local Git repository; `Makefile` `test` passes `CODE_REVISION` / `CODE_TREE_DIRTY`.

## Catalog result

Latest `make test` on `docs_test`: **28 passed**, 0 failed, 43/43 catalog criteria passed. See `verification-report.json` and `verification-report.md`.

That catalog is the machine-readable registry for the 32 mandatory rows. It is not the full PRS. Items under `prs_gaps` stay incomplete or unverified even when the catalog is green.

## Still incomplete or unverified against the full PRS

| ID | Status | Outstanding |
|---|---|---|
| PRS-AI-LIVE | BLOCKED | Live OpenRouter, Gemini, OpenAI, Anthropic calls need owner-supplied keys. Automated tests used mocks. |
| PRS-TTS-LIVE | BLOCKED | Speech adapter exists. No live audio was produced. |
| PRS-ANTHROPIC-GENERATE | PASS (mocked) / BLOCKED (live) | Implementation + mock test present. Live Anthropic generation not run. |
| PRS-RECORDING-VIDEO | PASS (automated) | Uploaded recording path exercised with ffmpeg fixture in pytest. |
| PRS-DOC-DEPTH | OPTIONAL | Catalog does not mandate full manuals; optional types remain observed-fact notes. |
| PRS-BEAT-OBSERVED | PASS | Live Beat queued a due rescan in development. |
| PRS-DEPLOY | BLOCKED | Staging/production deploy not authorized or performed. |
| PRS-REVISION | PASS when tree clean | Report authoritative only when tests start on a clean tree. |
| LICENSE | BLOCKED | Owner must choose a software license. None was invented. |
| GITHUB_REMOTE | NOT_STARTED | No git remote; no public publish. |

## Security review scope

Reviewed in code and covered in part by tests: password hashing, encrypted secrets, organization isolation, SSRF (metadata, dual-resolve rebinding, outbound TCP pin + unsafe redirect), ZIP path checks, prompt tool allow-list, provider URL query secrets, redaction, logout invalidation, Chromium sandbox env gate.

Not a full penetration test. Remaining limitations are listed in `SECURITY.md` (Chromium/`git clone` are not TCP-pinned; container Chromium typically runs without the setuid sandbox).
