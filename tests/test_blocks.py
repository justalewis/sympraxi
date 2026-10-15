"""Booking blocks: center and schedule limits, counting within a term or from a
date, waivers, expiry, lifting, what a blocked student can still do, and a race."""

import threading
from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.core import mail
from django.db import connection

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import attendance, blocks, services
from sympraxi.scheduling.models import (
    Appointment,
    Attendance,
    Block,
    BlockReason,
    Modality,
    Participant,
)
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule, make_term

pytestmark = pytest.mark.django_db

FALL = (date(2026, 9, 1), date(2026, 12, 18))
SPRING = (date(2027, 1, 11), date(2027, 5, 7))


@pytest.fixture
def center():
    return CenterFactory(
        time_zone="America/New_York", name="Write Place", contact_email="help@example.edu"
    )


@pytest.fixture
def terms(center):
    return make_term(center, *FALL), make_term(center, *SPRING)


@pytest.fixture
def tutor(center):
    return make_member(center, Role.TUTOR, full_name="Tomás Tutor")


@pytest.fixture
def desk(center):
    return make_member(center, Role.FRONT_DESK)


@pytest.fixture
def student(center):
    return make_member(center, Role.STUDENT, full_name="Sam Private-Name")


def schedule_in(term, tutor, name="Drop-in", **fields):
    schedule = make_schedule(term.center, term.starts_on, term.ends_on, name=name, **fields)
    return assign(schedule, tutor, hours=((time(0), time(23, 30)),))


@pytest.fixture
def spring(terms, tutor):
    return schedule_in(terms[1], tutor)


def booked(assignment, student, start: datetime) -> Appointment:
    a = Appointment.objects.create(
        schedule=assignment.schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        blocked_until=start + timedelta(minutes=30),
        modality=Modality.IN_PERSON,
    )
    Participant.objects.create(appointment=a, student=student)
    return a


def no_show(assignment, student, day: date, actor, hour: int = 10, **kwargs) -> Appointment:
    """A confirmed no-show, recorded 20 minutes after the start."""
    a = booked(assignment, student, at(assignment.schedule, day, hour))
    attendance.mark(
        actor=actor,
        appointment=a,
        marks={student.pk: Attendance.NO_SHOW},
        now=a.starts_at + timedelta(minutes=20),
        notify=False,
        **kwargs,
    )
    return a


def book_own(assignment, student, day: date, now: datetime) -> Appointment:
    return services.book(
        actor=student,
        assignment=assignment,
        starts_at=at(assignment.schedule, day, 15),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[student],
        as_staff=False,
        now=now,
        notify=False,
    )


FEB_1 = date(2027, 2, 1)
FEB_2 = date(2027, 2, 2)
FEB_10 = date(2027, 2, 10)
NOW = datetime(2027, 2, 3, 15, tzinfo=UTC)


# --- Reaching a limit -------------------------------------------------------------------


def test_a_center_limit_pauses_booking_on_every_schedule(
    center, spring, terms, tutor, desk, student
) -> None:
    center.no_show_block_limit = 2
    center.save()
    other = schedule_in(terms[1], tutor, name="Evening")
    no_show(spring, student, FEB_1, desk)
    assert not Block.objects.exists()
    no_show(other, student, FEB_2, desk)  # the center's limit counts every schedule
    [block] = Block.objects.all()
    assert (block.schedule, block.reason, block.count, block.limit) == (
        None,
        BlockReason.NO_SHOWS,
        2,
        2,
    )
    assert block.term == terms[1]
    assert block.ends_on == SPRING[1]
    event = AuditEvent.objects.get(action=AuditAction.BLOCK_SET)
    assert event.details["student"] == str(student.pk)
    assert event.actor_id == desk.pk

    for assignment in (spring, other):
        with pytest.raises(services.BookingError) as refused:
            book_own(assignment, student, FEB_10, NOW)
        assert refused.value.code == "blocked"
    # Staff can still book for them.
    services.book(
        actor=desk,
        assignment=spring,
        starts_at=at(spring.schedule, FEB_10, 15),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[student],
        as_staff=True,
        now=NOW,
        notify=False,
    )


