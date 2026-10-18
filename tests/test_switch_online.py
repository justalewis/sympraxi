"""Moving a day's in-person appointments online: the review, the move, the emails,
and what stays put."""

from datetime import time, timedelta

import pytest
from django.core import mail
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Modality
from sympraxi.scheduling.timeutils import local_date
from tests.scheduling_world import assign, build_world, make_member, make_schedule

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    return build_world(center)  # an in-person appointment tomorrow, tutor has a link


@pytest.fixture
def director(world):
    return make_member(world.center, Role.DIRECTOR)


def day_of(world) -> str:
    return local_date(world.appointment.starts_at, world.schedule.tz).isoformat()


def url(world) -> str:
    return reverse("scheduling:switch_online", kwargs={"slug": world.center.slug})


def test_the_review_shows_what_moves_and_changes_nothing(client, world, director) -> None:
    client.force_login(director)
    page = client.post(url(world), {"day": day_of(world), "review": "1"}).content.decode()
    assert "Move 1 appointment online" in page
    assert world.tutor.display_name in page
    world.appointment.refresh_from_db()
    assert world.appointment.modality == Modality.IN_PERSON


def test_confirming_moves_it_online_and_emails_everyone(
    client, world, director, django_capture_on_commit_callbacks
) -> None:
    client.force_login(director)
    before = world.appointment.sequence
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            url(world),
            {"day": day_of(world), "note": "The library is closed for snow.", "confirm": "1"},
        )
    assert response.status_code == 302
    world.appointment.refresh_from_db()
    assert world.appointment.modality == Modality.ONLINE
    assert world.appointment.meeting_url == world.assignment.meeting_url
    assert world.appointment.room is None
    assert world.appointment.sequence == before + 1
    [event] = AuditEvent.objects.filter(action=AuditAction.APPOINTMENT_CHANGED)
    assert event.details["via"] == "switch_online"
    assert sorted(m.to[0] for m in mail.outbox) == sorted([world.student.email, world.tutor.email])
    for message in mail.outbox:
        assert "moved online" in message.subject
        assert "The library is closed for snow." in message.body
        assert world.assignment.meeting_url in message.body
        [(name, ics, _type)] = message.attachments
        assert name == "appointment.ics" and f"SEQUENCE:{before + 1}" in ics


def test_tutors_without_a_link_are_listed_and_left_in_person(client, world, director) -> None:
    world.assignment.meeting_url = ""
    world.assignment.offers_online = False
    world.assignment.save()
    client.force_login(director)
    page = client.post(url(world), {"day": day_of(world), "review": "1"}).content.decode()
    assert "Tutors without a meeting link" in page
    assert "Nothing on that day can move online." in page
    client.post(url(world), {"day": day_of(world), "confirm": "1"})
    world.appointment.refresh_from_db()
    assert world.appointment.modality == Modality.IN_PERSON


def test_other_days_canceled_and_online_appointments_stay_put(client, world, director) -> None:
    later = Appointment.objects.create(
        schedule=world.schedule,
        assignment=world.assignment,
        tutor=world.tutor,
        starts_at=world.appointment.starts_at + timedelta(days=1),
        ends_at=world.appointment.ends_at + timedelta(days=1),
        blocked_until=world.appointment.blocked_until + timedelta(days=1),
        modality=Modality.IN_PERSON,
    )
    canceled = Appointment.objects.create(
        schedule=world.schedule,
        assignment=world.assignment,
        tutor=world.tutor,
        starts_at=world.appointment.starts_at + timedelta(hours=2),
        ends_at=world.appointment.ends_at + timedelta(hours=2),
        blocked_until=world.appointment.blocked_until + timedelta(hours=2),
        modality=Modality.IN_PERSON,
        status=AppointmentStatus.CANCELED,
    )
    client.force_login(director)
    client.post(url(world), {"day": day_of(world), "confirm": "1"})
    for kept in (later, canceled):
        kept.refresh_from_db()
        assert kept.modality == Modality.IN_PERSON
    world.appointment.refresh_from_db()
    assert world.appointment.modality == Modality.ONLINE


def test_one_schedule_only(client, world, director) -> None:
    second = make_schedule(
        world.center, world.schedule.starts_on, world.schedule.ends_on, name="Second"
    )
    tutor = make_member(world.center, Role.TUTOR)
    assignment = assign(
        second, tutor, hours=((time(0), time(23, 30)),), meeting_url="https://meet.example.edu/2"
    )
    other = Appointment.objects.create(
        schedule=second,
        assignment=assignment,
        tutor=tutor,
        starts_at=world.appointment.starts_at,
        ends_at=world.appointment.ends_at,
        blocked_until=world.appointment.blocked_until,
        modality=Modality.IN_PERSON,
    )
    client.force_login(director)
    client.post(url(world), {"day": day_of(world), "schedule": str(second.pk), "confirm": "1"})
    world.appointment.refresh_from_db()
    other.refresh_from_db()
    assert world.appointment.modality == Modality.IN_PERSON
    assert other.modality == Modality.ONLINE
    assert other.meeting_url == "https://meet.example.edu/2"


def test_placeholders_move_without_emails(
    client, world, director, django_capture_on_commit_callbacks
) -> None:
    Appointment.objects.filter(pk=world.appointment.pk).update(is_placeholder=True)
    world.appointment.participants.all().delete()
    client.force_login(director)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(url(world), {"day": day_of(world), "confirm": "1"})
    world.appointment.refresh_from_db()
    assert world.appointment.modality == Modality.ONLINE
    assert mail.outbox == []


def test_a_day_that_is_over_is_refused(client, world, director) -> None:
    client.force_login(director)
    yesterday = local_date(world.appointment.starts_at, world.schedule.tz) - timedelta(days=2)
    response = client.post(url(world), {"day": yesterday.isoformat(), "review": "1"})
    assert "That day is over." in response.content.decode()


def test_only_directors_move_a_day(client, world) -> None:
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(url(world)).status_code == 403
