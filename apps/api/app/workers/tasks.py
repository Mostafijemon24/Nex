from app.workers.celery_app import celery_app


@celery_app.task(name="jobs.execute")
def execute_job_task(job_id: str) -> None:
    from app.services.pipeline import execute_job

    execute_job(job_id)


def dispatch(job_id: str) -> str:
    result = execute_job_task.apply_async(args=[job_id], queue="browser")
    return str(result.id or "")


@celery_app.task(name="jobs.monitors")
def run_due_monitors() -> int:
    from app.services.monitors import run_due

    return run_due()
