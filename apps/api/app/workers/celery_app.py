"""Celery application. Browser and general work use separate queues."""

from __future__ import annotations

import os
import threading
import time

from celery import Celery
from celery.signals import worker_ready

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("docs", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_default_queue="default",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_track_started=True,
    imports=("app.workers.tasks",),
    beat_schedule={"due-monitors": {"task": "jobs.monitors", "schedule": 60.0}},
)


@worker_ready.connect
def _start_heartbeat(sender=None, **_kwargs) -> None:
    queue = os.environ.get("WORKER_QUEUE", "")
    if not queue:
        return

    def loop() -> None:
        from redis import Redis

        client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2)
        while True:
            try:
                client.set(f"worker-heartbeat:{queue}", str(time.time()), ex=20)
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=loop, name=f"heartbeat-{queue}", daemon=True).start()
