"""Reminder emails: timing (across both daylight-saving changes), sending once,
canceled and moved appointments, catching up after downtime, who gets them, and
what they say.

Times are in America/New_York. Fall back: Sunday, November 1, 2026. Spring
forward: Sunday, March 14, 2027.
"""

import threading
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import override_settings

from sympraxi.centers.models import Role
from sympraxi.scheduling import reminders, services
from sympraxi.scheduling.models import (
    Appointment,
    Modality,
    Participant,
    Reminder,
)
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule

BASE = "https://writing.example.edu"
NY = ZoneInfo("America/New_York")
TUESDAY = date(2027, 1, 12)
FALL_BACK = date(2026, 11, 1)
SPRING_FORWARD = date(2027, 3, 14)


@pytest.fixture
def center(db):
    return CenterFactory(
        time_zone="America/New_York", name="Write Place", contact_email="help@example.edu"
    )


def setup(center, first: date, last: date, **schedule_fields):
    fields = {"reminder_1_minutes": 1440, "reminder_2_minutes": 60, **schedule_fields}
    schedule = make_schedule(center, first, last, **fields)
    tutor = make_member(center, Role.TUTOR, full_name="Tomás Tutor")
    assignment = assign(schedule, tutor, hours=((time(0), time(23, 30)),), capacity=3)
    student = make_member(center, Role.STUDENT, full_name="Sam Private-Name")
    return assignment, student


def booked(assignment, students, start: datetime, minutes: int = 30, **fields) -> Appointment:
    """An appointment booked a month before it starts, long before any reminder is due."""
    appointment = Appointment.objects.create(
        schedule=assignment.schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=minutes),
        blocked_until=start + timedelta(minutes=minutes),
        modality=Modality.IN_PERSON,
        capacity=max(1, len(students)),
        **fields,
    )
    for student in students:
        Participant.objects.create(appointment=appointment, student=student)
    long_ago = start - timedelta(days=30)
    Appointment.objects.filter(pk=appointment.pk).update(created_at=long_ago, updated_at=long_ago)
    Participant.objects.filter(appointment=appointment).update(added_at=long_ago)
    appointment.refresh_from_db()
    return appointment


def sweep(now: datetime) -> int:
    return reminders.send_due_reminders(base_url=BASE, now=now)


def recipients() -> list[str]:
    return sorted(m.to[0] for m in mail.outbox)


@pytest.fixture
def tuesday(center):
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    appointment = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    return appointment, student


# --- Sending once -------------------------------------------------------------------


def test_a_reminder_goes_out_once_when_it_is_due(tuesday) -> None:
    appointment, student = tuesday
    due = appointment.starts_at - timedelta(hours=24)
    assert sweep(due - timedelta(seconds=1)) == 0
    assert sweep(due) == 1
    assert recipients() == [student.email]
    # Every later sweep inside the catch-up window finds it already sent.
    for minutes in (1, 5, 14):
        assert sweep(due + timedelta(minutes=minutes)) == 0
    assert len(mail.outbox) == 1
    [sent] = Reminder.objects.all()
    assert (sent.minutes_before, sent.starts_at, sent.due_at) == (1440, appointment.starts_at, due)


def test_each_reminder_time_sends_its_own_email(tuesday) -> None:
    appointment, _student = tuesday
    start = appointment.starts_at
    assert sweep(start - timedelta(hours=24)) == 1
    assert sweep(start - timedelta(hours=1)) == 1
    assert sweep(start - timedelta(minutes=1)) == 0
    assert sorted(Reminder.objects.values_list("minutes_before", flat=True)) == [60, 1440]


def test_the_third_reminder_and_schedules_without_reminders(center) -> None:
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28), reminder_3_minutes=30)
    appointment = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    assert sweep(appointment.starts_at - timedelta(minutes=30)) == 1
    quiet = make_schedule(
        center,
        date(2027, 1, 4),
        date(2027, 2, 28),
        name="No reminders",
        reminder_1_minutes=None,
        reminder_2_minutes=None,
    )
    other = assign(quiet, hours=((time(0), time(23, 30)),))
    silent = booked(other, [student], at(quiet, TUESDAY, 14))
    for lead in (24 * 60, 60, 30):
        assert sweep(silent.starts_at - timedelta(minutes=lead)) == 0


