"""Requests that arrive at the same moment, each in its own database connection.

These run with real commits (transaction=True), so the row locks and the
exclusion constraint are what decide the winner, not the order of Python calls.
"""

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time

import pytest
from django.conf import settings
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.scheduling import services
from sympraxi.scheduling.forms import format_start
from sympraxi.scheduling.models import Appointment, AppointmentStatus
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, build_world, make_member, make_schedule

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


def moving(student, appointment, assignment, hour: int, minutes: int = 30):
    return lambda: services.reschedule(
        actor=student,
        appointment=appointment,
        assignment=assignment,
        starts_at=at(assignment.schedule, TUESDAY, hour),
        minutes=minutes,
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


@pytest.fixture
def shared_tutor(center, schedule):
    """One tutor on two schedules: in person on `schedule`, online on another."""
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
    return tutor, first, second


def test_one_slot_one_booking(center, schedule) -> None:
    tutor = assign(schedule, hours=((time(9), time(17)),))
    students = [make_member(center, Role.STUDENT) for _ in range(4)]
    results = race(*(booking(s, tutor, 10) for s in students))
    assert results == ["ok", "slot_taken", "slot_taken", "slot_taken"]
    assert Appointment.objects.exclude(status=AppointmentStatus.CANCELED).count() == 1


def test_one_tutor_on_two_schedules_one_booking(center, shared_tutor) -> None:
    # Each booking locks all of the tutor's assignments, so the second waits for
    # the first and then sees it. Locking only the booked assignment could deadlock.
    tutor, first, second = shared_tutor
    a, b = (make_member(center, Role.STUDENT) for _ in range(2))
    results = race(booking(a, first, 10, minutes=60), booking(b, second, 10))
    assert results == ["ok", "slot_taken"]
    assert Appointment.objects.filter(tutor=tutor).count() == 1


def test_a_move_and_a_booking_for_one_tutor_on_two_schedules(center, shared_tutor) -> None:
    tutor, first, second = shared_tutor
    a, b = (make_member(center, Role.STUDENT) for _ in range(2))
    appointment = booking(a, first, 9)()
    results = race(moving(a, appointment, first, 10), booking(b, second, 10))
    assert results == ["ok", "slot_taken"]
    at_ten = Appointment.objects.filter(tutor=tutor, starts_at=at(first.schedule, TUESDAY, 10))
    assert at_ten.count() == 1


@pytest.mark.parametrize("errcode", ["deadlock_detected", "serialization_failure"])
def test_a_deadlock_the_locks_miss_is_still_refused_politely(center, schedule, errcode) -> None:
    # Stand in for a clash with a writer that doesn't take the tutor locks.
    tutor = assign(schedule, hours=((time(9), time(17)),))
    student = make_member(center, Role.STUDENT)

    def fail_insert(execute, sql, params, many, context):
        if sql.startswith('INSERT INTO "scheduling_appointment" '):
            sql = f"DO $$ BEGIN RAISE EXCEPTION 'clash' USING ERRCODE = '{errcode}'; END $$"
            params = None
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_insert), pytest.raises(services.BookingError) as refused:
        booking(student, tutor, 10)()
    assert refused.value.code == "slot_taken"
    assert not Appointment.objects.exists()


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


def test_a_double_click_books_once_and_both_clicks_show_the_booking(center) -> None:
    # Both posts reach the server together, in one browser session. One books.
    # The other waits on the student's lock and is refused, or finds the time
    # gone, and either way sees that the student holds it.
    world = build_world(center)
    student = make_member(center, Role.STUDENT)
    first = Client()
    first.force_login(student)
    second = Client()
    session = first.cookies[settings.SESSION_COOKIE_NAME].value
    second.cookies[settings.SESSION_COOKIE_NAME] = session
    kwargs = {"slug": center.slug, "schedule_id": world.schedule.pk}
    url = reverse("scheduling:book", kwargs=kwargs) + "?" + world.slot_query()
    data = {
        "assignment": world.assignment.pk,
        "start": format_start(world.free_start),
        "minutes": 30,
        "modality": "in_person",
    }
    barrier = threading.Barrier(2)
    responses = []

    def click(client: Client) -> None:
        try:
            barrier.wait()
            responses.append(client.post(url, data))
        finally:
            connection.close()

    threads = [threading.Thread(target=click, args=(c,)) for c in (first, second)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    appointment = Appointment.objects.get(participants__student=student)
    detail = reverse(
        "scheduling:appointment", kwargs={"slug": center.slug, "appointment_id": appointment.pk}
    )
    assert [r.status_code for r in responses] == [302, 302]
    assert [r["Location"] for r in responses] == [detail, detail]
    for response in responses:
        shown = [str(m) for m in get_messages(response.wsgi_request)]
        assert shown == ["You're booked. A confirmation email is on its way."]
