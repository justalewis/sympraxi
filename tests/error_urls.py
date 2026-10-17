"""The site's URLs plus one that fails, for testing the 500 page (tests/test_error_pages.py)."""

from django.http import HttpRequest, HttpResponse
from django.urls import path

from sympraxi.urls import handler500
from sympraxi.urls import urlpatterns as site_urlpatterns


def fail(request: HttpRequest) -> HttpResponse:
    raise RuntimeError("the database is unreachable at db:5432")


urlpatterns = [path("fail/", fail), *site_urlpatterns]

__all__ = ["handler500", "urlpatterns"]
