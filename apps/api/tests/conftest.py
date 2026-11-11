"""PostgreSQL-backed API tests. Environment is set before application imports."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-0123456789"
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = __import__("base64").urlsafe_b64encode(__import__("secrets").token_bytes(32)).decode()
os.environ["ALLOW_LOCAL_TARGETS"] = "true"
os.environ["REQUIRE_WORKERS"] = "false"
os.environ["COOKIE_SECURE"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://docs:docs@localhost:5432/docs")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

_original_database_url = os.environ["DATABASE_URL"]
_admin_url = os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/postgres"
os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/docs_test"


def database_name(url: str) -> str:
    path = urlsplit(url.replace("postgresql+psycopg", "postgresql", 1)).path.strip("/")
    return path.split("?", 1)[0]


def assert_isolated_test_database(url: str) -> str:
    name = database_name(url)
    if name in {"docs", "postgres", "template0", "template1"} or not name.endswith("_test"):
        raise SystemExit(f"Refusing to run tests against database {name!r}. The test database name must end with _test.")
    return name


assert_isolated_test_database(os.environ["DATABASE_URL"])
_redis = urlsplit(os.environ["REDIS_URL"])
os.environ["REDIS_URL"] = urlunsplit(_redis._replace(path="/1"))
os.environ["STORAGE_DIR"] = tempfile.mkdtemp(prefix="docs-test-")
os.environ["VERIFICATION_LOG"] = str(Path(os.environ["STORAGE_DIR"]) / "verification.jsonl")
_requirements = Path(os.environ.get("REQUIREMENTS_DIR", ""))
if not (_requirements / "requirements.json").is_file():
    for _candidate in (Path(__file__).resolve().parents[3] / "requirements", Path("/srv/requirements")):
        if (_candidate / "requirements.json").is_file():
            os.environ["REQUIREMENTS_DIR"] = str(_candidate)
            break

import time  # noqa: E402

from sqlalchemy import create_engine, text  # noqa: E402

_admin = create_engine(_admin_url, isolation_level="AUTOCOMMIT")
for _attempt in range(30):
    try:
        with _admin.connect() as connection:
            exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = 'docs_test'")).scalar()
            if not exists:
                connection.execute(text("CREATE DATABASE docs_test"))
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("PostgreSQL is not ready. Start the database and run the tests again.")
_admin.dispose()

from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.verification.seed import seed_requirements  # noqa: E402


def _project_count(url: str) -> int | None:
    name = database_name(url)
    if name.endswith("_test"):
        return None
    counter = create_engine(url, pool_pre_ping=True)
    try:
        with counter.connect() as connection:
            return int(connection.execute(text("SELECT COUNT(*) FROM projects")).scalar() or 0)
    except Exception:
        return None
    finally:
        counter.dispose()


@pytest.fixture(scope="session", autouse=True)
def development_database_unchanged():
    before = _project_count(_original_database_url)
    yield before
    after = _project_count(_original_database_url)
    if before is not None and after is not None and before != after:
        raise RuntimeError(f"Development database project count changed from {before} to {after}.")


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    Path(os.environ["VERIFICATION_LOG"]).write_text("", encoding="utf-8")
    yield
    _write_report()


@pytest.fixture(autouse=True)
def clean_database():
    from redis import Redis

    assert_isolated_test_database(os.environ["DATABASE_URL"])
    table_names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))
    Redis.from_url(os.environ["REDIS_URL"]).flushdb()
    db = SessionLocal()
    try:
        seed_requirements(db)
    finally:
        db.close()
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.workers.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    with TestClient(app) as test_client:
        yield test_client


def record(requirement: str, criterion: str, result: str, actual: str) -> None:
    from app.verification.engine import record_execution

    db = SessionLocal()
    try:
        record_execution(
            db,
            requirement_key=requirement,
            criterion_key=criterion,
            result=result,
            actual_result=actual,
            environment="pytest",
            evidence_summary=actual,
        )
        db.commit()
    finally:
        db.close()
    with Path(os.environ["VERIFICATION_LOG"]).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"requirement": requirement, "criterion": criterion, "result": result, "actual": actual}) + "\n")


def _write_report() -> None:
    requirements = json.loads((Path(os.environ["REQUIREMENTS_DIR"]) / "requirements.json").read_text(encoding="utf-8"))
    criteria = json.loads((Path(os.environ["REQUIREMENTS_DIR"]) / "acceptance-criteria.json").read_text(encoding="utf-8"))
    logged = []
    path = Path(os.environ["VERIFICATION_LOG"])
    if path.exists():
        logged = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_criterion: dict[str, str] = {}
    for item in logged:
        by_criterion[item["criterion"]] = item["result"]
    requirement_status = {}
    for requirement in requirements:
        related = [item for item in criteria if item["requirement_id"] == requirement["id"]]
        statuses = [by_criterion.get(item["id"], "not_tested") for item in related]
        if any(status == "failed" for status in statuses):
            requirement_status[requirement["id"]] = "failed"
        elif statuses and all(status == "passed" for status in statuses):
            requirement_status[requirement["id"]] = "verified"
        elif any(status == "passed" for status in statuses):
            requirement_status[requirement["id"]] = "partially_verified"
        else:
            requirement_status[requirement["id"]] = "not_started"
    passed = sum(1 for status in by_criterion.values() if status == "passed")
    executed = sum(1 for status in by_criterion.values() if status != "skipped")
    revision = os.environ.get("CODE_REVISION", "").strip() or "unknown"
    dirty_env = os.environ.get("CODE_TREE_DIRTY")
    dirty = True
    if dirty_env is not None:
        dirty = dirty_env.strip().lower() in {"1", "true", "yes"}
    elif revision == "unknown":
        try:
            import subprocess

            completed = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False, timeout=3)
            revision = completed.stdout.strip() or "unknown"
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False, timeout=3)
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else True
        except (OSError, subprocess.TimeoutExpired):
            revision = "unknown"
            dirty = True
    else:
        dirty = False
    authoritative = revision != "unknown" and not dirty
    report = {
        "source": "pytest",
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "database_name": database_name(os.environ["DATABASE_URL"]),
        "code_revision": revision,
        "environment": "pytest",
        "authoritative_for_current_code": authoritative,
        "note": (
            "This file records a test-database run. It does not modify the development database or mark live requirement rows verified."
            + ("" if authoritative else " The revision is unknown or the working tree is dirty, so this report is not authoritative for the current files.")
        ),
        "total_requirements": len(requirements),
        "implemented_requirements": sum(status in {"verified", "partially_verified", "failed"} for status in requirement_status.values()),
        "verified_requirements": sum(status == "verified" for status in requirement_status.values()),
        "failed_requirements": sum(status == "failed" for status in requirement_status.values()),
        "blocked_requirements": 0,
        "untested_requirements": sum(status == "not_started" for status in requirement_status.values()),
        "acceptance_criteria_coverage": {"tested": len(by_criterion), "total": len(criteria)},
        "test_pass_rate": {"passed": passed, "executed": executed},
        "requirements": requirement_status,
        "results": logged,
    }
    destination = Path(os.environ["REQUIREMENTS_DIR"]) / "verification-report.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
