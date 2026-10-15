"""Every Sympraxi URL must declare who may use it.

Adding a view without classifying it here fails `test_every_url_is_classified`.
That is the point: no page that touches student data ships by accident.
Third-party URLs (allauth, the Django admin) are excluded; the admin is
restricted to site administrators and sits behind the same 2FA gate.
"""

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from sympraxi.core import policy
from sympraxi.core.policy import Action
from tests.scheduling_world import World, build_world

PUBLIC = {"core:home", "core:healthz", "core:source"}

# Center-scoped pages, the action each one requires, and the object the action is
# checked against ("center", or an attribute of the test World).
CENTER_PAGES: dict[str, tuple[Action, str]] = {
    "centers:detail": (Action.VIEW_CENTER, "center"),
    "centers:members": (Action.VIEW_MEMBERS, "center"),
    "privacy:audit_log": (Action.VIEW_AUDIT_LOG, "center"),
    # Booking
    "scheduling:schedules": (Action.VIEW_SCHEDULES, "center"),
    "scheduling:open_times": (Action.VIEW_SCHEDULES, "schedule"),
    "scheduling:book": (Action.BOOK_OWN, "schedule"),
    "scheduling:staff_book": (Action.BOOK_FOR_OTHERS, "schedule"),
    "scheduling:day_sheet": (Action.VIEW_ALL_APPOINTMENTS, "schedule"),
    "scheduling:my_appointments": (Action.BOOK_OWN, "center"),
    "scheduling:my_tutoring": (Action.VIEW_OWN_TUTORING, "center"),
    "scheduling:appointment": (Action.VIEW_APPOINTMENT, "appointment"),
    "scheduling:appointment_ics": (Action.VIEW_APPOINTMENT, "appointment"),
    "scheduling:appointment_cancel": (Action.CANCEL_APPOINTMENT, "appointment"),
    "scheduling:appointment_change": (Action.CHANGE_APPOINTMENT, "appointment"),
    "scheduling:appointment_confirm": (Action.BOOK_FOR_OTHERS, "appointment"),
    # Setup
    "scheduling:schedule_create": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:manage": (Action.MANAGE_SCHEDULES, "schedule"),
    "scheduling:schedule_edit": (Action.MANAGE_SCHEDULES, "schedule"),
    "scheduling:assignment_create": (Action.MANAGE_SCHEDULES, "schedule"),
    "scheduling:assignment_edit": (Action.MANAGE_SCHEDULES, "assignment"),
    "scheduling:hours": (Action.MANAGE_SCHEDULES, "assignment"),
    "scheduling:exception_create": (Action.MANAGE_SCHEDULES, "assignment"),
    "scheduling:exception_delete": (Action.MANAGE_SCHEDULES, "exception"),
    "scheduling:terms": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:term_create": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:term_edit": (Action.MANAGE_SCHEDULES, "term"),
    "scheduling:closure_create": (Action.MANAGE_SCHEDULES, "term"),
    "scheduling:closure_delete": (Action.MANAGE_SCHEDULES, "closure"),
    "scheduling:rooms": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:room_create": (Action.MANAGE_SCHEDULES, "center"),
    "scheduling:room_edit": (Action.MANAGE_SCHEDULES, "room"),
    "scheduling:exemptions": (Action.MANAGE_EXEMPTIONS, "center"),
    "scheduling:exemption_create": (Action.MANAGE_EXEMPTIONS, "center"),
    "scheduling:exemption_revoke": (Action.MANAGE_EXEMPTIONS, "exemption"),
}

# The URL keyword for each kind of object.
URL_KWARGS = {
    "schedule": "schedule_id",
    "appointment": "appointment_id",
    "assignment": "assignment_id",
    "exception": "exception_id",
    "term": "term_id",
    "closure": "closure_id",
    "room": "room_id",
    "exemption": "exemption_id",
}

# Pages that need a chosen open time in the query string.
NEEDS_SLOT = {"scheduling:book", "scheduling:staff_book"}

ROLES = ["none", "student", "tutor", "front_desk", "director", "site_admin"]


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


def url_for(name: str, world: World) -> str:
    kind = CENTER_PAGES[name][1]
    kwargs = {"slug": world.center.slug}
    if kind != "center":
        kwargs[URL_KWARGS[kind]] = str(getattr(world, kind).pk)
    url = reverse(name, kwargs=kwargs)
    return f"{url}?{world.slot_query()}" if name in NEEDS_SLOT else url


def object_for(name: str, world: World) -> object:
    kind = CENTER_PAGES[name][1]
    return world.center if kind == "center" else getattr(world, kind)


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def test_every_url_is_classified() -> None:
    assert sympraxi_url_names() == PUBLIC | set(CENTER_PAGES)


@pytest.mark.parametrize("name", sorted(PUBLIC - {"core:source"}))
def test_public_pages_load_anonymously(client, db, name) -> None:
    assert client.get(reverse(name)).status_code == 200


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_anonymous_visitors_are_sent_to_sign_in(client, world, name) -> None:
    response = client.get(url_for(name, world))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("account_login"))


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_outsiders_get_404(client, person_with_role, world, other_center, name) -> None:
    outsider = person_with_role("director", other_center)
    client.force_login(outsider)
    assert client.get(url_for(name, world)).status_code == 404


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_each_role_gets_what_the_policy_allows(
    client, person_with_role, center, world, name, role
) -> None:
    """The view enforces the action it declares. The matrix test says who has it."""
    user = person_with_role(role, center)
    client.force_login(user)
    action, _ = CENTER_PAGES[name]
    obj = object_for(name, world)
    if policy.can(user, action, obj):
        expected = 200
    elif policy.can(user, action, center) or not (
        policy.roles_in(user, center) or user.is_superuser
    ):
        expected = 404  # theirs to see in general, but not this one; or not a member
    else:
        expected = 403
    assert client.get(url_for(name, world)).status_code == expected


def test_students_open_their_own_appointment(client, center) -> None:
    world = build_world(center)
    client.force_login(world.student)
    for name in ("scheduling:appointment", "scheduling:appointment_ics"):
        assert client.get(url_for(name, world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_cancel", world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_change", world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_confirm", world)).status_code == 403


def test_tutors_open_appointments_they_teach_but_cannot_cancel_them(client, center) -> None:
    world = build_world(center)
    client.force_login(world.tutor)
    assert client.get(url_for("scheduling:appointment", world)).status_code == 200
    assert client.get(url_for("scheduling:appointment_cancel", world)).status_code == 403


@pytest.mark.parametrize("name", sorted(CENTER_PAGES))
def test_directors_can_open_every_center_page_they_are_granted(
    client, person_with_role, world, name
) -> None:
    director = person_with_role("director", world.center)
    client.force_login(director)
    action, _ = CENTER_PAGES[name]
    expected = 200 if policy.can(director, action, object_for(name, world)) else 403
    assert client.get(url_for(name, world)).status_code == expected