def test_the_database_refuses_a_second_copy(tuesday) -> None:
    appointment, student = tuesday
    fields = {
        "appointment": appointment,
        "recipient": student,
        "minutes_before": 60,
        "starts_at": appointment.starts_at,
        "due_at": appointment.starts_at - timedelta(hours=1),
        "sent_at": appointment.starts_at - timedelta(hours=1),
    }
    Reminder.objects.create(**fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        Reminder.objects.create(**fields)


def test_a_schedule_cannot_send_two_reminders_at_the_same_time(center) -> None:
    schedule = make_schedule(center, date(2027, 1, 4), date(2027, 2, 28))
    schedule.reminder_2_minutes = schedule.reminder_1_minutes
    with pytest.raises(ValidationError):
        schedule.full_clean()


# --- Daylight saving ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "day_before_clock", "zone"),
    [(FALL_BACK, time(11), "EST"), (SPRING_FORWARD, time(9), "EDT")],
    ids=["fall", "spring"],
)
def test_reminders_count_real_hours_across_the_switch(center, day, day_before_clock, zone) -> None:
    """A 10 a.m. appointment on the switch day: the hour-before reminder comes at 9,
    and the day-before one 24 real hours earlier, which the clock calls 11 (fall)
    or 9 (spring)."""
    assignment, student = setup(center, day - timedelta(days=7), day + timedelta(days=7))
    appointment = booked(assignment, [student], at(assignment.schedule, day, 10))
    start = appointment.starts_at

    day_before = start - timedelta(hours=24)
    assert day_before.astimezone(NY).time() == day_before_clock
    assert sweep(day_before - timedelta(seconds=1)) == 0
    assert sweep(day_before) == 1

    hour_before = start - timedelta(hours=1)
    assert hour_before.astimezone(NY).time() == time(9)
    assert sweep(hour_before - timedelta(seconds=1)) == 0
    assert sweep(hour_before) == 1

    for message in mail.outbox:
        assert "10 a.m." in message.body
        assert zone in message.body


def test_a_moved_appointment_is_reminded_at_its_new_time_across_the_switch(center) -> None:
    """Moved from the Saturday before spring forward to the Monday after it."""
    assignment, student = setup(center, date(2027, 3, 1), date(2027, 3, 31))
    schedule = assignment.schedule
    appointment = booked(assignment, [student], at(schedule, date(2027, 3, 13), 10))
    old_day_before = appointment.starts_at - timedelta(hours=24)
    services.reschedule(
        actor=make_member(center, Role.FRONT_DESK),
        appointment=appointment,
        starts_at=at(schedule, date(2027, 3, 15), 10),
        minutes=30,
        as_staff=True,
        notify=False,
    )
    appointment.refresh_from_db()
    assert sweep(old_day_before) == 0
    new_day_before = appointment.starts_at - timedelta(hours=24)
    assert new_day_before == datetime(2027, 3, 14, 14, tzinfo=UTC)  # 10 a.m. EDT on Sunday
    assert sweep(new_day_before) == 1
    [message] = mail.outbox
    assert "Monday, March 15, 2027" in message.body
    assert "EDT" in message.body


# --- Canceled and moved appointments -------------------------------------------------


def test_never_for_a_canceled_appointment(tuesday) -> None:
    appointment, _student = tuesday
    services.cancel(
        actor=make_member(appointment.schedule.center, Role.FRONT_DESK),
        appointment=appointment,
        as_staff=True,
        notify=False,
    )
    start = appointment.starts_at
    assert sweep(start - timedelta(hours=24)) == 0
    assert sweep(start - timedelta(hours=1)) == 0
    assert mail.outbox == []


def test_a_student_who_left_a_group_gets_none(center) -> None:
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    other = make_member(center, Role.STUDENT, full_name="Lee Other-Student")
    appointment = booked(assignment, [student, other], at(assignment.schedule, TUESDAY, 10))
    Participant.objects.filter(appointment=appointment, student=other).update(
        canceled_at=appointment.starts_at - timedelta(days=3)
    )
    assert sweep(appointment.starts_at - timedelta(hours=24)) == 1
    assert recipients() == [student.email]


