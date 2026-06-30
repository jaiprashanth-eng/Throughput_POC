# Load Test — System A vs System B Benchmark

Compares MQ-based (System A) and direct thread-pool (System B) price refresh under
controlled, identical conditions.

## Prerequisites

1. **Infrastructure running**
   ```bash
   cd poc
   docker compose up -d
   ```

2. **Django server (ASGI required for System B)**
   ```bash
   source .venv/bin/activate
   export REDIS_URL=redis://localhost:6379/0
   export CELERYD_CONCURRENCY=4
   export DIRECT_MAX_CONCURRENCY=4
   export MOCK_API_LATENCY_MS=80
   ./start_poc.sh async
   ```

   IMPORTANT: System B now uses asyncio, not a thread pool.
   DIRECT_WORKER_THREADS no longer exists — concurrency is unbounded by
   default (every coroutine in a batch runs concurrently on one event
   loop, gated only by Postgres/Redis connection pool limits, not a
   worker count). ./start_poc.sh dev (the WSGI dev server) will crash
   System B with "RuntimeError: no running event loop" the moment a
   request hits asyncio.create_task() — do not use dev mode for any
   System B testing from this point forward.

3. **Celery worker** (required for System A only)
   ```bash
   source .venv/bin/activate
   export REDIS_URL=redis://localhost:6379/0
   export CELERYD_CONCURRENCY=4
   export MOCK_API_LATENCY_MS=80
   celery -A system_a_mq.celery_app worker \
     --queues=price_refresh_mq \
     --concurrency=${CELERYD_CONCURRENCY:-4} \
     --loglevel=info \
     --prefetch-multiplier=1
   ```

4. **Install benchmark dependency**
   ```bash
   pip install requests
   ```

## Production-mode server (required for concurrent benchmarks)

For concurrent load testing (`--mode concurrent`), always use prod mode:

```bash
cd poc
source .venv/bin/activate
export REDIS_URL=redis://localhost:6379/0
export DIRECT_WORKER_THREADS=4
export MOCK_API_LATENCY_MS=80
./start_poc.sh prod
```

For sequential benchmarks, dev mode is fine:

```bash
./start_poc.sh dev
```

> **Why prod mode matters for concurrent tests:** The Django dev server serializes
> requests, meaning System B's concurrent job submissions queue at the HTTP layer
> and start sequentially — not simultaneously. Under Gunicorn gthread, concurrent
> POSTs are handled in parallel and the thread pool receives all submissions at
> once, which is the scenario that shows MQ's back-pressure advantage.

## Running a fair benchmark

All three variables must match across systems:

| Variable | System A | System B |
|----------|----------|----------|
| Concurrency | `CELERYD_CONCURRENCY` | `DIRECT_WORKER_THREADS` |
| Mock API latency | `MOCK_API_LATENCY_MS` | `MOCK_API_LATENCY_MS` |
| Batch size | `--batch-size` (same IDs for both) | `--batch-size` |

The script seeds products, then runs `--rounds` iterations **per system**, alternating
A → B → A → B to reduce warm-up skew. Each round uses the **same** `product_ids`
list for both systems.

```bash
cd poc
python load_test/run_benchmark.py \
  --batch-size 200 \
  --rounds 5 \
  --workers 4 \
  --latency-ms 80 \
  --base-url http://localhost:8000
```

Results are printed as a comparison table and saved to `load_test/results.json`.

## What the numbers mean

| Metric | Meaning |
|--------|---------|
| **dispatch_ms** | Time from HTTP request received to response returned. Measures enqueue/dispatch overhead only — should stay low (<50ms) regardless of batch size. |
| **wall_time_ms** | End-to-end time from job created to last item finished (stored in Redis/JobStatus). This is the metric that matters for throughput. |
| **items/s** | `total / (wall_time_ms / 1000)` — effective throughput for that job. |
| **p95 wall_ms** | 95th percentile wall time across rounds — stability under repeated load. |

### MQ overhead

**Dispatch overhead (System A):** RabbitMQ publish + Celery task scheduling adds
latency to `dispatch_ms` and introduces a gap between job creation and first task
execution (`first_task_start_latency_ms` in Celery logs).

**Processing overhead (System A):** Each item is a separate Celery message —
serialization, broker round-trip, and worker prefetch behavior add per-item cost
beyond the shared `PriceService` + mock API time.

**System B** submits directly to an in-process thread pool — no broker, no message
serialization. Dispatch is faster; first-thread start is near-immediate.

On **small batches** with low mock latency, System B often wins on raw wall time
because MQ fixed costs dominate.

On **large batches** with high external API latency, both systems spend most time
in `PriceService` — results converge, and MQ advantages (durability, retry,
back-pressure, horizontal scaling) matter more than raw speed.

## Why MQ may lose on small batches but win elsewhere

| Scenario | System B (direct) | System A (MQ) |
|----------|-------------------|---------------|
| Small batch, low latency | Faster — no broker tax | Slower — per-message overhead |
| Large batch, high latency | Good if threads suffice | Comparable throughput |
| Worker crash mid-job | In-flight items lost | Messages requeued / retried |
| Traffic spike | Thread pool backs up in-process | Queue absorbs back-pressure |
| Scale-out | Limited to one process | Add more Celery workers |

## Variables that change the outcome

1. **`MOCK_API_LATENCY_MS`** — Higher latency → both systems spend more time in
   `PriceService`; MQ overhead becomes a smaller fraction of wall time.

2. **`--batch-size`** — Larger batches amplify per-item MQ cost on System A;
   System B thread pool queues work in-memory.

3. **`--workers` / concurrency** — Must match (`DIRECT_WORKER_THREADS ==
   CELERYD_CONCURRENCY`) for fair comparison. Too few workers → both systems
   under-utilize; too many → contention on DB and mock API.

4. **`MOCK_API_JITTER_MS` / `MOCK_API_FAILURE_RATE`** — Affect per-item duration
   and retry behavior (System A retries via Celery; System B marks failure immediately).

## CLI reference

```
--batch-size  INT   Product IDs per job (default: 200)
--rounds      INT   Rounds per system (default: 5)
--workers     INT   Concurrency parity hint (default: 4)
--base-url    STR   Django server URL (default: http://localhost:8000)
--latency-ms  INT   MOCK_API_LATENCY_MS hint (default: 80)
```

## Output

Example table:

```
System     | Rounds | Avg dispatch_ms | Avg wall_time_ms | p95 wall_ms | Avg items/s
-------------------------------------------------------------------------------------
System A   |   5    |      12ms       |     4320ms       |   4800ms    |   231/s
System B   |   5    |       8ms       |     3100ms       |   3400ms    |   322/s
```

Full per-round data: `load_test/results.json`
