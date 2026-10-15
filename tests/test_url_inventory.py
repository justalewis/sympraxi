"""Every Sympraxi URL must declare who may use it.

Adding a view without classifying it here fails `test_every_url_is_classified`.
That is the point: no page that touches student data ships by accident.
Third-party URLs (allauth, the Django admin) are excluded; the admin is
restricted to site administrators and sits behind the same 2FA gate.
"""

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from sympraxi.core.policy import Action

PUBLIC = {"core:home", "core:healthz", "core:source"}

# Center-scoped pages and the action each one requires.
CENTER_PAGES = {
    "centers:detail": Action.VIEW_CENTER,
    "centers:members": Action.VIEW_MEMBERS,
    "privacy:audit_log": Action.VIEW_AUDIT_LOG,
}


def sympraxi_url_names() -> set[str]:
    names: set[str] = set()

    def walk(patterns: list[URLPattern | URLResolver], namespaces: list[str]) -> None:
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns, [*namespaces, p.namespace] if p.namespace else namespaces)
            elif p.callback.__module__.startswith("sympraxi."):
                names.add(":".join([*namespaces, p.name or "<unnamed>"]))

    walk(get_resolver().url_patterns, [])
    return names


def test_every_url_is_classified() -> None:
    assert sympraxi_url_names() == PUBLIC | set(CENTER_PAGES)


@pytest.mark.parametrize("name", sorted(PUBLIC - {"core:source"}))
def test_public_pages_load_anonymously(client, db, name) -> None:
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_anonymous_visitors_are_sent_to_sign_in(client, center, name) -> None:
    response = client.get(reverse(name, kwargs={"slug": center.slug}))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_outsiders_get_404(client, person_with_role, center, other_center, name) -> None:
    outsider = person_with_role("director", other_center)
    client.force_login(outsider)
    assert client.get(reverse(name, kwargs={"slug": center.slug})).status_code == 404


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_students_get_only_what_their_role_allows(client, person_with_role, center, name) -> None:
    client.force_login(person_with_role("student", center))
    expected = 200 if CENTER_PAGES[name] == Action.VIEW_CENTER else 403
    assert client.get(reverse(name, kwargs={"slug": center.slug})).status_code == expected


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_directors_can_open_every_center_page(client, person_with_role, center, name) -> None:
    client.force_login(person_with_role("director", center))
    assert client.get(reverse(name, kwargs={"slug": center.slug})).status_code == 200
