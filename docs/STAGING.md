# Staging deployment procedure

This procedure prepares a production-like staging environment. **Do not run it against a public host or create billable cloud resources without the owner’s explicit authorization.**

## Prerequisites (owner-supplied)

1. A staging hostname (DNS A/AAAA) the operator controls.
2. A server or Compose host with Docker Engine and Compose v2.
3. Approval to obtain TLS certificates for that hostname.
4. Authorized AI provider API keys for live verification (never commit them). For this release gate the owner selected **OpenRouter**.
5. **Hostinger VPS** access (SSH or Hostinger Docker Manager) — IP/hostname and deploy authorization. Do not paste passwords or API keys into chat.

Owner-selected staging provider: Hostinger VPS. Hostinger Docker Manager overview: https://www.hostinger.com/support/12040815-how-to-deploy-your-first-container-with-hostinger-docker-manager/

Until SSH/hostname and deploy authorization exist, complete local configuration dry-runs only.

## Configuration

1. Copy `.env.example` to `.env` on the staging host (never commit `.env`).
2. Set at minimum:
   - `APP_ENV=production`
   - Long random `SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEY`
   - Non-default `POSTGRES_PASSWORD` and matching `DATABASE_URL`
   - `APP_DOMAIN=<staging-hostname>`
   - `COOKIE_SECURE=true`
   - `CORS_ORIGINS` if the web origin differs
   - `CHROMIUM_NO_SANDBOX=true` only inside containers that cannot use the setuid sandbox; prefer `false` on hosts that can
3. Confirm `.gitignore` excludes `.env`, `storage/`, and `backups/`.

## Deploy (authorized host only)

```bash
./scripts/prepare-env.sh
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

Expected services: `postgres`, `redis`, `api`, `worker`, `browser-worker`, `web`, `caddy`.

## Verify

| Check | Command / action |
|---|---|
| Liveness | `curl -fsS https://$APP_DOMAIN/health` |
| Readiness | `curl -fsS https://$APP_DOMAIN/health/ready` |
| Migrations | API entrypoint migrates on start; confirm no migration errors in `docker compose logs api` |
| Workers | Readiness shows workers ok; Beat logs `due-monitors` |
| Auth | Sign in / sign out with a dedicated staging account |
| Persistence | Create a project, restart `api`, confirm project remains |
| Backup | `./scripts/backup.sh` then inspect `backups/<timestamp>/` |
| Rollback | Redeploy previous image tag / git revision; restore DB from backup if schema moved forward unsafely |

## Live AI on staging (OpenRouter)

1. Configure the OpenRouter key in the staging UI (encrypted credential store) **or** as a host-only env used by `scripts/live-openrouter-check.sh`.
2. Never commit the key. Never put it in chat.
3. Run connection test in the UI, then one generation.
4. Optionally: `OPENROUTER_API_KEY=… ./scripts/live-openrouter-check.sh` on the staging host (prints PASS/FAIL and revision only).
5. Record the deployed git revision with each result.

Catalog AI-* criteria are covered by automated mocks locally. Owner-selected **live** gate for this release is OpenRouter only; Gemini/OpenAI/Anthropic live calls remain optional unless the catalog is later expanded.

## Rollback

1. `docker compose … down` (keep volumes unless intentionally wiping).
2. Check out the last known-good revision.
3. `up -d --build` again.
4. If migrations are incompatible, restore `database.sql` from the pre-deploy backup after confirming downtime is acceptable.