def test_a_moved_appointment_is_reminded_again_for_its_new_time(tuesday) -> None:
    appointment, _student = tuesday
    start = appointment.starts_at
    assert sweep(start - timedelta(hours=24)) == 1
    # Moved two days later, well before the new time's reminders are due.
    later = start + timedelta(days=2)
    Appointment.objects.filter(pk=appointment.pk).update(
        starts_at=later,
        ends_at=later + timedelta(minutes=30),
        blocked_until=later + timedelta(minutes=30),
        updated_at=start - timedelta(hours=12),
    )
    assert sweep(start - timedelta(hours=1)) == 0  # the old time's reminder doesn't go
    assert sweep(later - timedelta(hours=24)) == 1
    assert sweep(later - timedelta(hours=1)) == 1
    assert list(
        Reminder.objects.order_by("sent_at").values_list("starts_at", "minutes_before")
    ) == [(start, 1440), (later, 1440), (later, 60)]


def test_no_reminder_that_fell_due_before_a_booking_or_move(tuesday) -> None:
    """The confirmation email has just gone out; a reminder on its heels is noise."""
    appointment, _student = tuesday
    day_before = appointment.starts_at - timedelta(hours=24)
    Appointment.objects.filter(pk=appointment.pk).update(
        updated_at=day_before + timedelta(minutes=2)
    )
    assert sweep(day_before + timedelta(minutes=3)) == 0
    # The next reminder isn't affected.
    assert sweep(appointment.starts_at - timedelta(hours=1)) == 1


def test_a_student_who_joined_after_it_fell_due_gets_none(center) -> None:
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    late_joiner = make_member(center, Role.STUDENT)
    appointment = booked(assignment, [student, late_joiner], at(assignment.schedule, TUESDAY, 10))
    day_before = appointment.starts_at - timedelta(hours=24)
    Participant.objects.filter(appointment=appointment, student=late_joiner).update(
        added_at=day_before + timedelta(minutes=1)
    )
    assert sweep(day_before + timedelta(minutes=2)) == 1
    assert recipients() == [student.email]


def test_tentative_and_placeholder_appointments_get_none(center) -> None:
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    schedule = assignment.schedule
    tentative = booked(assignment, [student], at(schedule, TUESDAY, 10), is_tentative=True)
    booked(assignment, [], at(schedule, TUESDAY, 10, 30), is_placeholder=True)
    assert sweep(tentative.starts_at - timedelta(hours=24)) == 0
    assert sweep(tentative.starts_at - timedelta(hours=1)) == 0


# --- Downtime --------------------------------------------------------------------------


def test_a_late_sweep_catches_up_only_inside_the_window(center) -> None:
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    schedule = assignment.schedule
    a = booked(assignment, [student], at(schedule, TUESDAY, 10))
    b = booked(assignment, [student], at(schedule, TUESDAY, 11))
    # The scheduler comes back 14 minutes after a's day-before reminder was due.
    back = a.starts_at - timedelta(hours=24) + timedelta(minutes=14)
    assert sweep(back) == 1  # a's, 14 minutes late
    back = b.starts_at - timedelta(hours=24) + timedelta(minutes=16)
    assert sweep(back) == 0  # b's is 16 minutes late: skipped, and never sent
    assert sweep(back + timedelta(minutes=1)) == 0
    assert Reminder.objects.filter(appointment=b, minutes_before=1440).count() == 0
    # Its hour-before reminder still goes out on time.
    assert sweep(b.starts_at - timedelta(hours=1)) == 1


def test_a_long_outage_does_not_send_a_stack_of_stale_reminders(center) -> None:
    """Down for six hours: on its return the scheduler sends only what is due now."""
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    schedule = assignment.schedule
    for hour in range(9, 17):
        booked(assignment, [student], at(schedule, TUESDAY, hour))
    back = at(schedule, TUESDAY, 15)  # down since 9 a.m.
    sent = sweep(back)
    # Only the 4 p.m. appointment's hour-before reminder is due at 3 p.m.
    assert sent == 1
    [reminder] = Reminder.objects.all()
    assert reminder.minutes_before == 60
    assert reminder.starts_at == at(schedule, TUESDAY, 16)


# --- Who gets them -----------------------------------------------------------------


