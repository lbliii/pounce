"""
Django ASGI compatibility tests — verify Django apps work through pounce.

Django ASGI requires careful setup: settings must be configured before
importing any Django modules. Tests use a minimal in-memory configuration
with no database to keep things fast and self-contained.

Exercises: async views, URL routing, JSON responses, middleware, and
exception handling through get_asgi_application().

"""

import json

import pytest

pytest.importorskip("django")

import os

# Configure Django settings before any Django imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.integration.frameworks._django_settings")

import django as django_mod

django_mod.setup()

from django.core.asgi import get_asgi_application  # noqa: E402
from django.http import HttpResponse, JsonResponse  # noqa: E402
from django.urls import path  # noqa: E402

# ---------------------------------------------------------------------------
# Django views — defined at module level so URL patterns can reference them
# ---------------------------------------------------------------------------


async def homepage_view(request):
    return HttpResponse("Hello from Django")


async def json_view(request):
    return JsonResponse({"message": "Hello from Django", "framework": "django"})


async def item_view(request, item_id):
    return JsonResponse({"item_id": item_id})


async def echo_method_view(request):
    return JsonResponse({"method": request.method, "path": request.path})


async def post_json_view(request):
    raw = await request.aread()
    body = json.loads(raw)
    return JsonResponse({"received": body}, status=201)


async def query_params_view(request):
    params = dict(request.GET)
    # Django QueryDict returns lists; flatten single-value params
    flat = {k: v[0] if len(v) == 1 else v for k, v in params.items()}
    return JsonResponse({"params": flat})


async def error_view(request):
    return JsonResponse({"error": "not found"}, status=404)


async def server_error_view(request):
    msg = "intentional error"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# URL configuration — used by _django_settings.ROOT_URLCONF
# ---------------------------------------------------------------------------

urlpatterns = [
    path("", homepage_view),
    path("json/", json_view),
    path("items/<int:item_id>/", item_view),
    path("echo/", echo_method_view),
    path("create/", post_json_view),
    path("search/", query_params_view),
    path("not-found/", error_view),
    path("error/", server_error_view),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _get_app():
    """Get the Django ASGI application."""
    return get_asgi_application()


class TestDjangoRouting:
    """Basic URL routing with async views."""

    def test_homepage(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.text == "Hello from Django"

    def test_json_response(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/json/")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Hello from Django", "framework": "django"}

    def test_path_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/items/42/")
        assert resp.status_code == 200
        assert resp.json() == {"item_id": 42}

    def test_method_echo(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/echo/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["method"] == "GET"
        assert data["path"] == "/echo/"

    def test_post_json_body(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_get_app())
        resp = http_client.post(
            f"http://{host}:{port}/create/",
            json={"name": "Widget"},
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 201
        assert resp.json() == {"received": {"name": "Widget"}}

    def test_query_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/search/?q=test&page=2")
        assert resp.status_code == 200
        assert resp.json() == {"params": {"q": "test", "page": "2"}}

    def test_not_found_django_404(self, pounce_server, http_client) -> None:
        """Django's own 404 handler for unknown URLs."""
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/nonexistent/")
        assert resp.status_code == 404

    def test_custom_404_view(self, pounce_server, http_client) -> None:
        """View that returns 404 status explicitly."""
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/not-found/")
        assert resp.status_code == 404
        assert resp.json() == {"error": "not found"}


class TestDjangoErrorHandling:
    """Django's error handling through ASGI."""

    def test_server_error(self, pounce_server, http_client) -> None:
        """Unhandled exception in view returns 500."""
        host, port = pounce_server(_get_app())
        resp = http_client.get(f"http://{host}:{port}/error/")
        assert resp.status_code == 500
