from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class ProductData:
    id: int
    platform_identifier: str
    price: Optional[Decimal]
    mrp: Optional[Decimal]


@dataclass
class RefreshResult:
    product_id: int
    new_price: Optional[Decimal]
    success: bool


@dataclass
class JobSummary:
    job_id: str
    system: Optional[str]
    total: int
    completed: int
    failed_count: int
    status: Optional[str]
    wall_time_ms: Optional[float]
    enqueued_at: Optional[str]


@dataclass
class JobStats:
    total_jobs: Optional[int]
    avg_wall_time_ms: Optional[float]
    p50_wall_time_ms: Optional[float]
    p95_wall_time_ms: Optional[float]
    p99_wall_time_ms: Optional[float]
    avg_items_per_job: Optional[float]