def test_a_schedule_limit_counts_and_pauses_only_that_schedule(
    center, spring, terms, tutor, desk, student
) -> None:
    schedule = spring.schedule
    schedule.no_show_block_limit = 1
    schedule.save()
    other = schedule_in(terms[1], tutor, name="Evening")
    no_show(other, student, FEB_1, desk)
    assert not Block.objects.exists()
    no_show(spring, student, FEB_2, desk)
    [block] = Block.objects.all()
    assert block.schedule == schedule
    with pytest.raises(services.BookingError):
        book_own(spring, student, FEB_10, NOW)
    book_own(other, student, FEB_10, NOW)


def test_late_cancellations_reach_their_own_limit(
    center, spring, student, django_capture_on_commit_callbacks
) -> None:
    center.late_cancel_block_limit = 2
    center.no_show_block_limit = 2
    center.save()
    schedule = spring.schedule
    schedule.late_cancel_minutes = 120
    schedule.change_cutoff_minutes = 60
    schedule.save()
    for day in (FEB_1, FEB_2):
        a = booked(spring, student, at(schedule, day, 10))
        with django_capture_on_commit_callbacks(execute=True):
            services.cancel(
                actor=student,
                appointment=a,
                as_staff=False,
                now=a.starts_at - timedelta(minutes=90),
            )
    [block] = Block.objects.all()
    assert block.reason == BlockReason.LATE_CANCELS
    last = [m for m in mail.outbox if m.to == [student.email]][-1]
    assert "That makes 2 late cancellations this term, so online booking is paused" in last.body
    assert "You can book online again starting Saturday, May 8, 2027." in last.body


def test_one_block_per_limit_however_many_more(center, spring, desk, student) -> None:
    center.no_show_block_limit = 1
    center.save()
    no_show(spring, student, FEB_1, desk)
    no_show(spring, student, FEB_2, desk)
    assert Block.objects.count() == 1


def test_waived_ones_do_not_count(center, spring, desk, student) -> None:
    center.no_show_block_limit = 2
    center.save()
    first = no_show(spring, student, FEB_1, desk)
    attendance.waive(actor=desk, appointment=first, student_id=student.pk, reason="Flu.")
    no_show(spring, student, FEB_2, desk)
    assert not Block.objects.exists()
    assert (
        blocks.standing(student, Appointment.objects.get(pk=first.pk), BlockReason.NO_SHOWS).count
        == 1
    )


# --- Counting within a term, or from a date ----------------------------------------------


def test_counting_starts_over_each_term(center, terms, tutor, desk, student) -> None:
    center.no_show_block_limit = 2
    center.save()
    fall = schedule_in(terms[0], tutor, name="Fall drop-in")
    spring = schedule_in(terms[1], tutor, name="Spring drop-in")
    no_show(fall, student, date(2026, 12, 1), desk)
    spring_no_show = no_show(spring, student, FEB_1, desk)
    assert not Block.objects.exists()
    assert blocks.standing(student, spring_no_show, BlockReason.NO_SHOWS).count == 1


def test_counting_from_a_date_reaches_across_terms(center, terms, tutor, desk, student) -> None:
    center.no_show_block_limit = 2
    center.block_count_from = date(2026, 11, 1)
    center.save()
    fall = schedule_in(terms[0], tutor, name="Fall drop-in")
    spring = schedule_in(terms[1], tutor, name="Spring drop-in")
    no_show(fall, student, date(2026, 10, 15), desk)  # before the date: not counted
    no_show(fall, student, date(2026, 12, 1), desk)
    assert not Block.objects.exists()
    no_show(spring, student, FEB_1, desk)
    [block] = Block.objects.all()
    assert block.counted_from == date(2026, 11, 1)
    assert block.term is None
    assert block.ends_on == SPRING[1]  # still ends with the term it was set in


def test_no_block_for_a_term_that_is_already_over(center, terms, tutor, desk, student) -> None:
    center.no_show_block_limit = 1
    center.save()
    fall = schedule_in(terms[0], tutor, name="Fall drop-in")
    a = booked(fall, student, at(fall.schedule, date(2026, 12, 15), 10))
    attendance.mark(
        actor=desk,
        appointment=a,
        marks={student.pk: Attendance.NO_SHOW},
        now=datetime(2027, 1, 5, 15, tzinfo=UTC),
        notify=False,
    )
    assert not Block.objects.exists()


# --- Ending a block ------------------------------------------------------------------------