def test_students_can_turn_them_off(tuesday) -> None:
    appointment, student = tuesday
    student.reminders_for_appointments = False
    student.save()
    assert sweep(appointment.starts_at - timedelta(hours=24)) == 0
    assert mail.outbox == []


def test_tutors_get_them_only_if_they_turn_them_on(tuesday) -> None:
    appointment, student = tuesday
    tutor = appointment.tutor
    assert not tutor.reminders_for_tutoring
    assert sweep(appointment.starts_at - timedelta(hours=24)) == 1
    assert recipients() == [student.email]
    tutor.reminders_for_tutoring = True
    tutor.save()
    assert sweep(appointment.starts_at - timedelta(hours=1)) == 2
    assert tutor.email in recipients()


# --- What they say -------------------------------------------------------------------


def test_reminders_say_as_little_as_possible_about_other_people(center) -> None:
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    other = make_member(center, Role.STUDENT, full_name="Lee Other-Student")
    tutor = assignment.tutor
    tutor.reminders_for_tutoring = True
    tutor.save()
    appointment = booked(assignment, [student, other], at(assignment.schedule, TUESDAY, 10))
    assert sweep(appointment.starts_at - timedelta(hours=24)) == 3
    by_recipient = {m.to[0]: m for m in mail.outbox}

    to_student = by_recipient[student.email]
    assert to_student.subject == "[Sympraxi] Reminder: appointment Jan 12, 10 a.m."
    assert "reminder of your appointment at Write Place" in to_student.body
    assert "Tuesday, January 12, 2027, 10 a.m." in to_student.body
    assert "EST" in to_student.body
    assert "Tomás Tutor" in to_student.body
    assert f"{BASE}/centers/{center.slug}/appointments/{appointment.pk}/" in to_student.body
    assert f"{BASE}/accounts/reminders/" in to_student.body
    assert to_student.reply_to == ["help@example.edu"]
    assert "Lee" not in to_student.body and other.email not in to_student.body
    assert by_recipient[other.email].body.count("Sam") == 0

    to_tutor = by_recipient[tutor.email]
    assert "an appointment you're tutoring" in to_tutor.body
    for name in ("Sam", "Lee", student.email, other.email):
        assert name not in to_tutor.subject and name not in to_tutor.body

    for message in mail.outbox:
        assert "—" not in message.body and "—" not in message.subject
        assert message.attachments == []


def test_inside_the_change_cutoff_the_reminder_points_to_the_center(center) -> None:
    assignment, student = setup(
        center, date(2027, 1, 4), date(2027, 2, 28), change_cutoff_minutes=120
    )
    appointment = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    sweep(appointment.starts_at - timedelta(hours=1))
    [message] = mail.outbox
    assert "cancel it if you can't make it" not in message.body
    assert "If you can't make it, let the center know at help@example.edu." in message.body


@override_settings(
    MAILERS={
        "default": {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"},
        "delivery": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    }
)
def test_reminders_go_through_the_queued_mailer(tuesday) -> None:
    appointment, student = tuesday
    assert sweep(appointment.starts_at - timedelta(hours=24)) == 1
    [message] = mail.outbox
    assert message.to == [student.email]


# --- Two schedulers at once --------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_schedulers_sweeping_at_once_send_each_reminder_once() -> None:
    center = CenterFactory(time_zone="America/New_York")
    assignment, student = setup(center, date(2027, 1, 4), date(2027, 2, 28))
    others = [make_member(center, Role.STUDENT) for _ in range(2)]
    tutor = assignment.tutor
    tutor.reminders_for_tutoring = True
    tutor.save()
    appointment = booked(assignment, [student, *others], at(assignment.schedule, TUESDAY, 10))
    now = appointment.starts_at - timedelta(hours=24)
    barrier = threading.Barrier(2)
    counts: list[int] = []
    lock = threading.Lock()

    def run() -> None:
        try:
            barrier.wait()
            sent = sweep(now)
        finally:
            connection.close()
        with lock:
            counts.append(sent)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    # Three students and the tutor: four emails between the two sweeps, one each.
    assert sum(counts) == 4
    assert recipients() == sorted([student.email, tutor.email, *(o.email for o in others)])
    assert Reminder.objects.count() == 4
