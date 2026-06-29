# System A — MQ-based Price Refresh

System A accepts a batch of product IDs via HTTP, enqueues work to RabbitMQ, and
processes each product in parallel Celery workers. All business logic lives in
`shared/services.py`; this package only handles HTTP orchestration and task fan-out.

## Architecture

```
POST /api/a/refresh/
       │
       ▼
  Django view ──► create_job (Redis) + JobStatus (DB)
       │
       ▼
  celery.group() fan-out  ──►  RabbitMQ (queue: price_refresh_mq)
       │
       ├──► refresh_single_product_task(pid, job_id)
       ├──► refresh_single_product_task(pid, job_id)
       └──► ...
                │
                ▼
         PriceService.refresh_single_product()  (shared/)
                │
                ▼
         mark_item_done(job_id)  (Redis)
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/a/refresh/` | Enqueue a batch refresh job |
| GET | `/api/a/jobs/{job_id}/` | Live job status from Redis |
| GET | `/api/a/stats/` | Aggregate stats from `JobStatus` (system=`mq`) |

### POST /api/a/refresh/

```json
{ "product_ids": [1, 2, 3], "async": true }
```

Response (returned immediately, regardless of batch size):

```json
{
  "job_id": "uuid",
  "total": 3,
  "status": "pending",
  "system": "mq",
  "dispatch_ms": 12.345
}
```

## Celery worker startup

```bash
celery -A system_a_mq.celery_app worker \
  --queues=price_refresh_mq \
  --concurrency=${CELERYD_CONCURRENCY:-4} \
  --loglevel=info \
  --prefetch-multiplier=1
```

## Timing hooks (for later extraction)

| Metric | Where recorded |
|--------|----------------|
| Dispatch latency | `dispatch_ms` in POST response |
| First task start latency | Log line `first_task_start` with `first_task_start_latency_ms` |
| Total wall time | `wall_time_ms` in Redis / `JobStatus` when last item completes |
| Per-item execution time | Log line `task_complete` with `price_service_ms` |

## Constraints

- Views never call `PriceService` — they only enqueue.
- `tasks.py` delegates all business logic to `shared/services.py`.
- Fan-out uses `celery.group()` only (no chord or chain).
- All tasks route to the `price_refresh_mq` queue.
- `CELERY_TASK_ALWAYS_EAGER = False` — tasks never run synchronously in tests.