def test_a_block_ends_after_its_last_day(center, spring, desk, student) -> None:
    center.no_show_block_limit = 1
    center.save()
    no_show(spring, student, FEB_1, desk)
    [block] = Block.objects.all()
    schedule = spring.schedule
    last_evening = at(schedule, block.ends_on, 23, 59)
    next_morning = at(schedule, block.ends_on + timedelta(days=1), 0, 1)
    assert blocks.in_effect(student, schedule, now=last_evening) == [block]
    assert blocks.in_effect(student, schedule, now=next_morning) == []
    assert block.first_free_day == date(2027, 5, 8)


def test_a_director_lifts_a_block(center, spring, desk, student) -> None:
    center.no_show_block_limit = 1
    center.save()
    no_show(spring, student, FEB_1, desk)
    [block] = Block.objects.all()
    director = make_member(center, Role.DIRECTOR)
    blocks.lift(actor=director, block=block, note="Talked it over.")
    blocks.lift(actor=director, block=block, note="Twice.")
    block.refresh_from_db()
    assert block.lifted_by == director
    assert block.lift_note == "Talked it over."
    events = AuditEvent.objects.filter(action=AuditAction.BLOCK_LIFTED)
    assert events.count() == 1
    assert "Talked" not in str(events.get().details)
    book_own(spring, student, FEB_10, NOW)
    # Lifting doesn't reset the count: the next one past the limit pauses booking again.
    no_show(spring, student, date(2027, 2, 4), desk)
    assert Block.objects.filter(lifted_at__isnull=True).count() == 1


# --- What a blocked student can still do -------------------------------------------------


def test_a_blocked_student_keeps_their_appointments_and_can_cancel_or_move_them(
    center, spring, desk, student
) -> None:
    schedule = spring.schedule
    upcoming = book_own(spring, student, FEB_10, NOW)
    another = booked(spring, student, at(schedule, date(2027, 2, 11), 15))
    center.no_show_block_limit = 1
    center.save()
    no_show(spring, student, FEB_1, desk)
    assert blocks.in_effect(student, schedule, now=NOW)
    services.reschedule(
        actor=student,
        appointment=upcoming,
        starts_at=at(schedule, FEB_10, 16),
        minutes=30,
        as_staff=False,
        now=NOW,
        notify=False,
    )
    services.cancel(actor=student, appointment=another, as_staff=False, now=NOW, notify=False)


# --- The no-show email when it sets a block --------------------------------------------


def test_the_no_show_email_says_booking_is_paused(
    center, spring, desk, student, django_capture_on_commit_callbacks
) -> None:
    center.no_show_block_limit = 1
    center.save()
    a = booked(spring, student, at(spring.schedule, FEB_1, 10))
    with django_capture_on_commit_callbacks(execute=True):
        attendance.mark(
            actor=desk,
            appointment=a,
            marks={student.pk: Attendance.NO_SHOW},
            now=a.starts_at + timedelta(minutes=20),
        )
    [message] = mail.outbox
    assert "That makes 1 missed appointment this term, so online booking is paused" in (
        message.body
    )
    assert "You can book online again starting Saturday, May 8, 2027." in message.body
    assert "You're welcome to book another time" not in message.body


# --- Two no-shows confirmed at once -----------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_no_shows_confirmed_at_once_set_one_block() -> None:
    center = CenterFactory(time_zone="America/New_York", no_show_block_limit=2)
    tutor = make_member(center, Role.TUTOR)
    spring = schedule_in(make_term(center, *SPRING), tutor)
    student = make_member(center, Role.STUDENT)
    staff = [make_member(center, Role.FRONT_DESK) for _ in range(2)]
    appointments = [booked(spring, student, at(spring.schedule, day, 10)) for day in (FEB_1, FEB_2)]
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def confirm(actor, appointment) -> None:
        try:
            barrier.wait()
            attendance.mark(
                actor=actor,
                appointment=appointment,
                marks={student.pk: Attendance.NO_SHOW},
                now=NOW,
                notify=False,
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    threads = [
        threading.Thread(target=confirm, args=(actor, a))
        for actor, a in zip(staff, appointments, strict=True)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    assert Participant.objects.filter(attendance=Attendance.NO_SHOW).count() == 2
    assert Block.objects.count() == 1
