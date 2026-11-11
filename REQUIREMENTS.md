# Requirements

The baseline catalog is `requirements/requirements.json`. Acceptance criteria are `requirements/acceptance-criteria.json`. Both files describe the platform's own behavior. A documentation project can also receive requirements for features that an analysis actually observed.

## Status rules

- `not_started`: no mandatory criterion has passed.
- `partially_verified`: at least one mandatory criterion passed, and none failed.
- `verified`: every mandatory criterion has a passing execution and the evidence it requires.
- `failed`: a mandatory criterion failed.
- `blocked`: a blocker is recorded, or a mandatory criterion is blocked.
- `needs_review`: the requirement has no criteria.

A client cannot set `verified` through the API. Automated criteria change only when the server records an execution. Human review is accepted only for criteria whose method is `human_review`. The current platform criteria are automated.

The database is the runtime record for the database the API is using. The JSON files are the specification and are not rewritten when a test passes. `requirements/verification-report.json` is produced by pytest from the `docs_test` database after assertions pass. It includes the database name, timestamp, environment, and code revision. Importing it through `POST /api/v1/verification-runs/import` stores that evidence and does not change requirement rows. Because this workspace is not a Git checkout, the revision is `unknown`, and the report is not authoritative for the current files.

## Coverage

`GET /api/v1/reports/coverage` returns separate counts:

- requirements by status
- acceptance criteria tested and total
- test executions passed and executed
- evidence present and required
- verified requirements and total requirements

Those counts are not folded into one score.

## Catalog versus the full PRS

The catalog has 32 requirements and 43 criteria. Passing that catalog does not mean every sentence of the original PRS was implemented. In particular, live vendor calls, a running beat process, cloud deployment, and narration with a real speech provider are outside the mocks used by pytest. See `requirements/implementation-audit.md`.
