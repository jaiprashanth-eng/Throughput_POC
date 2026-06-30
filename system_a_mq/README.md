# System A — MQ-based Price Refresh

System A accepts a batch of product IDs via HTTP POST, fans the work out to RabbitMQ via
`celery.group()`, and processes each product in parallel Celery workers. Business logic is
fully isolated in the shared hexagonal architecture under `shared/price_refresh_service/`.

## Architecture

```
POST /api/a/refresh/
       │
       ▼
  Django view (sync, DRF @api_view)
       │
       ├── create_job(job_id)          → Redis (atomic HSET)
       ├── JobStatus.objects.create()  → Postgres
       │
       ▼
  celery.group() fan-out
       │
       └──► RabbitMQ  queue: price_refresh_mq
                │
                ├── refresh_single_product_task(pid=1, job_id=...)
                ├── refresh_single_product_task(pid=2, job_id=...)
                └── ...  (N tasks, N = len(product_ids))
                          │
                          ▼
                    container.price_refresh_service
                          │
                    PriceRefreshServiceImpl
                          ├── repository.get_product(pid)      → Postgres
                          ├── MockPriceAPI.get_price(ident)    → in-process (sync)
                          └── repository.save_price(pid, ...)  → Postgres
                                    │
                                    ▼
                            mark_item_done(job_id)  → Redis (Lua script)
                            _sync_job_status_from_redis()  → Postgres update
```

## Hexagonal architecture (ports and adapters)

```
system_a_mq/container.py
    │
    ├── PriceRefreshRepository  (Protocol — interface)
    │   └── PostgresPriceRefreshRepository  (concrete impl in shared/price_refresh_service/io_django.py)
    │           ├── get_product(product_id) → Product.objects.get(pk=...)
    │           └── save_price(product_id, price, mrp) → Product.objects.filter().update(...)
    │
    └── PriceRefreshService  (Protocol — interface)
        └── PriceRefreshServiceImpl  (concrete impl in shared/price_refresh_service/service.py)
                └── refresh_single_product(product_id) → RefreshResult
```

`container = Container()` is a module-level singleton. `tasks.py` imports `container` and
calls `container.price_refresh_service.refresh_single_product(product_id)`.

## File layout

```
system_a_mq/
├── celery_app.py     # Celery app definition, autodiscovers system_a_mq.tasks
├── container.py      # DI container: wires PostgresPriceRefreshRepository → PriceRefreshServiceImpl
├── tasks.py          # @app.task: calls container.price_refresh_service, marks item done
├── views.py          # DRF @api_view: refresh (POST), job_detail (GET), stats (GET)
├── urls.py
├── apps.py
├── exceptions.py     # MQError, MQNotFound
└── middleware.py     # Pydantic validation + MQError → HTTP mapping decorators
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/a/refresh/` | Enqueue a batch refresh job |
| GET | `/api/a/jobs/{job_id}/` | Live job status from Redis |
| GET | `/api/a/stats/` | Aggregate stats from `JobStatus` (system=`mq`) |

### POST /api/a/refresh/

Request:
```json
{ "product_ids": [1, 2, 3] }
```

Response (returned immediately before any items are processed):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "total": 3,
  "status": "pending",
  "system": "mq",
  "dispatch_ms": 12.345
}
```

## Celery worker

```bash
export DATABASE_URL=postgres://poc:poc@localhost:5433/poc_db
export REDIS_URL=redis://localhost:6379/0
export MOCK_API_LATENCY_MS=80

celery -A system_a_mq.celery_app worker \
  --queues=price_refresh_mq \
  --concurrency=${CELERYD_CONCURRENCY:-4} \
  --prefetch-multiplier=1 \
  --loglevel=info
```

`--prefetch-multiplier=1` ensures each worker fetches exactly one message at a time,
preventing a single worker from hoarding tasks when the pool is busy.

## Retry behaviour

Each task retries up to 2 times (`max_retries=2, default_retry_delay=0`) on any exception.
If all retries are exhausted, `mark_item_done(job_id, success=False)` is called and the
task raises — the item counts as failed but the job continues to completion.

## Log format

Every task emits a structured log line on completion or failure:

```
task_complete task_id=<celery-id> product_id=<int> job_id=<uuid>
              start_time=<iso> end_time=<iso>
              latency_ms=<float> price_service_ms=<float>

task_failed   task_id=<celery-id> product_id=<int> job_id=<uuid>
              start_time=<iso> end_time=<iso>
              latency_ms=<float> price_service_ms=<float|n/a>
```

The first task to run in a job also emits:
```
first_task_start job_id=<uuid> first_task_start_latency_ms=<float>
```

This measures the gap from HTTP dispatch to first Celery task execution — captures broker
round-trip latency.

## Constraints

- Views only enqueue; they never call business logic directly.
- All business logic lives in `shared/price_refresh_service/` via `container`.
- Fan-out uses `celery.group()` only (no chord, chain, or canvas).
- All tasks route to the `price_refresh_mq` queue.
- `CELERY_TASK_ALWAYS_EAGER = False` — tasks always go through the broker, never inline.
