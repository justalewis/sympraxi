"""Requests that arrive at the same moment, each in its own database connection.

These run with real commits (transaction=True), so the row locks and the
exclusion constraint are what decide the winner, not the order of Python calls.
"""

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time

import pytest
from django.db import connection

from sympraxi.centers.models import Role
from sympraxi.scheduling import services
from sympraxi.scheduling.models import Appointment, AppointmentStatus
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule

pytestmark = pytest.mark.django_db(transaction=True)

TUESDAY = date(2027, 1, 12)
NOW = datetime(2027, 1, 11, 13, tzinfo=UTC)


def race(*attempts: Callable[[], object]) -> list[str]:
    """Run the attempts in threads released together; return "ok" or each refusal's code."""
    barrier = threading.Barrier(len(attempts))
    results: list[str] = []
    lock = threading.Lock()

    def run(attempt: Callable[[], object]) -> None:
        try:
            barrier.wait()
            attempt()
            outcome = "ok"
        except services.BookingError as exc:
            outcome = exc.code
        finally:
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=run, args=(a,)) for a in attempts]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return sorted(results)


def booking(student, assignment, hour: int, minutes: int = 30):
    return lambda: services.book(
        actor=student,
        assignment=assignment,
        starts_at=at(assignment.schedule, TUESDAY, hour),
        minutes=minutes,
        modality=assignment.modalities[0],
        students=[student],
        as_staff=False,
        now=NOW,
        notify=False,
    )


@pytest.fixture
def center():
    return CenterFactory(time_zone="America/New_York")


@pytest.fixture
def schedule(center):
    return make_schedule(center, date(2027, 1, 4), date(2027, 2, 28))


def test_one_slot_one_booking(center, schedule) -> None:
    tutor = assign(schedule, hours=((time(9), time(17)),))
    students = [make_member(center, Role.STUDENT) for _ in range(4)]
    results = race(*(booking(s, tutor, 10) for s in students))
    assert results == ["ok", "slot_taken", "slot_taken", "slot_taken"]
    assert Appointment.objects.exclude(status=AppointmentStatus.CANCELED).count() == 1


def test_one_tutor_on_two_schedules_one_booking(center, schedule) -> None:
    # Different assignments take different row locks; the exclusion constraint decides.
    other = make_schedule(center, schedule.starts_on, schedule.ends_on, name="Online")
    tutor = make_member(center, Role.TUTOR)
    first = assign(schedule, tutor, hours=((time(9), time(17)),))
    second = assign(
        other,
        tutor,
        hours=((time(9), time(17)),),
        offers_in_person=False,
        offers_online=True,
        meeting_url="https://meet.example.edu/x",
    )
    a, b = (make_member(center, Role.STUDENT) for _ in range(2))
    results = race(booking(a, first, 10, minutes=60), booking(b, second, 10))
    assert results == ["ok", "slot_taken"]
    assert Appointment.objects.filter(tutor=tutor).count() == 1


def test_group_seats_are_never_oversold(center, schedule) -> None:
    group = assign(schedule, hours=((time(9), time(17)),), capacity=3)
    students = [make_member(center, Role.STUDENT) for _ in range(5)]
    results = race(*(booking(s, group, 10) for s in students))
    assert results == ["full", "full", "ok", "ok", "ok"]
    appointment = Appointment.objects.get(assignment=group)
    assert appointment.active_participants().count() == 3


def test_a_student_racing_themselves_stays_within_the_limit(center, schedule) -> None:
    schedule.max_per_day = 1
    schedule.save()
    tutors = [assign(schedule, hours=((time(9), time(17)),)) for _ in range(3)]
    student = make_member(center, Role.STUDENT)
    results = race(*(booking(student, t, 10 + i) for i, t in enumerate(tutors)))
    assert results == ["limit_day", "limit_day", "ok"]
