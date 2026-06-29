#!/bin/bash
# Usage: ./start_poc.sh [dev|prod]
# dev  = Django runserver (current behavior, single-threaded)
# prod = Gunicorn with gthread workers (production-realistic)

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
else
  echo "Starting Django dev server (single-threaded)..."
  python manage.py runserver 0.0.0.0:8000
fi
