"""Private calendar feeds: what they list, what they never say, and the secret address."""

import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Membership, Role
from sympraxi.notify import feeds
from sympraxi.notify.models import CalendarFeed, feed_token_hash
from sympraxi.notify.views import NEW_ADDRESS
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Participant
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db


def feed_url(token: str) -> str:
    return reverse("notify:feed", kwargs={"token": token})


def turn_on(client, user) -> str:
    """Turn the feed on through the page and return the address it shows once."""
    client.force_login(user)
    client.post(reverse("notify:calendar"), {"action": "start"})
    address = client.session[NEW_ADDRESS]
    page = client.get(reverse("notify:calendar")).content.decode()
    assert address in page
    assert NEW_ADDRESS not in client.session
    return address.rsplit("/calendar/", 1)[1].removesuffix(".ics")


def test_a_student_subscribes_to_their_appointments(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    client.logout()
    response = client.get(feed_url(token))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/calendar")
    body = response.content.decode()
    assert body.count("BEGIN:VEVENT") == 1
    assert f"UID:{world.appointment.pk}@" in body
    assert world.tutor.display_name in body  # their own tutor's name
    assert "X-WR-CALNAME:" in body


def test_a_tutors_feed_never_names_students(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.tutor)
    body = client.get(feed_url(token)).content.decode()
    assert "tutoring appointment" in body
    assert world.student.display_name not in body
    assert world.student.full_name not in body
    assert world.student.email not in body


def test_canceled_far_off_and_turned_off_roles_are_left_out(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    appointment = Appointment.objects.filter(pk=world.appointment.pk)

    def events() -> int:
        return client.get(feed_url(token)).content.decode().count("BEGIN:VEVENT")

    appointment.update(status=AppointmentStatus.CANCELED)
    assert events() == 0
    appointment.update(status=world.appointment.status)
    assert events() == 1

    later = timezone.now() + feeds.AHEAD + timedelta(days=1)
    appointment.update(
        starts_at=later,
        ends_at=later + timedelta(minutes=30),
        blocked_until=later + timedelta(minutes=30),
    )
    assert events() == 0
    appointment.update(
        starts_at=world.appointment.starts_at,
        ends_at=world.appointment.ends_at,
        blocked_until=world.appointment.blocked_until,
    )
    assert events() == 1

    Membership.objects.filter(user=world.student, center=center).update(is_active=False)
    assert events() == 0


def test_a_student_who_left_a_group_doesnt_see_it(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    Participant.objects.filter(student=world.student).update(canceled_at=timezone.now())
    assert "BEGIN:VEVENT" not in client.get(feed_url(token)).content.decode()


def test_only_a_hash_of_the_address_is_kept(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    feed = CalendarFeed.objects.get(user=world.student)
    assert feed.token_hash == feed_token_hash(token)
    assert token not in str(list(CalendarFeed.objects.values()))
    assert token not in str(list(AuditEvent.objects.values()))


def test_replacing_the_address_stops_the_old_one(client, center) -> None:
    world = build_world(center)
    old = turn_on(client, world.student)
    new = turn_on(client, world.student)
    assert old != new
    assert client.get(feed_url(old)).status_code == 404
    assert client.get(feed_url(new)).status_code == 200
    actions = [e.action for e in AuditEvent.objects.filter(actor=world.student).order_by("id")]
    assert AuditAction.CALENDAR_FEED_STARTED in actions
    assert AuditAction.CALENDAR_FEED_REPLACED in actions


def test_turning_the_feed_off(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    client.post(reverse("notify:calendar"), {"action": "off"})
    assert not CalendarFeed.objects.exists()
    assert client.get(feed_url(token)).status_code == 404
    assert AuditEvent.objects.filter(action=AuditAction.CALENDAR_FEED_TURNED_OFF).exists()


def test_a_turned_off_account_has_no_feed(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    world.student.is_active = False
    world.student.save()
    assert client.get(feed_url(token)).status_code == 404


def test_the_feed_notes_when_it_was_last_used_at_most_hourly(client, center) -> None:
    world = build_world(center)
    token = turn_on(client, world.student)
    client.get(feed_url(token))
    first = CalendarFeed.objects.get().last_used_at
    assert first is not None
    client.get(feed_url(token))
    assert CalendarFeed.objects.get().last_used_at == first


def test_the_feed_works_for_staff_without_two_factor(client, center, settings) -> None:
    """Calendar apps fetch the address without signing in, and a signed-in tutor who
    hasn't set up two-factor login isn't sent to set it up."""
    settings.SYMPRAXI_REQUIRE_STAFF_MFA = True
    tutor = make_member(center, Role.TUTOR)
    token = feeds.start_feed(tutor)
    tutor.authenticator_set.all().delete()
    client.force_login(tutor)
    assert client.get(feed_url(token)).status_code == 200


def test_the_page_says_the_address_once(client, center) -> None:
    world = build_world(center)
    turn_on(client, world.student)
    page = client.get(reverse("notify:calendar")).content.decode()
    assert not re.search(r"/calendar/[A-Za-z0-9_-]{40,}\.ics", page)
    assert "Replace the address" in page
