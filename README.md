# Price Refresh POC

Benchmarks two architectures for refreshing product prices from an external API:

| | System A (MQ) | System B (direct) |
|---|---|---|
| Concurrency | Celery workers consuming RabbitMQ | asyncio coroutines on a single event loop |
| Dispatch | `celery.group()` fan-out to `price_refresh_mq` | `asyncio.create_task()` fire-and-forget |
| Concurrency cap | `CELERYD_CONCURRENCY` (OS processes) | `DIRECT_MAX_CONCURRENCY` (asyncio.Semaphore) |
| Server | Gunicorn WSGI (gthread) | Gunicorn ASGI (UvicornWorker) — **required** |
| Business logic | `shared/price_refresh_service/` (hexagonal) | `shared/services_async.py` (async variant) |

Both systems share the same `Product` + `JobStatus` models, `MockPriceAPI`, and Redis job tracking.

---

## Quick start

```bash
cd poc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Start infrastructure (Postgres, Redis, RabbitMQ):**

```bash
docker compose up -d
```

**Apply migrations and seed products:**

```bash
export DATABASE_URL=postgres://poc:poc@localhost:5433/poc_db
export REDIS_URL=redis://localhost:6379/0
python manage.py migrate
python manage.py seed_products --count 1000
```

---

## Running the servers

### System B (asyncio) — ASGI required

```bash
export DATABASE_URL=postgres://poc:poc@localhost:5433/poc_db
export REDIS_URL=redis://localhost:6379/0
export DIRECT_MAX_CONCURRENCY=4
export MOCK_API_LATENCY_MS=80
./start_poc.sh async
```

`./start_poc.sh async` runs:
`gunicorn poc_project.asgi:application --worker-class uvicorn.workers.UvicornWorker --workers 1`

> **Why ASGI?** `asyncio.create_task()` in the `refresh` view only works when a running
> event loop persists after the HTTP response is sent. UvicornWorker provides this.
> `./start_poc.sh dev` (WSGI runserver) will raise `RuntimeError: no running event loop`
> the moment a request hits `create_task()`.

### System A (MQ) — Celery worker required

Start the Django server in any mode (A's views are sync):

```bash
./start_poc.sh async   # same server handles both systems
```

Then start the Celery worker in a separate terminal:

```bash
export DATABASE_URL=postgres://poc:poc@localhost:5433/poc_db
export REDIS_URL=redis://localhost:6379/0
export MOCK_API_LATENCY_MS=80
celery -A system_a_mq.celery_app worker \
  --queues=price_refresh_mq \
  --concurrency=4 \
  --prefetch-multiplier=1 \
  --loglevel=info
```

### Server modes reference

| Command | Worker | Use case |
|---------|--------|----------|
| `./start_poc.sh dev` | Django runserver | Dev only — System A works, System B crashes |
| `./start_poc.sh prod` | Gunicorn gthread | System A only — sync views |
| `./start_poc.sh async` | Gunicorn + UvicornWorker | **Both systems** — required for System B |

---

## Project layout

```
poc/
├── manage.py
├── requirements.txt
├── docker-compose.yml
├── start_poc.sh                    # dev / prod / async server modes
│
├── poc_project/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py                     # ASGI entry point (UvicornWorker)
│   └── wsgi.py
│
├── shared/                         # Shared across both systems
│   ├── models.py                   # Product, JobStatus
│   ├── mock_price_api.py           # get_price() sync + get_price_async() async
│   ├── redis_utils.py              # Sync + async Redis job tracking helpers
│   ├── services_async.py           # PriceServiceAsync (Django async ORM)
│   └── price_refresh_service/      # Hexagonal architecture (used by System A)
│       ├── type.py                 # RefreshResult dataclass, exception types
│       ├── repository.py           # PriceRefreshRepository Protocol (interface)
│       ├── service.py              # PriceRefreshService Protocol + Impl
│       └── io_django.py            # PostgresPriceRefreshRepository (concrete)
│
├── system_a_mq/                    # System A — Celery + RabbitMQ
│   ├── container.py                # DI container: wires Repository → Service
│   ├── celery_app.py
│   ├── tasks.py                    # Celery task: calls container.price_refresh_service
│   └── views.py                   # Sync DRF views
│
├── system_b_direct/                # System B — asyncio event loop
│   ├── executor.py                 # refresh_batch_async: asyncio.gather + Semaphore
│   └── views.py                   # Async Django views (JsonResponse, no @api_view)
│
└── load_test/
    ├── run_benchmark.py            # CLI benchmark runner
    └── README.md                   # Benchmark instructions and analysis
