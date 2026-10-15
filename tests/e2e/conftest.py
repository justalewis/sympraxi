import os
from collections.abc import Callable

import pytest
from django.conf import settings
from django.test import Client

# Playwright's sync API runs an event loop in the test thread; the test's own
# database calls are still synchronous, so Django's async-safety check must be off.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict) -> dict:
    """Keep the test browser on this machine.

    Every host except localhost fails to resolve, so a page that tries to reach
    the internet (an SSO redirect that slips past `route()`, say) fails instead
    of sending anything out. Sympraxi's pages load nothing from other hosts.
    """
    args = [*browser_type_launch_args.get("args", []), RESOLVE_ONLY_LOCALHOST]
    return {**browser_type_launch_args, "args": args}


RESOLVE_ONLY_LOCALHOST = "--host-resolver-rules=MAP * ~NOTFOUND , EXCLUDE localhost"


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
