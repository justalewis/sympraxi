"""Reminders to tutors about session records they haven't written: when they're due,
who doesn't need a record, one email per tutor, and each appointment once."""

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Membership, Role
from sympraxi.records import reminders
from sympraxi.records.models import RecordReminder
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Attendance, Participant
from tests.records_world import appointment_for, make_topics, write_record
from tests.scheduling_world import assign, build_world, make_member

pytestmark = pytest.mark.django_db

HOUR = 1 / 24


@pytest.fixture
def world(center):
    make_topics()
    return build_world(center)


def sweep() -> int:
    return reminders.send_record_reminders(base_url="https://wc.example.edu")


def ended(world, hours_ago: float, **fields):
    """A 30-minute appointment with the world's student that ended `hours_ago`."""
    return appointment_for(world, world.student, days=-(hours_ago + 0.5) * HOUR, **fields)


def test_a_tutor_is_reminded_a_day_after_the_session(world) -> None:
    appointment = ended(world, 25)
    assert sweep() == 1
    [message] = mail.outbox
    assert message.to == [world.tutor.email]
    assert "A session record to write" in message.subject
    assert world.student.display_name in message.body
    assert (
        reverse(
            "scheduling:appointment",
            kwargs={"slug": world.center.slug, "appointment_id": appointment.pk},
        )
        in message.body
    )
    assert reverse("records:to_write", kwargs={"slug": world.center.slug}) in message.body
    assert RecordReminder.objects.get().appointment == appointment


def test_each_appointment_is_mentioned_once(world) -> None:
    ended(world, 25)
    assert sweep() == 1
    assert sweep() == 0
    assert len(mail.outbox) == 1


def test_not_before_it_is_due(world) -> None:
    ended(world, 3)
    assert sweep() == 0


def test_the_center_chooses_when_or_turns_them_off(world) -> None:
    ended(world, 3)
    world.center.record_reminder_hours = 2
    world.center.save()
    assert sweep() == 1
    world.center.record_reminder_hours = None
    world.center.save()
    appointment_for(world, world.student, days=-(26 + 0.5) * HOUR)
    assert sweep() == 0


def test_a_final_record_means_no_reminder_but_a_draft_still_does(world) -> None:
    written = ended(world, 25)
    write_record(written, world.student)
    drafted = ended(world, 27)
    write_record(drafted, world.student, final=False)
    assert sweep() == 1
    assert [r.appointment for r in RecordReminder.objects.all()] == [drafted]


def test_no_shows_and_students_who_left_need_no_record(world) -> None:
    no_show = ended(world, 25)
    Participant.objects.filter(appointment=no_show).update(attendance=Attendance.NO_SHOW)
    left = ended(world, 27)
    Participant.objects.filter(appointment=left).update(canceled_at=timezone.now())
    assert sweep() == 0


def test_canceled_appointments_and_placeholders_get_none(world) -> None:
    canceled = ended(world, 25)
    Appointment.objects.filter(pk=canceled.pk).update(status=AppointmentStatus.CANCELED)
    placeholder = ended(world, 27)
    Appointment.objects.filter(pk=placeholder.pk).update(is_placeholder=True)
    assert sweep() == 0


def test_old_sessions_are_not_dug_up(world) -> None:
    ended(world, 24 + reminders.LOOK_BACK.total_seconds() / 3600 + 2)
    assert sweep() == 0


def test_one_email_per_tutor_for_everything_due(world) -> None:
    ended(world, 25)
    ended(world, 27)
    other_tutor = make_member(world.center, Role.TUTOR)
    ended(world, 29, tutor=other_tutor, assignment=assign(world.schedule, other_tutor))
    assert sweep() == 2
    by_tutor = {m.to[0]: m for m in mail.outbox}
    assert "2 session records to write" in by_tutor[world.tutor.email].subject
    assert "A session record to write" in by_tutor[other_tutor.email].subject


def test_a_group_lists_only_the_students_still_missing_a_record(world) -> None:
    second = make_member(world.center, Role.STUDENT, full_name="Riley Second")
    group = appointment_for(world, [world.student, second], days=-(25 + 0.5) * HOUR, capacity=2)
    write_record(group, world.student)
    assert sweep() == 1
    [message] = mail.outbox
    [session] = [line for line in message.body.splitlines() if line.startswith("- ")]
    assert "Riley Second" in session
    assert world.student.display_name not in session


def test_tutors_whose_role_is_off_get_none(world) -> None:
    ended(world, 25)
    Membership.objects.filter(user=world.tutor, role=Role.TUTOR).update(is_active=False)
    assert sweep() == 0


def test_the_scheduler_runs_it(world) -> None:
    from sympraxi.scheduling import scheduler

    ended(world, 25)
    result = scheduler.sweep(base_url="https://wc.example.edu")
    assert result.record_reminders == 1


def test_directors_choose_on_the_record_settings_page(client, world) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    url = reverse("records:settings", kwargs={"slug": world.center.slug})
    assert "remind tutors about records" in client.get(url).content.decode().lower()
    client.post(url, {"record_reminder_hours": "4"})
    world.center.refresh_from_db()
    assert world.center.record_reminder_hours == 4
    client.post(url, {"record_reminder_hours": ""})
    world.center.refresh_from_db()
    assert world.center.record_reminder_hours is None
