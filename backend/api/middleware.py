"""Мини-middleware без зависимостей: CORS и опциональный Bearer-токен.

Токен (PDFTRANSL_API_TOKEN) закрывает весь /api/; принимается и
?token= для ссылок на скачивание, где нельзя поставить Authorization-заголовок.
"""

import secrets

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils.cache import patch_vary_headers


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
            patch_vary_headers(response, ("Origin",))
        return response

    return middleware


def token_auth_middleware(get_response):
    """Optional bearer-token gate for the API.

    Off by default (no PDFTRANSL_API_TOKEN set) — nothing changes for
    local development. When the token is set, every /api/ request must
    carry it: ``Authorization: Bearer <token>`` or ``?token=<token>``.
    """

    def middleware(request):
        tokens: dict[str, str] = {}
        raw = getattr(settings, "PDFTRANSL_API_TOKENS", "")
        for entry in raw.split(","):
            owner, separator, token = entry.strip().partition(":")
            if separator and owner and token:
                tokens[owner] = token
        if not tokens and settings.PDFTRANSL_API_TOKEN:
            tokens["default"] = settings.PDFTRANSL_API_TOKEN
        if request.method == "OPTIONS":
            return get_response(request)
        if tokens and request.path.startswith("/api/"):
            supplied = ""
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
            owner = next(
                (name for name, token in tokens.items()
                 if secrets.compare_digest(supplied, token)),
                None,
            )
            if owner is None:
                return JsonResponse(
                    {"error": "invalid or missing API token"}, status=401
                )
            request.pdftransl_owner = owner
            request.pdftransl_is_admin = owner in settings.PDFTRANSL_ADMIN_OWNERS
        else:
            request.pdftransl_owner = "local"
            request.pdftransl_is_admin = True
        return get_response(request)

    return middleware
