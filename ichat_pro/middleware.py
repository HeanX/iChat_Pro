"""API authentication middleware (P4 T31 / fixes R-06).

API paths must answer with JSON, never with the HTML login redirect:
an expired session used to produce a 302 -> login page, which XHR clients
parsed as garbage or misread as success. Registered under the T31 error
contract as ``authentication_required`` (HTTP 401 / WS close 4401).
"""

from django.http import JsonResponse

from chat.errors import get_error

API_PREFIX = "/api/"


class ApiAuthMiddleware:
    """Return 401 JSON for unauthenticated /api/ requests."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(API_PREFIX) and not request.user.is_authenticated:
            error = get_error("authentication_required")
            return JsonResponse(
                {"error": error.code, "detail": error.message},
                status=error.http_status,
            )
        return self.get_response(request)
