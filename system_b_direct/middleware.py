"""Route decorators for system_b_direct: Pydantic validation, DirectError → HTTP mapping."""

from functools import wraps

from pydantic import ValidationError
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.status import HTTP_200_OK, HTTP_400_BAD_REQUEST

from system_b_direct.exceptions import DirectError


def direct_post_route(request_model):
    """Validate POST body via Pydantic, map DirectError → HTTP, wrap result in Response."""

    def decorator(view_func):
        @api_view(["POST"])
        @wraps(view_func)
        def wrapper(request, **kwargs) -> Response:
            try:
                req = request_model.model_validate(request.data)
            except ValidationError as e:
                return Response({"error": str(e)}, status=HTTP_400_BAD_REQUEST)
            try:
                result = view_func(req, **kwargs)
            except DirectError as e:
                return Response({"error": e.message}, status=e.status_code)
            return result if isinstance(result, Response) else Response(result, status=HTTP_200_OK)

        return wrapper

    return decorator


def direct_get_route(view_func):
    """Map DirectError → HTTP for GET endpoints with path/query params."""

    @api_view(["GET"])
    @wraps(view_func)
    def wrapper(request, **kwargs) -> Response:
        try:
            result = view_func(request, **kwargs)
        except DirectError as e:
            return Response({"error": e.message}, status=e.status_code)
        return result if isinstance(result, Response) else Response(result, status=HTTP_200_OK)

    return wrapper
