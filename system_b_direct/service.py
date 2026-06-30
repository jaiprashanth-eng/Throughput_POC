# PARITY CONTRACT with system_a_mq:
# - Same PriceRefreshService call
# - Same mock API latency (controlled by same env vars)
# - Same DB (same Product table, same JobStatus table)
# - Same Redis for job tracking
# - Same batch sizes tested
# - Same thread/worker count (DIRECT_WORKER_THREADS == CELERYD_CONCURRENCY)
# - Same log format
# Only difference: dispatch path (RabbitMQ+Celery vs in-process thread pool).

import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone as dt_timezone

from django.db import close_old_connections

from shared.price_refresh_service.repository import JobRepository
from shared.price_refresh_service.service import PriceRefreshService
from shared.price_refresh_service.type import JobStats
from shared.redis_utils import create_job, get_job_status, mark_item_done

logger = logging.getLogger(__name__)

_SHUTDOWN_SENTINEL = object()


class DirectDispatchService:
    """Manages the in-process thread pool for direct (no-broker) price refresh."""

    def __init__(self, *, price_refresh_service: PriceRefreshService, job_repo: JobRepository) -> None:
        self._price_refresh_service = price_refresh_service
        self._job_repo = job_repo
        self._task_queue: queue.Queue = None  # type: ignore
        self._workers: list = []
        self._pool_lock = threading.Lock()

    def submit_batch(self, product_ids: list) -> dict:
        start = time.perf_counter()
        job_id = str(uuid.uuid4())
        total = len(product_ids)

        create_job(job_id, system="direct", total=total)
        self._job_repo.create_job(job_id, system="direct", total=total)

        for product_id in product_ids:
            self._submit(product_id, job_id)

        dispatch_ms = (time.perf_counter() - start) * 1000
        return {
            "job_id": job_id,
            "total": total,
            "status": "pending",
            "system": "direct",
            "dispatch_ms": round(dispatch_ms, 3),
        }

    def get_job(self, job_id: str) -> dict:
        return get_job_status(job_id)

    def sync_job_from_redis(self, job_id: str) -> None:
        redis_data = get_job_status(job_id)
        self._job_repo.sync_from_redis(job_id, redis_data)

    def get_stats(self) -> JobStats:
        return self._job_repo.get_stats("direct")

    # ── internal: thread pool ─────────────────────────────────────────────────

    def _submit(self, product_id: int, job_id: str) -> None:
        self._ensure_workers_started()
        self._task_queue.put((product_id, job_id))

    def _ensure_workers_started(self) -> None:
        if self._task_queue is None:
            with self._pool_lock:
                if self._task_queue is None:
                    self._task_queue = queue.Queue()
                    max_workers = int(os.getenv("DIRECT_WORKER_THREADS", "4"))
                    for _ in range(max_workers):
                        thread = threading.Thread(target=self._worker_loop, daemon=True)
                        thread.start()
                        self._workers.append(thread)

    def _worker_loop(self) -> None:
        while True:
            item = self._task_queue.get()
            try:
                if item is _SHUTDOWN_SENTINEL:
                    break
                product_id, job_id = item
                self._refresh_worker(product_id, job_id)
            finally:
                self._task_queue.task_done()

    def _record_first_task_start(self, job_id: str, start_time: float) -> None:
        from shared.redis_utils import _get_client, _job_key

        client = _get_client()
        key = f"{_job_key(job_id)}:first_task_start_ms"
        now_ms = int(start_time * 1000)
        if client.setnx(key, now_ms):
            enqueued_at_ms = client.hget(_job_key(job_id), "enqueued_at_ms")
            if enqueued_at_ms:
                first_task_latency_ms = now_ms - int(enqueued_at_ms)
                logger.info(
                    "first_task_start job_id=%s first_task_start_latency_ms=%.2f",
                    job_id,
                    first_task_latency_ms,
                )

    def _refresh_worker(self, product_id: int, job_id: str) -> None:
        close_old_connections()
        task_start = time.time()
        task_id = f"thread-{threading.get_ident()}"
        self._record_first_task_start(job_id, task_start)
        self._job_repo.mark_processing(job_id)

        price_service_ms = None
        try:
            svc_start = time.time()
            self._price_refresh_service.refresh_single_product(product_id)
            price_service_ms = (time.time() - svc_start) * 1000

            mark_item_done(job_id, success=True)
            self.sync_job_from_redis(job_id)

            task_end = time.time()
            logger.info(
                "task_complete task_id=%s product_id=%s job_id=%s start_time=%s end_time=%s "
                "latency_ms=%.2f price_service_ms=%.2f",
                task_id,
                product_id,
                job_id,
                datetime.fromtimestamp(task_start, tz=dt_timezone.utc).isoformat(),
                datetime.fromtimestamp(task_end, tz=dt_timezone.utc).isoformat(),
                (task_end - task_start) * 1000,
                price_service_ms,
            )
        except Exception:
            mark_item_done(job_id, success=False)
            self.sync_job_from_redis(job_id)

            task_end = time.time()
            logger.info(
                "task_failed task_id=%s product_id=%s job_id=%s start_time=%s end_time=%s "
                "latency_ms=%.2f price_service_ms=%s",
                task_id,
                product_id,
                job_id,
                datetime.fromtimestamp(task_start, tz=dt_timezone.utc).isoformat(),
                datetime.fromtimestamp(task_end, tz=dt_timezone.utc).isoformat(),
                (task_end - task_start) * 1000,
                f"{price_service_ms:.2f}" if price_service_ms is not None else "n/a",
            )