```

---

## Shared hexagonal architecture (System A)

System A uses a ports-and-adapters (hexagonal) pattern for the business logic layer:

```
container.py
    │
    ├── PostgresPriceRefreshRepository  (concrete, implements PriceRefreshRepository Protocol)
    │       └── Product.objects.get / filter().update  (Django ORM)
    │
    └── PriceRefreshServiceImpl  (concrete, implements PriceRefreshService Protocol)
            ├── repository.get_product(product_id)
            ├── MockPriceAPI.get_price(platform_identifier)
            └── repository.save_price(product_id, price, mrp)
                    └── returns RefreshResult(success, product_id, new_price)
```

The `PriceRefreshService` and `PriceRefreshRepository` are `typing.Protocol` classes —
structural subtyping means any class with the right methods satisfies the interface without
explicit inheritance.

---

## Mock external price API

`MockPriceAPI` runs in-process (no container). Prices are deterministic SHA-256 hashes of
`platform_identifier`, so the same product always gets the same price regardless of timing.

| Variable | Default | Description |
|----------|---------|-------------|
| `MOCK_API_LATENCY_MS` | `80` | Base simulated network latency |
| `MOCK_API_JITTER_MS` | `40` | Random ± jitter on latency |
| `MOCK_API_FAILURE_RATE` | `0.05` | Probability of simulated API failure per call |

System A uses the sync `get_price()` (blocks the Celery worker thread).
System B uses `get_price_async()` which calls `asyncio.sleep()` — yields the event loop
during the wait so all coroutines in a batch overlap in time.

---

## API endpoints

| System | Method | Path | Description |
|--------|--------|------|-------------|
| A | POST | `/api/a/refresh/` | Enqueue batch job via Celery |
| A | GET | `/api/a/jobs/{job_id}/` | Job status from Redis |
| A | GET | `/api/a/stats/` | Aggregate stats (DB) |
| B | POST | `/api/b/refresh/` | Submit batch job via asyncio |
| B | GET | `/api/b/jobs/{job_id}/` | Job status from Redis (async) |
| B | GET | `/api/b/stats/` | Aggregate stats (DB) |
| — | GET | `/api/health/` | DB + Redis connectivity check |

---

## Benchmarking

```bash
# Sequential — standard parity test
python load_test/run_benchmark.py \
  --mode sequential --batch-size 200 --rounds 3 \
  --workers 4 --latency-ms 80 --use-postgres

# Concurrent burst — 5 simultaneous jobs per system
python load_test/run_benchmark.py \
  --mode concurrent --batch-size 100 --concurrent-jobs 5 \
  --workers 4 --latency-ms 80 --use-postgres
```

See `load_test/README.md` for full analysis and what the numbers mean.

---

## Key benchmark findings

With `batch_size=200`, `concurrency=4`, `latency_ms=80`, Postgres:

| Scenario | System A wall_ms | System B wall_ms | Winner |
|---|---|---|---|
| Sequential job | ~5400ms | ~4700ms | **B ~15% faster** |
| Concurrent burst (5 jobs) | ~52000ms | ~14000ms | **B ~73% faster** |
| Dispatch latency | ~95ms | ~25ms | **B ~4× faster** |

System B's asyncio advantage is largest under concurrent burst because all jobs' coroutines
interleave on the single event loop — the 4-coroutine semaphore cap operates globally
across all concurrent jobs. System A's Celery queue serializes under burst because the
fixed worker pool is shared.
