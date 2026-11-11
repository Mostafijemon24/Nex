#!/usr/bin/env bash
# Single-service Railway free-tier entrypoint: local Redis + API (+ optional workers).
set -euo pipefail

# SQLAlchemy + psycopg need the explicit driver in the URL scheme.
if [[ "${DATABASE_URL:-}" == postgresql://* ]]; then
  export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgresql://}"
elif [[ "${DATABASE_URL:-}" == postgres://* ]]; then
  export DATABASE_URL="postgresql+psycopg://${DATABASE_URL#postgres://}"
fi

redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --dir /tmp --save "" --appendonly no
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

if [[ "${EMBEDDED_WORKERS:-true}" == "true" ]]; then
  (
    for _ in $(seq 1 90); do
      if python - <<'PY' >/dev/null 2>&1
from sqlalchemy import create_engine, text
import os
url = os.environ["DATABASE_URL"]
engine = create_engine(url)
with engine.connect() as connection:
    connection.execute(text("SELECT version_num FROM alembic_version"))
PY
      then
        echo "Migrations ready; starting embedded workers."
        break
      fi
      sleep 2
    done
    exec celery -A app.workers.celery_app:celery_app worker \
      -Q default,browser \
      --pool solo \
      --concurrency 1 \
      -B \
      --schedule /tmp/celerybeat-schedule \
      --loglevel "${LOG_LEVEL:-INFO}"
  ) &
fi

exec python -m app.entrypoint serve
