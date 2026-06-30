from typing import Optional

from pydantic import BaseModel


class RefreshRequest(BaseModel):
    product_ids: list[int]


class RefreshResponse(BaseModel):
    job_id: str
    total: int
    status: str
    system: str
    dispatch_ms: float


class JobDetailResponse(BaseModel):
    job_id: str
    system: Optional[str] = None
    status: Optional[str] = None
    total: int
    completed: int
    failed_count: int
    wall_time_ms: Optional[float] = None
    enqueued_at: Optional[str] = None


class JobStatsResponse(BaseModel):
    total_jobs: Optional[int] = None
    avg_wall_time_ms: Optional[float] = None
    p50_wall_time_ms: Optional[float] = None
    p95_wall_time_ms: Optional[float] = None
    p99_wall_time_ms: Optional[float] = None
    avg_items_per_job: Optional[float] = None
