"""The booking rules, one at a time. `test_booking_properties.py` mixes them at random."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import services
from sympraxi.scheduling.availability import open_times
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AvailabilityException,
    Closure,
    ExceptionKind,
    Exemption,
    Modality,
    Room,
    Visibility,
)
from sympraxi.scheduling.services import BookingError
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule

pytestmark = pytest.mark.django_db

MONDAY = date(2027, 1, 11)
TUESDAY = MONDAY + timedelta(days=1)
WEDNESDAY = MONDAY + timedelta(days=2)
THURSDAY = MONDAY + timedelta(days=3)
NEXT_TUESDAY = TUESDAY + timedelta(days=7)
NOW = datetime(2027, 1, 11, 13, tzinfo=UTC)  # Monday 8:00 in New York


@pytest.fixture
def center():
    return CenterFactory(time_zone="America/New_York")


@pytest.fixture
def schedule(center):
    return make_schedule(
        center,
        date(2027, 1, 4),
        date(2027, 2, 28),
        opens_at=time(8),
        closes_at=time(20),
        min_notice_minutes=60,
        horizon_days=14,
        change_cutoff_minutes=120,
    )


@pytest.fixture
def tutor(schedule):
    room = Room.objects.create(center=schedule.center, name="Room 1")
    return assign(
        schedule,
        hours=((time(9), time(17)),),
        room=room,
        offers_online=True,
        meeting_url="https://meet.example.edu/a",
    )


@pytest.fixture
def student(center):
    return make_member(center, Role.STUDENT)


@pytest.fixture
def staff(center):
    return make_member(center, Role.FRONT_DESK)


def book(actor, assignment, day: date, hour: int, minute: int = 0, **kwargs: Any) -> Appointment:
    kwargs.setdefault("minutes", 30)
    kwargs.setdefault("modality", Modality.IN_PERSON)
    kwargs.setdefault("students", [actor])
    kwargs.setdefault("as_staff", False)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("notify", False)
    return services.book(
        actor=actor,
        assignment=assignment,
        starts_at=at(assignment.schedule, day, hour, minute),
        **kwargs,
    )


def refused(code: str, *args: Any, **kwargs: Any) -> None:
    with pytest.raises(BookingError) as exc:
        book(*args, **kwargs)
    assert exc.value.code == code, exc.value.message


# --- Booking and double-booking ------------------------------------------------


def test_a_student_books_an_open_time(tutor, student) -> None:
    appointment = book(student, tutor, TUESDAY, 10)
    assert appointment.is_active
    assert list(appointment.active_participants().values_list("student", flat=True)) == [student.pk]
    assert appointment.room == tutor.room
    assert appointment.meeting_url == ""
    assert appointment.blocked_until == appointment.ends_at
    assert AuditEvent.objects.filter(
        action=AuditAction.APPOINTMENT_BOOKED, target_id=str(appointment.pk)
    ).exists()
    starts = [t.starts_at for t in open_times(tutor.schedule, TUESDAY, 30)]
    assert appointment.starts_at not in starts


def test_online_appointments_get_the_meeting_link(tutor, student) -> None:
    appointment = book(student, tutor, TUESDAY, 10, modality=Modality.ONLINE)
    assert appointment.meeting_url == "https://meet.example.edu/a"
    assert appointment.room is None


def test_two_students_cannot_book_the_same_tutor_at_once(tutor, student, center) -> None:
    book(student, tutor, TUESDAY, 10)
    other = make_member(center, Role.STUDENT)
    refused("slot_taken", other, tutor, TUESDAY, 10)
    refused("slot_taken", other, tutor, TUESDAY, 9, 30, minutes=60)


def test_the_break_after_an_appointment_keeps_the_tutor_free(schedule, tutor, student, center):
    schedule.buffer_minutes = 15
    schedule.save()
    first = book(student, tutor, TUESDAY, 10)
    assert first.blocked_until == first.ends_at + timedelta(minutes=15)
    other = make_member(center, Role.STUDENT)
    refused("slot_taken", other, tutor, TUESDAY, 10, 30)
    book(other, tutor, TUESDAY, 11)


def test_the_database_refuses_overlaps_even_without_the_service(tutor, student) -> None:
    first = book(student, tutor, TUESDAY, 10)

    def clone(**changes: Any) -> Appointment:
        values = {
            "schedule": first.schedule,
            "assignment": first.assignment,
            "tutor": first.tutor,
            "starts_at": first.starts_at + timedelta(minutes=15),
            "ends_at": first.ends_at + timedelta(minutes=15),
            "blocked_until": first.ends_at + timedelta(minutes=15),
            "modality": Modality.IN_PERSON,
        }
        values.update(changes)
        return Appointment.objects.create(**values)

    overlap = "scheduling_appointment_no_tutor_overlap"
    with pytest.raises(IntegrityError, match=overlap), transaction.atomic():
        clone()
    # Canceled appointments don't hold the time, and back-to-back is fine.
    clone(status=AppointmentStatus.CANCELED)
    clone(
        starts_at=first.ends_at,
        ends_at=first.ends_at + timedelta(minutes=30),
        blocked_until=first.ends_at + timedelta(minutes=30),
    )


def test_a_canceled_appointment_frees_the_time(tutor, student, center) -> None:
    appointment = book(student, tutor, TUESDAY, 10)
    services.cancel(actor=student, appointment=appointment, as_staff=False, now=NOW, notify=False)
    other = make_member(center, Role.STUDENT)
    book(other, tutor, TUESDAY, 10)


def test_one_tutor_on_two_schedules_cannot_be_double_booked(schedule, tutor, student, center):
    evening = make_schedule(center, schedule.starts_on, schedule.ends_on, name="Online")
    same_person = assign(evening, tutor.tutor, hours=((time(9), time(17)),))
    book(student, tutor, TUESDAY, 10)
    other = make_member(center, Role.STUDENT)
    refused("slot_taken", other, same_person, TUESDAY, 10)
    book(other, same_person, TUESDAY, 11)


# --- When and how ----------------------------------------------------------------


def test_times_outside_the_tutors_hours_or_off_the_grid_are_refused(tutor, student) -> None:
    refused("outside_hours", student, tutor, TUESDAY, 8, 30)  # before the tutor starts
    refused("outside_hours", student, tutor, TUESDAY, 16, 30, minutes=60)  # runs past 17:00
    refused("outside_hours", student, tutor, TUESDAY, 10, 15)  # not on the 30-minute grid
    refused("outside_hours", student, tutor, date(2027, 3, 2), 10)  # after the schedule ends


def test_closures_blackouts_and_extra_hours(schedule, tutor, student) -> None:
    Closure.objects.create(term=schedule.term, label="Snow day", starts_on=TUESDAY, ends_on=TUESDAY)
    refused("outside_hours", student, tutor, TUESDAY, 10)
    AvailabilityException.objects.create(
        assignment=tutor,
        kind=ExceptionKind.BLACKOUT,
        starts_at=at(schedule, WEDNESDAY, 9),
        ends_at=at(schedule, WEDNESDAY, 12),
    )
    refused("outside_hours", student, tutor, WEDNESDAY, 11, 30)
    AvailabilityException.objects.create(
        assignment=tutor,
        kind=ExceptionKind.EXTRA,
        starts_at=at(schedule, WEDNESDAY, 17),
        ends_at=at(schedule, WEDNESDAY, 19),
    )
    book(student, tutor, WEDNESDAY, 18)
    # Extra hours never reach past the schedule's closing time.
    AvailabilityException.objects.create(
        assignment=tutor,
        kind=ExceptionKind.EXTRA,
        starts_at=at(schedule, THURSDAY, 19),
        ends_at=at(schedule, THURSDAY, 21),
    )
    refused("outside_hours", student, tutor, THURSDAY, 20)


def test_lengths_and_ways_of_meeting(schedule, center, student) -> None:
    in_person_only = assign(schedule, hours=((time(9), time(17)),), min_minutes=60, max_minutes=60)
    refused("length", student, in_person_only, TUESDAY, 10, minutes=30)
    refused("modality", student, in_person_only, TUESDAY, 10, minutes=60, modality=Modality.ONLINE)
    book(student, in_person_only, TUESDAY, 10, minutes=60)


def test_minimum_notice_and_booking_horizon(tutor, student) -> None:
    refused("notice", student, tutor, MONDAY, 9, now=NOW + timedelta(minutes=1))
    book(student, tutor, MONDAY, 9)  # exactly an hour's notice is enough
    book(student, tutor, MONDAY + timedelta(days=14), 10)
    refused("horizon", student, tutor, MONDAY + timedelta(days=15), 10)


def test_staff_are_not_held_to_notice_or_horizon(tutor, student, staff) -> None:
    book(staff, tutor, MONDAY, 9, students=[student], as_staff=True, now=NOW + timedelta(hours=1))
    book(staff, tutor, MONDAY + timedelta(days=30), 10, students=[student], as_staff=True)


def test_staff_only_and_hidden_schedules(schedule, tutor, student, staff) -> None:
    schedule.visibility = Visibility.STAFF
    schedule.save()
    refused("not_allowed", student, tutor, TUESDAY, 10)
    book(staff, tutor, TUESDAY, 10, students=[student], as_staff=True)
    schedule.visibility = Visibility.HIDDEN
    schedule.save()
    refused("not_allowed", staff, tutor, TUESDAY, 11, students=[student], as_staff=True)


# --- Who --------------------------------------------------------------------------


def test_students_book_only_for_themselves(tutor, student, center) -> None:
    other = make_member(center, Role.STUDENT)
    refused("not_allowed", student, tutor, TUESDAY, 10, students=[other])
    refused("not_allowed", student, tutor, TUESDAY, 10, as_staff=True)


def test_only_students_of_the_center_can_be_booked(tutor, staff) -> None:
    outsider = make_member(CenterFactory(), Role.STUDENT)
    refused("not_member", staff, tutor, TUESDAY, 10, students=[outsider], as_staff=True)


def test_a_student_cannot_be_in_two_places_at_once(schedule, tutor, student) -> None:
    book(student, tutor, TUESDAY, 10)
    other_tutor = assign(schedule, hours=((time(9), time(17)),))
    refused("student_busy", student, other_tutor, TUESDAY, 10)
    refused("student_busy", student, other_tutor, TUESDAY, 9, 30, minutes=60)


# --- Limits ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("limits", "allowed", "attempt", "code"),
    [
        ({"max_per_day": 1}, [(TUESDAY, 10, 30)], (TUESDAY, 14, 30), "limit_day"),
        (
            {"max_per_week": 2},
            [(TUESDAY, 10, 30), (WEDNESDAY, 10, 30)],
            (THURSDAY, 10, 30),
            "limit_week",
        ),
        (
            {"max_per_term": 2},
            [(TUESDAY, 10, 30), (NEXT_TUESDAY, 10, 30)],
            (THURSDAY, 10, 30),
            "limit_term",
        ),
        ({"max_minutes_per_day": 60}, [(TUESDAY, 10, 60)], (TUESDAY, 14, 30), "minutes_day"),
        (
            {"max_minutes_per_week": 90},
            [(TUESDAY, 10, 60), (WEDNESDAY, 10, 30)],
            (THURSDAY, 10, 30),
            "minutes_week",
        ),
        (
            {"max_minutes_per_term": 60},
            [(TUESDAY, 10, 60)],
            (NEXT_TUESDAY, 10, 30),
            "minutes_term",
        ),
    ],
    ids=["per-day", "per-week", "per-term", "minutes-day", "minutes-week", "minutes-term"],
)
def test_limits(schedule, tutor, student, limits, allowed, attempt, code) -> None:
    for name, value in limits.items():
        setattr(schedule, name, value)
    schedule.save()
    for day, hour, minutes in allowed:
        book(student, tutor, day, hour, minutes=minutes)
    day, hour, minutes = attempt
    refused(code, student, tutor, day, hour, minutes=minutes)


def test_weekly_limits_reset_on_monday(schedule, tutor, student) -> None:
    schedule.max_per_week = 1
    schedule.save()
    book(student, tutor, date(2027, 1, 17), 10)  # Sunday
    book(student, tutor, date(2027, 1, 18), 10)  # Monday


def test_canceled_and_placeholder_appointments_do_not_count(schedule, tutor, student, staff):
    schedule.max_per_day = 1
    schedule.save()
    first = book(student, tutor, TUESDAY, 10)
    services.cancel(actor=student, appointment=first, as_staff=False, now=NOW, notify=False)
    book(staff, tutor, TUESDAY, 11, students=[], as_staff=True, is_placeholder=True)
    book(student, tutor, TUESDAY, 14)


def test_limits_count_other_schedules_only_when_asked(schedule, tutor, student, center) -> None:
    other = make_schedule(center, schedule.starts_on, schedule.ends_on, name="Online")
    other_tutor = assign(other, hours=((time(9), time(17)),))
    schedule.max_per_day = 1
    schedule.save()
    book(student, other_tutor, TUESDAY, 10)
    book(student, tutor, TUESDAY, 14)  # this schedule counts only its own

    schedule.limits_span_center = True
    schedule.save()
    book(student, other_tutor, WEDNESDAY, 10)
    refused("limit_day", student, tutor, WEDNESDAY, 14)


def test_exemptions_lift_limits_for_appointments_through_their_date(schedule, tutor, student):
    schedule.max_per_day = 1
    schedule.save()
    Exemption.objects.create(center=schedule.center, student=student, expires_on=TUESDAY)
    book(student, tutor, TUESDAY, 10)
    book(student, tutor, TUESDAY, 14)
    book(student, tutor, WEDNESDAY, 10)
    refused("limit_day", student, tutor, WEDNESDAY, 14)


def test_exemptions_for_one_schedule_and_ended_exemptions(schedule, tutor, student, center):
    schedule.max_per_day = 1
    schedule.save()
    other = make_schedule(center, schedule.starts_on, schedule.ends_on, name="Online")
    Exemption.objects.create(center=center, student=student, schedule=other, expires_on=THURSDAY)
    ended = Exemption.objects.create(center=center, student=student, expires_on=THURSDAY)
    ended.revoked_at = timezone.now()
    ended.save()
    book(student, tutor, TUESDAY, 10)
    refused("limit_day", student, tutor, TUESDAY, 14)


def test_staff_are_held_to_limits_unless_they_override(schedule, tutor, student, staff) -> None:
    schedule.max_per_day = 1
    schedule.save()
    book(student, tutor, TUESDAY, 10)
    refused("limit_day", staff, tutor, TUESDAY, 14, students=[student], as_staff=True)
    appointment = book(
        staff, tutor, TUESDAY, 14, students=[student], as_staff=True, override_limits=True
    )
    event = AuditEvent.objects.get(target_id=str(appointment.pk))
    assert event.details["override_limits"] is True
    assert event.details["staff"] is True


def test_students_cannot_override(schedule, tutor, student) -> None:
    schedule.max_per_day = 1
    schedule.save()
    book(student, tutor, TUESDAY, 10)
    refused("limit_day", student, tutor, TUESDAY, 14, override_limits=True)


def test_tutor_caps(schedule, center) -> None:
    capped = assign(schedule, hours=((time(9), time(17)),), max_per_day=1, max_per_week=2)
    students = [make_member(center, Role.STUDENT) for _ in range(4)]
    book(students[0], capped, TUESDAY, 10)
    refused("tutor_cap", students[1], capped, TUESDAY, 14)
    book(students[1], capped, WEDNESDAY, 10)
    refused("tutor_cap", students[2], capped, THURSDAY, 10)
    book(students[3], capped, NEXT_TUESDAY, 10)


# --- Groups, placeholders, tentative ---------------------------------------------


def test_group_appointments_fill_their_seats_then_refuse(schedule, center) -> None:
    group = assign(schedule, hours=((time(9), time(17)),), capacity=2)
    first, second, third = (make_member(center, Role.STUDENT) for _ in range(3))
    appointment = book(first, group, TUESDAY, 10)
    joined = book(second, group, TUESDAY, 10)
    assert joined.pk == appointment.pk
    assert appointment.active_participants().count() == 2
    refused("full", third, group, TUESDAY, 10)
    refused("slot_taken", third, group, TUESDAY, 9, 30, minutes=60)  # overlaps the group
    seats = [t for t in open_times(schedule, TUESDAY, 30) if t.appointment is not None]
    assert seats == []


def test_open_times_list_group_seats(schedule, center) -> None:
    group = assign(schedule, hours=((time(9), time(17)),), capacity=3)
    student = make_member(center, Role.STUDENT)
    appointment = book(student, group, TUESDAY, 10)
    seats = [t for t in open_times(schedule, TUESDAY, 30) if t.appointment is not None]
    assert [(t.appointment, t.seats_left) for t in seats] == [(appointment, 2)]


def test_a_student_cannot_join_the_same_group_twice(schedule, center, staff) -> None:
    group = assign(schedule, hours=((time(9), time(17)),), capacity=3)
    student = make_member(center, Role.STUDENT)
    book(student, group, TUESDAY, 10)
    refused("student_busy", staff, group, TUESDAY, 10, students=[student], as_staff=True)


def test_leaving_a_group_keeps_it_for_the_others(schedule, center) -> None:
    group = assign(schedule, hours=((time(9), time(17)),), capacity=2)
    first, second = (make_member(center, Role.STUDENT) for _ in range(2))
    appointment = book(first, group, TUESDAY, 10)
    book(second, group, TUESDAY, 10)
    services.cancel(actor=first, appointment=appointment, as_staff=False, now=NOW, notify=False)
    appointment.refresh_from_db()
    assert appointment.is_active
    assert list(appointment.active_participants().values_list("student", flat=True)) == [second.pk]
    services.cancel(actor=second, appointment=appointment, as_staff=False, now=NOW, notify=False)
    appointment.refresh_from_db()
    assert not appointment.is_active


def test_placeholders_hold_time_without_a_student(tutor, student, staff) -> None:
    hold = book(staff, tutor, TUESDAY, 10, students=[], as_staff=True, is_placeholder=True)
    assert hold.is_placeholder and not hold.participants.exists()
    refused("slot_taken", student, tutor, TUESDAY, 10)
    refused("placeholder", staff, tutor, TUESDAY, 11, as_staff=True, is_placeholder=True)
    refused("no_student", staff, tutor, TUESDAY, 11, students=[], as_staff=True)
    refused("not_allowed", student, tutor, TUESDAY, 11, students=[], is_placeholder=True)


def test_tentative_bookings_wait_for_staff(schedule, tutor, student, staff) -> None:
    tutor.needs_confirmation = True
    tutor.save()
    appointment = book(student, tutor, TUESDAY, 10)
    assert appointment.is_tentative
    with pytest.raises(BookingError):
        services.confirm(actor=student, appointment=appointment, notify=False)
    services.confirm(actor=staff, appointment=appointment, notify=False)
    appointment.refresh_from_db()
    assert not appointment.is_tentative
    assert appointment.sequence == 1
    # Staff bookings aren't tentative unless staff say so.
    other = make_member(schedule.center, Role.STUDENT)
    assert not book(staff, tutor, TUESDAY, 11, students=[other], as_staff=True).is_tentative


# --- Canceling and changing ---------------------------------------------------------


def test_students_cannot_cancel_inside_the_cutoff_but_staff_can(tutor, student, staff) -> None:
    appointment = book(student, tutor, MONDAY, 11)  # three hours away; cutoff is two
    late = NOW + timedelta(hours=1, minutes=30)
    with pytest.raises(BookingError) as exc:
        services.cancel(
            actor=student, appointment=appointment, as_staff=False, now=late, notify=False
        )
    assert exc.value.code == "cutoff"
    services.cancel(actor=staff, appointment=appointment, as_staff=True, now=late, notify=False)
    appointment.refresh_from_db()
    assert appointment.status == AppointmentStatus.CANCELED
    assert appointment.canceled_by == staff


def test_students_cannot_cancel_other_students(tutor, student, center) -> None:
    appointment = book(student, tutor, TUESDAY, 10)
    other = make_member(center, Role.STUDENT)
    with pytest.raises(BookingError):
        services.cancel(actor=other, appointment=appointment, as_staff=False, now=NOW)


def test_reschedule_moves_the_appointment(schedule, tutor, student) -> None:
    appointment = book(student, tutor, TUESDAY, 10)
    moved = services.reschedule(
        actor=student,
        appointment=appointment,
        starts_at=at(schedule, WEDNESDAY, 11),
        minutes=60,
        as_staff=False,
        now=NOW,
        notify=False,
    )
    assert moved.pk == appointment.pk
    assert moved.starts_at == at(schedule, WEDNESDAY, 11)
    assert moved.minutes == 60
    assert moved.sequence == 1


def test_reschedule_checks_limits_without_counting_itself(schedule, tutor, student) -> None:
    schedule.max_per_day = 1
    schedule.save()
    appointment = book(student, tutor, TUESDAY, 10)
    book(student, tutor, WEDNESDAY, 10)
    services.reschedule(
        actor=student,
        appointment=appointment,
        starts_at=at(schedule, TUESDAY, 14),
        minutes=30,
        as_staff=False,
        now=NOW,
        notify=False,
    )
    with pytest.raises(BookingError) as exc:
        services.reschedule(
            actor=student,
            appointment=appointment,
            starts_at=at(schedule, WEDNESDAY, 14),
            minutes=30,
            as_staff=False,
            now=NOW,
            notify=False,
        )
    assert exc.value.code == "limit_day"


def test_reschedule_cannot_move_onto_a_taken_time(schedule, tutor, student, center) -> None:
    other = make_member(center, Role.STUDENT)
    book(other, tutor, TUESDAY, 11)
    appointment = book(student, tutor, TUESDAY, 10)
    with pytest.raises(BookingError) as exc:
        services.reschedule(
            actor=student,
            appointment=appointment,
            starts_at=at(schedule, TUESDAY, 10, 30),
            minutes=60,
            as_staff=False,
            now=NOW,
            notify=False,
        )
    assert exc.value.code == "slot_taken"


def test_a_series_is_all_or_nothing(schedule, tutor, student, staff, center) -> None:
    other = make_member(center, Role.STUDENT)
    book(other, tutor, NEXT_TUESDAY, 10)
    with pytest.raises(BookingError) as exc:
        services.book_series(
            weeks=3,
            actor=staff,
            assignment=tutor,
            starts_at=at(schedule, TUESDAY, 10),
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[student],
            as_staff=True,
            notify=False,
        )
    assert exc.value.code == "slot_taken"
    assert "19, 2027" in exc.value.message
    assert not Appointment.objects.filter(participants__student=student).exists()


def test_only_staff_book_series(tutor, student) -> None:
    with pytest.raises(BookingError) as exc:
        services.book_series(
            weeks=2,
            actor=student,
            assignment=tutor,
            starts_at=at(tutor.schedule, TUESDAY, 10),
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[student],
            as_staff=False,
            now=NOW,
        )
    assert exc.value.code == "not_allowed"
