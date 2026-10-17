"""Error pages use the site's layout and give nothing away."""

import html
import re
import uuid

import pytest
from django.test import Client
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.core.views import server_error
from tests.factories import PASSWORD, CenterFactory
from tests.scheduling_world import World, build_world, make_member

pytestmark = pytest.mark.django_db

LAYOUT = ("Skip to main content", 'aria-label="Main"', "Get the source code")


def in_layout(content: str) -> bool:
    return all(part in content for part in LAYOUT)


def without_tokens(content: str) -> str:
    """The page with its per-response CSRF token and CSP nonce taken out."""
    content = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", content)
    return re.sub(r'nonce="[^"]*"', "", content)


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def test_an_unknown_address_gets_the_404_page(client) -> None:
    response = client.get("/no-such-page-xyz/")
    content = response.content.decode()
    assert response.status_code == 404
    assert in_layout(content)
    assert "<title>Page not found | Sympraxi</title>" in content
    assert "Go to the home page" in content
    assert "no-such-page-xyz" not in content


def test_someone_elses_appointment_looks_like_one_that_does_not_exist(client, world) -> None:
    client.force_login(make_member(world.center, Role.STUDENT))
    url = "/centers/{}/appointments/{}/".format
    theirs = client.get(url(world.center.slug, world.appointment.pk))
    missing = client.get(url(world.center.slug, uuid.uuid4()))
    elsewhere = client.get(url(CenterFactory().slug, world.appointment.pk))
    assert theirs.status_code == missing.status_code == elsewhere.status_code == 404
    page = without_tokens(theirs.content.decode())
    assert page == without_tokens(missing.content.decode())
    assert page == without_tokens(elsewhere.content.decode())
    assert in_layout(page)
    assert "Go to My centers" in page
    assert str(world.appointment.pk) not in page


def test_a_member_whose_role_lacks_a_page_gets_the_403_page(client, world) -> None:
    client.force_login(make_member(world.center, Role.STUDENT))
    response = client.get(reverse("centers:members", kwargs={"slug": world.center.slug}))
    content = html.unescape(response.content.decode())
    assert response.status_code == 403
    assert in_layout(content)
    assert "This page isn't part of your role" in content


def test_a_failed_csrf_check_gets_its_own_page_without_the_reason(world) -> None:
    client = Client(enforce_csrf_checks=True)
    response = client.post(reverse("account_login"), {"login": "x@example.edu", "password": "x"})
    content = response.content.decode()
    assert response.status_code == 403
    assert in_layout(content)
    assert "This form has expired" in content
    assert "CSRF" not in content
    assert "cookie not set" not in content


@pytest.mark.urls("tests.error_urls")
def test_a_server_error_gets_the_500_page_without_the_details(world) -> None:
    client = Client(raise_request_exception=False)
    client.force_login(make_member(world.center, Role.STUDENT))
    response = client.get("/fail/")
    content = response.content.decode()
    assert response.status_code == 500
    assert in_layout(content)
    assert "<title>Something went wrong | Sympraxi</title>" in content
    assert "db:5432" not in content
    assert "RuntimeError" not in content


def test_the_500_page_needs_nothing_from_the_request(rf) -> None:
    response = server_error(rf.get("/"))
    content = response.content.decode()
    assert response.status_code == 500
    assert in_layout(content)
    # It can't tell who is signed in, so it offers neither "Sign in" nor "Sign out".
    assert "Sign in" not in content
    assert "Sign out" not in content
    assert 'href="/"' in content


def test_signing_in_after_an_expired_form_still_works(world) -> None:
    """The CSRF page is only a page: the next real attempt goes through."""
    user = make_member(world.center, Role.STUDENT)
    client = Client(enforce_csrf_checks=True)
    client.get(reverse("account_login"))
    token = client.cookies["csrftoken"].value
    response = client.post(
        reverse("account_login"),
        {"login": user.email, "password": PASSWORD, "csrfmiddlewaretoken": token},
    )
    assert response.status_code == 302
