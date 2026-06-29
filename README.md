# Price Refresh POC — Shared Foundation

Self-contained Django POC for comparing two architectures that refresh product prices:

- **System A (MQ)** — Celery + RabbitMQ (`system_a_mq/`)
- **System B (direct)** — ThreadPoolExecutor, no broker (`system_b_direct/`)

Both systems will share `PriceService`, `MockPriceAPI`, models, and Redis job tracking.

## Quick start (SQLite, no Docker)

```bash
cd poc
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_products --count 5000
python manage.py runserver
```

Health check: `GET http://localhost:8000/api/health/`

## Docker services (Postgres, Redis, RabbitMQ)

```bash
cd poc
docker compose up -d
```

Then point Django at Postgres:

```bash
export DATABASE_URL=postgres://poc:poc@localhost:5433/poc_db
export REDIS_URL=redis://localhost:6379/0
export CELERY_BROKER_URL=amqp://guest:guest@localhost:5672//
export CELERY_RESULT_BACKEND=redis://localhost:6379/1
python manage.py migrate
python manage.py seed_products --count 5000
python manage.py runserver
```

RabbitMQ management UI: http://localhost:15672 (guest / guest)

## Mock external price API

`MockPriceAPI` runs in-process (no separate container). Configure via environment:

| Variable | Default | Description |
|----------|---------|-------------|
| `MOCK_API_LATENCY_MS` | `80` | Base simulated network latency |
| `MOCK_API_JITTER_MS` | `40` | Random ± jitter on latency |
| `MOCK_API_FAILURE_RATE` | `0.05` | Probability of simulated API failure |

## Project layout

```
poc/
  manage.py
  poc_project/          # Django project settings & URLs
  shared/
    models.py           # Product, JobStatus
    services.py         # PriceService (shared business logic)
    mock_price_api.py   # Simulated external API
    redis_utils.py      # Redis job status helpers
  requirements.txt
  docker-compose.yml
```

## Management commands

```bash
python manage.py seed_products --count 5000
```

Creates products with `platform_identifier` values like `PROD_0001`. Idempotent via `get_or_create`.
