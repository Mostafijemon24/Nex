# Architecture

The repository is one web application and one API. Background work is two Celery processes that share the API image and code.

- `web` is Next.js. In local Docker it proxies `/api/*` to the API so the session cookie stays on the same origin.
- `api` is FastAPI. It owns migrations, authentication, and the HTTP contract at `/api/v1`.
- `worker` listens to the `default` queue and starts Celery beat. Beat calls `jobs.monitors` about once a minute.
- `browser-worker` listens to the `browser` queue and runs Playwright. Analysis jobs are sent to that queue because discovery needs a browser.
- PostgreSQL stores the application data. Redis is the Celery broker and the rate-limit counter.
- Files go to the `storage` volume unless an organization selects S3.

## Request flow

1. The user creates a project and adds a website, Git URL, or ZIP.
2. `POST /api/v1/projects/{id}/jobs` stores a queued job and dispatches `jobs.execute`.
3. The pipeline validates inputs, discovers the site, inspects code, records features, compares the previous run, writes documents, and optionally renders a video.
4. Each finished step updates the job. Progress is the count of finished steps divided by the eight pipeline steps.
5. Documents from older runs stay in the database. A later run does not delete them.

## AI orchestration

Provider-specific HTTP lives in `app/integrations/providers.py`. OpenRouter, OpenAI, and generic compatible endpoints use bearer authentication and `/chat/completions`. OpenRouter connection prefers `openrouter/free` when the catalog lists it, stores free chat models first, and may send a free-only `models` fallback array so retries stay zero-cost. Gemini uses `x-goog-api-key` and `generateContent`. Anthropic uses `x-api-key` with `/v1/models` for connection tests and `/v1/messages` for generation.

If no provider has passed its connection test, the plan is a fixed list of tools. If a provider is connected, the API asks it for a JSON plan and rejects any tool name outside that registry. A failed model call keeps the fixed plan. Another provider is contacted only when that row has `allow_paid_fallback` enabled and a fallback id in the same organization. Permanent authentication and invalid-request errors are not retried.

Document bodies remain tied to observed facts. The run summary records `template_fallback` unless a separate SEO request uses a connected provider. SEO template output is labeled and is not described as model output.

Website and repository text is sent as untrusted content and cannot add tools.

## Browser boundary

Before navigation, and again on every browser request, the target URL is resolved and rejected when it is link-local, metadata, or otherwise non-public. `ALLOW_LOCAL_TARGETS` only relaxes loopback and RFC1918 addresses for development tests. Downloads are disabled. The crawler does not click arbitrary buttons and does not try to solve CAPTCHA or MFA.

## Change detection

Each code analysis stores SHA-256 hashes for the inspected files. The next analysis compares those hashes and the discovered URLs. A project can also store one monitor schedule. The default worker's beat process queues a rescan when that schedule is enabled and due. This is not a claim that an external uptime monitor is running.

## Verification

`requirements/requirements.json` and `requirements/acceptance-criteria.json` are the baseline catalog for the rows the application tracks. They are not a line-by-line copy of every sentence in the original PRS. Startup inserts missing rows and does not reset a status that already exists.

A requirement becomes `verified` only when every mandatory criterion has a passing execution with the evidence that criterion requires. The catalog status in `verification-report.json` comes from the `docs_test` database. Importing that file creates a `verification_runs` row and does not change requirement status in the database that receives the import. A run whose code revision does not match the current checkout is not authoritative. This directory has no Git metadata, so the recorded revision is `unknown`.
