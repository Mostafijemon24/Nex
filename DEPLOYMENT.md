# Deployment

Production refuses to start when `APP_ENV=production` and the secret key is short, local browser targets are enabled, or the database URL still uses the default `docs:docs` password.

## Local Docker

```bash
./scripts/prepare-env.sh
docker compose up --build
```

Persistent volumes are `pgdata` and `storage`. `docker compose down` keeps them. `docker compose down -v` deletes them.

## Vercel frontend

Deploy `apps/web` (`apps/web/vercel.json` sets the Next.js framework). Set:

- `NEXT_PUBLIC_API_URL` to the public API origin, for example `https://api.example.com`
- Do not rely on the Next.js `/api` proxy for a split deployment. The browser calls the API directly.

On the API, set `CORS_ORIGINS` to the Vercel origin, `COOKIE_SAMESITE=none`, and `COOKIE_SECURE=true`. The API and workers still need a long-running host. Vercel does not run Playwright, Redis workers, or ffmpeg for this application.

## Railway

Create separate services from `deployment/railway/api.toml`, `deployment/railway/worker.toml`, and `deployment/railway/browser-worker.toml`. Add Railway PostgreSQL and Redis. Set the same environment variables on each app service:

- `APP_ENV=production`
- `SECRET_KEY`
- `CREDENTIAL_ENCRYPTION_KEY`
- `DATABASE_URL` using the `postgresql+psycopg://` scheme
- `REDIS_URL`
- `STORAGE_DIR=/data/storage` with a volume, or `STORAGE_BACKEND=s3` plus `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, and `S3_SECRET_KEY`
- `REQUIRE_WORKERS=true` on the API
- `CORS_ORIGINS` and the cookie settings that match the frontend origin

The API service starts with `python -m app.entrypoint serve`, which migrates and then listens on port 8000. Point its health check at `/health`. Use `/health/ready` to see database, Redis, storage, and worker heartbeats.

The frontend can stay on Vercel or be a fourth Railway service built from `deployment/docker/Dockerfile.web` with `API_INTERNAL_URL=http://api.railway.internal:8000` when the browser talks to the Next.js proxy on the same host.

## Staging

See `docs/STAGING.md` for the authorized staging checklist (builds, secrets, migrations, workers, HTTPS, backup/rollback, live AI verification). Do not point `APP_DOMAIN` at a public host without owner approval.

## VPS

A DNS A or AAAA record for `APP_DOMAIN` must point at the server before HTTPS will succeed. Caddy obtains the certificate once that record resolves.

```bash
git clone <repository-url>
cd universal-ai-docs
./scripts/prepare-env.sh
```

Edit `.env`: set `APP_ENV=production`, replace the generated secrets if you want a password manager to own them, set a non-default `POSTGRES_PASSWORD`, set `DATABASE_URL` to match that password, set `APP_DOMAIN`, and set `COOKIE_SECURE=true`.

```bash
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d --build
```

The production file removes the published database, Redis, API, and web ports and adds Caddy on ports 80 and 443. The Caddyfile is `deployment/vps/Caddyfile`.

## Backup and restore

```bash
./scripts/backup.sh
```

The script writes `backups/<timestamp>/database.sql` and `storage.tar`. Restore the database with `psql` inside the Postgres container and extract the storage archive into the `storage` volume. Restoring over an existing database replaces its contents, so take a fresh backup first.

## Logs

```bash
docker compose logs -f --tail=200 api worker browser-worker web
```

Logs are JSON. Passwords, API keys, and tokens are redacted by field name.
