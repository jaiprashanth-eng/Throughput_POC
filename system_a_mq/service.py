"""MQ dispatch service: creates jobs, fans out via Celery group, tracks status."""

import time
import uuid

from shared.price_refresh_service.repository import JobRepository
from shared.price_refresh_service.type import JobStats
from shared.redis_utils import create_job, get_job_status


class MqDispatchService:
    def __init__(self, *, job_repo: JobRepository) -> None:
        self._job_repo = job_repo

    def submit_batch(self, product_ids: list) -> dict:
        from celery import group

        from system_a_mq.tasks import refresh_single_product_task  # lazy: avoids circular at import time

        start = time.perf_counter()
        job_id = str(uuid.uuid4())
        total = len(product_ids)

        create_job(job_id, system="mq", total=total)
        self._job_repo.create_job(job_id, system="mq", total=total)

        task_group = group(refresh_single_product_task.s(pid, job_id) for pid in product_ids)
        task_group.apply_async()

        self._job_repo.mark_processing(job_id)

        dispatch_ms = (time.perf_counter() - start) * 1000
        return {
            "job_id": job_id,
            "total": total,
            "status": "pending",
            "system": "mq",
            "dispatch_ms": round(dispatch_ms, 3),
        }

    def get_job(self, job_id: str) -> dict:
        return get_job_status(job_id)

    def sync_job_from_redis(self, job_id: str) -> None:
        redis_data = get_job_status(job_id)
        self._job_repo.sync_from_redis(job_id, redis_data)

    def record_first_task_start(self, job_id: str, start_time: float) -> None:
        from shared.redis_utils import _get_client, _job_key

        client = _get_client()
        key = f"{_job_key(job_id)}:first_task_start_ms"
        now_ms = int(start_time * 1000)
        if client.setnx(key, now_ms):
            enqueued_at_ms = client.hget(_job_key(job_id), "enqueued_at_ms")
            if enqueued_at_ms:
                import logging

                logging.getLogger(__name__).info(
                    "first_task_start job_id=%s first_task_start_latency_ms=%.2f",
                    job_id,
                    now_ms - int(enqueued_at_ms),
                )

    def get_stats(self) -> JobStats:
        return self._job_repo.get_stats("mq")
