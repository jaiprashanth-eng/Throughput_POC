# System B — asyncio Direct Price Refresh

System B accepts a batch of product IDs via HTTP POST and processes them using asyncio
coroutines on a single event loop thread. No RabbitMQ, no Celery, no OS threads — all
concurrency is cooperative, gated by `asyncio.Semaphore(DIRECT_MAX_CONCURRENCY)`.

> **ASGI required.** Run via `./start_poc.sh async` (Gunicorn + UvicornWorker).
> `./start_poc.sh dev` (WSGI runserver) will crash with `RuntimeError: no running event loop`
> the moment `asyncio.create_task()` is called from the `refresh` view.

## Architecture

```
POST /api/b/refresh/   (async Django view — no DRF @api_view)
       │
       ▼
  async def refresh(request)
       │
       ├── await create_job_async(job_id)          → Redis (async HSET)
       ├── await JobStatus.objects.acreate(...)     → Postgres (Django async ORM)
       │
       └── asyncio.create_task(refresh_batch_async(product_ids, job_id))
                │                   ← returns immediately; HTTP response sent here
                ▼
         refresh_batch_async()
                │
                ├── await JobStatus...aupdate(status="processing")
                │
                ├── asyncio.Semaphore(DIRECT_MAX_CONCURRENCY)
                │
                └── asyncio.gather(
                        _bounded_worker(pid=1, job_id),
                        _bounded_worker(pid=2, job_id),
                        ...N coroutines...
                    )
                         │  each coroutine:
                         ▼
                    async with semaphore:
                        await PriceServiceAsync.refresh_single_product(pid)
                              ├── await Product.objects.aget(pk=pid)       → Postgres
                              ├── await MockPriceAPI.get_price_async(ident) → asyncio.sleep
                              └── await product.asave(update_fields=[...]) → Postgres
                                       │
                                       ▼
                              await mark_item_done_async(job_id)  → Redis (Lua eval)

                    (after gather completes)
                    await _sync_job_status_to_db(job_id)  → Postgres final sync
```

## How the event loop achieves concurrency

The asyncio event loop runs on a **single OS thread**. Coroutines are not truly parallel —
they run one at a time, switching between each other at every `await` point:

- `await asyncio.sleep(...)` inside `MockPriceAPI.get_price_async` — yields during the
  simulated network wait; all other coroutines run during this time.
- `await Product.objects.aget(...)` / `await product.asave(...)` — Django's async ORM
  yields to the event loop while waiting for Postgres.
- `await client.eval(...)` / `await client.hset(...)` — async Redis client yields during
  network round-trips.

With 200 products at 80ms mock latency and `DIRECT_MAX_CONCURRENCY=4`, the event loop
keeps 4 coroutines in-flight simultaneously, each yielding at every IO point.

## File layout

```
system_b_direct/
├── executor.py     # refresh_batch_async: asyncio.gather + Semaphore cap
├── views.py        # Async Django views: refresh (POST), job_detail (GET), stats (GET)
├── urls.py
└── apps.py
```

Note: `container.py`, `middleware.py`, and `exceptions.py` were removed. System B's async
path uses `PriceServiceAsync` directly — no DI container is needed because there is no
injectable sync/async variant switch in the async path.

## Why plain Django views instead of DRF @api_view

DRF 3.17 has no `AsyncAPIView` or `async_api_view`. `@api_view` returns a sync
`View.as_view()` wrapper — `inspect.iscoroutinefunction` returns `False` on it. Django's
ASGI handler therefore calls it via `sync_to_async`, and inside that thread DRF calls
`handler(request)` on the `async def` which returns a raw coroutine object instead of
a `Response`, causing HTTP 500. The fix is to bypass DRF's dispatch chain entirely:

- `refresh` and `job_detail` are plain `async def` functions decorated only with
  `@csrf_exempt`, returning `JsonResponse`.
- `stats` (infrequent, sync ORM aggregates) stays on `@api_view`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/b/refresh/` | Submit a batch refresh job |
| GET | `/api/b/jobs/{job_id}/` | Live job status from Redis (async) |
| GET | `/api/b/stats/` | Aggregate stats from `JobStatus` (system=`direct`) |

### POST /api/b/refresh/

Request:
```json
{ "product_ids": [1, 2, 3] }
```

Response (returned immediately, before any products are refreshed):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "total": 3,
  "status": "pending",
  "system": "direct",
  "dispatch_ms": 4.812
}
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DIRECT_MAX_CONCURRENCY` | `4` | Max coroutines running simultaneously (asyncio.Semaphore). Set equal to `CELERYD_CONCURRENCY` for fair benchmarking. |

## Log format

Matches System A for apples-to-apples comparison:

```
task_complete product_id=<int> job_id=<uuid> latency_ms=<float>
task_failed   product_id=<int> job_id=<uuid> latency_ms=<float>
```

`latency_ms` covers the full per-item time: semaphore wait + Postgres read + mock API +
Postgres write + Redis mark-done.

## Parity contract with System A

| Dimension | System A | System B |
|---|---|---|
| Business outcome | `PriceRefreshServiceImpl` | `PriceServiceAsync` (same logic, async) |
| Job lifecycle | `pending → processing → done` | `pending → processing → done` |
| Log format | `task_complete / task_failed` with `latency_ms` | identical |
| Redis tracking | Lua `mark_item_done` script | same script via `redis.asyncio` |
| Concurrency cap | `CELERYD_CONCURRENCY` (Celery worker processes) | `DIRECT_MAX_CONCURRENCY` (asyncio.Semaphore) |
| Failure handling | Celery retry ×2, then mark failed | mark failed immediately (no retry) |

## Constraints

- `views.refresh` and `views.job_detail` must remain `async def` — they call async Redis
  and async ORM directly.
- `asyncio.create_task()` must be called from within a running event loop — only valid
  under UvicornWorker, not WSGI.
- `executor.py` must not call `time.sleep()` — use `asyncio.sleep()` to avoid blocking the
  event loop thread.
- No threads: `ThreadPoolExecutor`, `threading`, and `concurrent.futures` are prohibited.
