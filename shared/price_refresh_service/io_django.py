from datetime import datetime, timezone as dt_timezone

from django.db.models import Avg, Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shared.models import JobStatus, Product
from shared.price_refresh_service.type import JobStats, ProductData


def _percentile(values: list, p: float):
    if not values:
        return None
    sorted_vals = sorted(values)
    idx = (len(sorted_vals) - 1) * p / 100
    lower = int(idx)
    upper = min(lower + 1, len(sorted_vals) - 1)
    weight = idx - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


class DjangoProductRepository:
    def get_product(self, product_id: int) -> ProductData:
        try:
            p = Product.objects.get(pk=product_id)
        except Product.DoesNotExist as exc:
            raise ValueError(f"Product {product_id} does not exist") from exc
        return ProductData(
            id=p.id,
            platform_identifier=p.platform_identifier,
            price=p.price,
            mrp=p.mrp,
        )

    def update_product_price(self, product_id: int, price, mrp) -> None:
        Product.objects.filter(pk=product_id).update(
            price=price,
            mrp=mrp,
            last_refreshed_at=timezone.now(),
        )


class DjangoJobRepository:
    def create_job(self, job_id: str, system: str, total: int) -> None:
        JobStatus.objects.create(job_id=job_id, system=system, total=total, status="pending")

    def mark_processing(self, job_id: str) -> None:
        JobStatus.objects.filter(job_id=job_id, status="pending").update(
            status="processing",
            started_at=datetime.now(tz=dt_timezone.utc),
        )

    def sync_from_redis(self, job_id: str, redis_data: dict) -> None:
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
        JobStatus.objects.filter(job_id=job_id).update(**updates)

    def get_stats(self, system: str) -> JobStats:
        jobs = JobStatus.objects.filter(system=system)
        completed = jobs.filter(wall_time_ms__isnull=False)
        wall_times = list(completed.values_list("wall_time_ms", flat=True))
        agg = completed.aggregate(
            total_jobs=Count("id"),
            avg_wall_time_ms=Avg("wall_time_ms"),
        )
        all_agg = jobs.aggregate(avg_items_per_job=Avg("total"))
        return JobStats(
            total_jobs=agg["total_jobs"],
            avg_wall_time_ms=agg["avg_wall_time_ms"],
            p50_wall_time_ms=_percentile(wall_times, 50),
            p95_wall_time_ms=_percentile(wall_times, 95),
            p99_wall_time_ms=_percentile(wall_times, 99),
            avg_items_per_job=all_agg["avg_items_per_job"],
        )
