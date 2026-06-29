import time
import uuid
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from celery import group
from django.db.models import Avg, Count
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from shared.models import JobStatus
from shared.redis_utils import create_job, get_job_status

from system_a_mq.tasks import refresh_single_product_task


def _percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p / 100
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


@api_view(["POST"])
def refresh(request):
    request_start = time.perf_counter()

    product_ids = request.data.get("product_ids")
    if not product_ids:
        return Response(
            {"error": "product_ids is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    job_id = str(uuid.uuid4())
    total = len(product_ids)

    create_job(job_id, system="mq", total=total)
    JobStatus.objects.create(job_id=job_id, system="mq", total=total, status="pending")

    task_group = group(
        refresh_single_product_task.s(pid, job_id) for pid in product_ids
    )
    task_group.apply_async()

    JobStatus.objects.filter(job_id=job_id).update(
        status="processing",
        started_at=datetime.now(tz=dt_timezone.utc),
    )

    dispatch_ms = (time.perf_counter() - request_start) * 1000
    return Response(
        {
            "job_id": job_id,
            "total": total,
            "status": "pending",
            "system": "mq",
            "dispatch_ms": round(dispatch_ms, 3),
        }
    )


@api_view(["GET"])
def job_detail(request, job_id: str):
    try:
        job = get_job_status(job_id)
    except KeyError:
        return Response(
            {"error": f"Job {job_id} not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(
        {
            "job_id": job["job_id"],
            "system": job["system"],
            "status": job["status"],
            "total": job["total"],
            "completed": job["completed"],
            "failed_count": job["failed_count"],
            "wall_time_ms": job["wall_time_ms"],
            "enqueued_at": job["enqueued_at"],
        }
    )


@api_view(["GET"])
def stats(request):
    jobs = JobStatus.objects.filter(system="mq")
    completed_jobs = jobs.filter(wall_time_ms__isnull=False)

    wall_times = list(completed_jobs.values_list("wall_time_ms", flat=True))
    aggregates = completed_jobs.aggregate(
        total_jobs=Count("id"),
        avg_wall_time_ms=Avg("wall_time_ms"),
        avg_items_per_job=Avg("total"),
    )

    all_jobs_agg = jobs.aggregate(total_jobs_all=Count("id"), avg_items_per_job=Avg("total"))

    return Response(
        {
            "total_jobs": aggregates["total_jobs"],
            "avg_wall_time_ms": aggregates["avg_wall_time_ms"],
            "p50_wall_time_ms": _percentile(wall_times, 50),
            "p95_wall_time_ms": _percentile(wall_times, 95),
            "p99_wall_time_ms": _percentile(wall_times, 99),
            "avg_items_per_job": all_jobs_agg["avg_items_per_job"],
        }
    )
