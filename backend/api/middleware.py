"""Мини-middleware без зависимостей: CORS и опциональный Bearer-токен.

Токен (PDFTRANSL_API_TOKEN) закрывает весь /api/; принимается и
?token= — EventSource не умеет ставить заголовки.
"""

from django.conf import settings
from django.http import HttpResponse, JsonResponse


def cors_middleware(get_response):
    allowed = set(settings.CORS_ALLOWED_ORIGINS)

    def middleware(request):
        origin = request.headers.get("Origin", "")
        if request.method == "OPTIONS" and origin in allowed:
            response = HttpResponse(status=204)
        else:
            response = get_response(request)
        if origin in allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response["Access-Control-Allow-Headers"] = (
                "Content-Type, X-Requested-With, Authorization"
            )
        return response

    return middleware


def token_auth_middleware(get_response):
    """Optional bearer-token gate for the API.

    Off by default (no PDFTRANSL_API_TOKEN set) — nothing changes for
    local development. When the token is set, every /api/ request must
    carry it: ``Authorization: Bearer <token>`` or ``?token=<token>``.
    """

    def middleware(request):
        token = settings.PDFTRANSL_API_TOKEN
        if token and request.path.startswith("/api/"):
            supplied = ""
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
            supplied = supplied or request.GET.get("token", "")
            if supplied != token:
                return JsonResponse(
                    {"error": "invalid or missing API token"}, status=401
                )
        return get_response(request)

    return middleware
