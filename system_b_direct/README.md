# System B — Direct (No MQ) Price Refresh

System B accepts a batch of product IDs via HTTP and processes them using an
in-process `ThreadPoolExecutor`. No RabbitMQ, no Celery — concurrency is
controlled entirely by `DIRECT_WORKER_THREADS`.

## Architecture

```
POST /api/b/refresh/
       │
       ▼
  Django view ──► create_job (Redis) + JobStatus (DB)
       │
       ▼
  submit_refresh() × N  ──►  ThreadPoolExecutor (DIRECT_WORKER_THREADS)
       │
       ├──► _refresh_worker(pid, job_id)
       ├──► _refresh_worker(pid, job_id)
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
| POST | `/api/b/refresh/` | Submit a batch refresh job |
| GET | `/api/b/jobs/{job_id}/` | Live job status from Redis |
| GET | `/api/b/stats/` | Aggregate stats from `JobStatus` (system=`direct`) |

### POST /api/b/refresh/

```json
{ "product_ids": [1, 2, 3] }
```

Response (returned immediately, regardless of batch size):

```json
{
  "job_id": "uuid",
  "total": 3,
  "status": "pending",
  "system": "direct",
  "dispatch_ms": 5.123
}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DIRECT_WORKER_THREADS` | `4` | Thread pool size (parity with `CELERYD_CONCURRENCY`) |

## Comparison with System A

Use `GET /api/compare/` to see the last 10 completed jobs from each system side by side.

## Constraints

- Views never call `PriceService` — only `executor.submit_refresh()`.
- `executor.py` is the only file that calls `PriceService`.
- No Celery, asyncio, or Django signals.
- Same log format as System A for apples-to-apples benchmarking.
