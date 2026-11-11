"""Process entrypoints for the API and workers."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from sqlalchemy import text


def wait_for_database() -> None:
    from app.db.session import engine

    for _attempt in range(30):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except Exception:
            time.sleep(2)
    print("Database is not reachable. Check DATABASE_URL and that PostgreSQL is running.", file=sys.stderr)
    raise SystemExit(1)


def wait_for_migrations() -> None:
    from app.db.session import engine

    for _attempt in range(60):
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT version_num FROM alembic_version"))
            return
        except Exception:
            time.sleep(2)
    print("Database migrations have not finished. Start the API service so it can apply them.", file=sys.stderr)
    raise SystemExit(1)


def migrate() -> None:
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")


def serve() -> None:
    import uvicorn

    from app.verification.seed import seed_requirements
    from app.db.session import SessionLocal

    wait_for_database()
    migrate()
    db = SessionLocal()
    try:
        seed_requirements(db)
    finally:
        db.close()
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_config=None)


def run_worker(queue: str) -> None:
    wait_for_database()
    wait_for_migrations()
    os.environ["WORKER_QUEUE"] = queue
    command = [
        "celery",
        "-A",
        "app.workers.celery_app:celery_app",
        "worker",
        "-Q",
        queue,
        "--pool",
        "solo",
        "--concurrency",
        "1",
        "--loglevel",
        os.environ.get("LOG_LEVEL", "INFO"),
    ]
    if queue == "default":
        command.extend(["-B", "--schedule", "/tmp/celerybeat-schedule"])
    os.execvp("celery", command)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if mode == "serve":
        serve()
    elif mode == "worker":
        run_worker("default")
    elif mode == "browser-worker":
        run_worker("browser")
    else:
        print("Usage: python -m app.entrypoint [serve|worker|browser-worker]", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
