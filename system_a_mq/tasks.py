import logging
import time
from datetime import datetime, timezone as dt_timezone

from django.utils.dateparse import parse_datetime

from shared.models import JobStatus
from shared.redis_utils import _get_client, _job_key, get_job_status, mark_item_done
from shared.services import PriceService

from system_a_mq.celery_app import app

logger = logging.getLogger(__name__)

QUEUE_NAME = "price_refresh_mq"

def _record_first_task_start(job_id: str, start_time: float) -> None:
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


def _sync_job_status_from_redis(job_id: str) -> None:
    status = get_job_status(job_id)
    updates = {
        "completed": status["completed"],
        "failed_count": status["failed_count"],
    }

    if status["status"] == "done":
        updates["status"] = "done"
        updates["wall_time_ms"] = status["wall_time_ms"]
        if status["finished_at"]:
            updates["finished_at"] = parse_datetime(status["finished_at"])
    elif status["completed"] + status["failed_count"] > 0:
        updates["status"] = "processing"

    JobStatus.objects.filter(job_id=job_id).update(**updates)


@app.task(bind=True, max_retries=2, default_retry_delay=0, queue=QUEUE_NAME)
def refresh_single_product_task(self, product_id: int, job_id: str):
    task_start = time.time()
    task_id = self.request.id
    _record_first_task_start(job_id, task_start)

    price_service_ms = None
    try:
        svc_start = time.time()
        PriceService.refresh_single_product(product_id)
        price_service_ms = (time.time() - svc_start) * 1000

        mark_item_done(job_id, success=True)
        _sync_job_status_from_redis(job_id)

        task_end = time.time()
        latency_ms = (task_end - task_start) * 1000
        logger.info(
            "task_complete task_id=%s product_id=%s job_id=%s start_time=%s end_time=%s "
            "latency_ms=%.2f price_service_ms=%.2f",
            task_id,
            product_id,
            job_id,
            datetime.fromtimestamp(task_start, tz=dt_timezone.utc).isoformat(),
            datetime.fromtimestamp(task_end, tz=dt_timezone.utc).isoformat(),
            latency_ms,
            price_service_ms,
        )
    except Exception as exc:
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            mark_item_done(job_id, success=False)
            _sync_job_status_from_redis(job_id)

            task_end = time.time()
            latency_ms = (task_end - task_start) * 1000
            logger.info(
                "task_failed task_id=%s product_id=%s job_id=%s start_time=%s end_time=%s "
                "latency_ms=%.2f price_service_ms=%s",
                task_id,
                product_id,
                job_id,
                datetime.fromtimestamp(task_start, tz=dt_timezone.utc).isoformat(),
                datetime.fromtimestamp(task_end, tz=dt_timezone.utc).isoformat(),
                latency_ms,
                f"{price_service_ms:.2f}" if price_service_ms is not None else "n/a",
            )
            raise


