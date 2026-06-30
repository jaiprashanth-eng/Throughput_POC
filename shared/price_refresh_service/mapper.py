from typing import Any

from shared.price_refresh_service.schemas import JobDetailResponse, JobStatsResponse


def job_redis_to_detail_response(job: dict) -> dict:
    return JobDetailResponse(
        job_id=job["job_id"],
        system=job.get("system"),
        status=job.get("status"),
        total=int(job.get("total", 0)),
        completed=int(job.get("completed", 0)),
        failed_count=int(job.get("failed_count", 0)),
        wall_time_ms=job.get("wall_time_ms"),
        enqueued_at=job.get("enqueued_at"),
    ).model_dump()


def job_stats_to_response(stats: Any) -> dict:
    return JobStatsResponse(
        total_jobs=stats.total_jobs,
        avg_wall_time_ms=stats.avg_wall_time_ms,
        p50_wall_time_ms=stats.p50_wall_time_ms,
        p95_wall_time_ms=stats.p95_wall_time_ms,
        p99_wall_time_ms=stats.p99_wall_time_ms,
        avg_items_per_job=stats.avg_items_per_job,
    ).model_dump()
