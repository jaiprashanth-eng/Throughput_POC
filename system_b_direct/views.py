import asyncio
import json
import time
import uuid
from typing import Optional

from django.db.models import Avg, Count
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response

from shared.models import JobStatus
from shared.redis_utils import create_job_async, get_job_status_async

from system_b_direct.executor import refresh_batch_async


def _percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p / 100
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


# DRF's @api_view is a sync wrapper — Django's ASGI handler cannot detect it as
# async and will call it via sync_to_async, where awaiting is impossible.
# Use plain Django async views + JsonResponse for the two hot-path endpoints.
@csrf_exempt
async def refresh(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    request_start = time.perf_counter()

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    product_ids = data.get("product_ids")
    if not product_ids:
        return JsonResponse({"error": "product_ids is required"}, status=400)

    job_id = str(uuid.uuid4())
    total = len(product_ids)

    await create_job_async(job_id, system="direct", total=total)
    await JobStatus.objects.acreate(job_id=job_id, system="direct", total=total, status="pending")

    # Fire-and-forget: returns immediately; batch runs in background on the event loop.
    asyncio.create_task(refresh_batch_async(product_ids, job_id))

    dispatch_ms = (time.perf_counter() - request_start) * 1000
    return JsonResponse(
        {
            "job_id": job_id,
            "total": total,
            "status": "pending",
            "system": "direct",
            "dispatch_ms": round(dispatch_ms, 3),
        }
    )


@csrf_exempt
async def job_detail(request, job_id: str):
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        job = await get_job_status_async(job_id)
    except KeyError:
        return JsonResponse({"error": f"Job {job_id} not found"}, status=404)

    return JsonResponse(
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
    jobs = JobStatus.objects.filter(system="direct")
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
