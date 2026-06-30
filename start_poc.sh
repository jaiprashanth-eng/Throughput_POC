#!/bin/bash
# Usage: ./start_poc.sh [dev|prod|async]
# dev   = Django runserver (single-threaded, no ASGI)
# prod  = Gunicorn gthread workers (system_a_mq / sync views only)
# async = Gunicorn + UvicornWorker (required for system_b_direct async views)

MODE=${1:-dev}

if [ "$MODE" = "prod" ]; then
  echo "Starting Gunicorn (gthread, 2 workers, 8 threads each)..."
  gunicorn poc_project.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --worker-class gthread \
    --threads 8 \
    --timeout 120 \
    --log-level info

elif [ "$MODE" = "async" ]; then
  echo "Starting Gunicorn + UvicornWorker (ASGI, 1 worker, asyncio event loop)..."
  # Single worker: all coroutines share one event loop — correct for asyncio.create_task().
  # Multiple workers are also fine (each has its own loop); job state lives in Redis.
  gunicorn poc_project.asgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 120 \
    --log-level info

else
  echo "Starting Django dev server (single-threaded)..."
  python manage.py runserver 0.0.0.0:8000
fi
