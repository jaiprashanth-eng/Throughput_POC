from rest_framework.decorators import api_view
from rest_framework.response import Response

from shared.models import JobStatus


def _serialize_job(job: JobStatus) -> dict:
    avg_item_ms = None
    if job.wall_time_ms is not None and job.total:
        avg_item_ms = job.wall_time_ms / job.total
    return {
        "job_id": job.job_id,
        "total": job.total,
        "wall_time_ms": job.wall_time_ms,
        "avg_item_ms": avg_item_ms,
    }


@api_view(["GET"])
def compare(request):
    mq_jobs = (
        JobStatus.objects.filter(system="mq", status="done", wall_time_ms__isnull=False)
        .order_by("-finished_at")[:10]
    )
    direct_jobs = (
        JobStatus.objects.filter(system="direct", status="done", wall_time_ms__isnull=False)
        .order_by("-finished_at")[:10]
    )

    return Response(
        {
            "mq": [_serialize_job(job) for job in mq_jobs],
            "direct": [_serialize_job(job) for job in direct_jobs],
        }
    )
