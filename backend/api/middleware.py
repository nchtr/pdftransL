"""Minimal CORS middleware for the React dev server.

Kept dependency-free on purpose; swap for django-cors-headers if you
need credentials/preflight caching etc.
"""

from django.conf import settings
from django.http import HttpResponse


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
            response["Access-Control-Allow-Headers"] = "Content-Type, X-Requested-With"
        return response

    return middleware
