# PARITY CONTRACT with system_a_mq:
# - Same PriceServiceAsync call (same mock API latency, same DB table, same Redis tracking)
# - Same job lifecycle: pending → processing → done
# - Same log format (task_complete / task_failed lines with latency_ms)
# - Same batch sizes tested
# - Same concurrency cap (DIRECT_MAX_CONCURRENCY == CELERYD_CONCURRENCY),
#   enforced via asyncio.Semaphore instead of a bounded thread pool
# Only difference: dispatch path (RabbitMQ+Celery vs single-thread asyncio event loop).
#
# What is gone vs the ThreadPoolExecutor version:
# - No ThreadPoolExecutor, no threading, no concurrent.futures
# - No DIRECT_WORKER_THREADS env var (concurrency is coroutines, not OS threads)
# - No close_old_connections() (no DB connections held across thread boundaries)
# - No per-item DB sync — one DB sync after the full gather completes

import asyncio
import logging
import os
import time
from datetime import datetime, timezone as dt_timezone

from django.utils.dateparse import parse_datetime

from shared.models import JobStatus
from shared.redis_utils import get_job_status_async, mark_item_done_async
from shared.services_async import PriceServiceAsync

logger = logging.getLogger(__name__)


async def _sync_job_status_to_db(job_id: str) -> None:
    redis_data = await get_job_status_async(job_id)
    updates = {
        "completed": redis_data["completed"],
        "failed_count": redis_data["failed_count"],
    }
    if redis_data["status"] == "done":
        updates["status"] = "done"
        updates["wall_time_ms"] = redis_data["wall_time_ms"]
        if redis_data["finished_at"]:
            updates["finished_at"] = parse_datetime(redis_data["finished_at"])
    elif redis_data["completed"] + redis_data["failed_count"] > 0:
        updates["status"] = "processing"
    await JobStatus.objects.filter(job_id=job_id).aupdate(**updates)


async def _refresh_worker_async(product_id: int, job_id: str) -> None:
    task_start = time.monotonic()
    try:
        await PriceServiceAsync.refresh_single_product(product_id)
        await mark_item_done_async(job_id, success=True)
        logger.info(
            "task_complete product_id=%s job_id=%s latency_ms=%.2f",
            product_id,
            job_id,
            (time.monotonic() - task_start) * 1000,
        )
    except Exception:
        await mark_item_done_async(job_id, success=False)
        logger.info(
            "task_failed product_id=%s job_id=%s latency_ms=%.2f",
            product_id,
            job_id,
            (time.monotonic() - task_start) * 1000,
        )


async def refresh_batch_async(product_ids: list, job_id: str) -> None:
    await JobStatus.objects.filter(job_id=job_id, status="pending").aupdate(
        status="processing",
        started_at=datetime.now(tz=dt_timezone.utc),
    )

    max_concurrency = int(os.getenv("DIRECT_MAX_CONCURRENCY", "4"))
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _bounded_worker(pid: int) -> None:
        async with semaphore:
            await _refresh_worker_async(pid, job_id)

    await asyncio.gather(*[_bounded_worker(pid) for pid in product_ids])
    await _sync_job_status_to_db(job_id)
