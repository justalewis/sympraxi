import os
from collections.abc import Callable

import pytest
from django.conf import settings
from django.test import Client

# Playwright's sync API runs an event loop in the test thread; the test's own
# database calls are still synchronous, so Django's async-safety check must be off.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture
def sign_in(live_server) -> Callable[..., None]:
    """Give a Playwright browser context a signed-in session for a user."""

    def _sign_in(context, user) -> None:
        client = Client()
        client.force_login(user)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME]
        context.add_cookies(
            [{"name": settings.SESSION_COOKIE_NAME, "value": cookie.value, "url": live_server.url}]
        )

    return _sign_in
