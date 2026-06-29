from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response

from shared.redis_utils import _get_client


@api_view(["GET"])
def health_check(request):
    db_ok = False
    redis_ok = False

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            db_ok = cursor.fetchone()[0] == 1
    except Exception:
        db_ok = False

    try:
        redis_ok = _get_client().ping()
    except Exception:
        redis_ok = False

    return Response({"status": "ok", "db": db_ok, "redis": redis_ok})
