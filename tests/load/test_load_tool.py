"""Tests for the load test itself: its checks catch what they should, and its seeded
center and page parsing still match the real booking pages.

The load test runs outside pytest (`python -m tests.load run`); these keep it from
quietly going stale when models or templates change.
"""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from sympraxi.scheduling.models import Appointment, Participant, Schedule
from tests.load import check, world
from tests.load.check import Appt, Post, Rules, Seat, World
from tests.load.client import Page
from tests.load.driver import reason_for

TZ = ZoneInfo("America/New_York")
MONDAY = date(2027, 1, 11)
BOOKED = datetime(2027, 1, 4, 12, tzinfo=UTC)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TZ)


def rules(id: str = "s1", **kwargs: Any) -> Rules:
    values: dict[str, Any] = {
        "id": id,
        "center": "c",
        "tz": TZ,
        "term": (date(2027, 1, 1), date(2027, 5, 1)),
        "span": False,
        "most": {"day": None, "week": None, "term": None},
        "most_minutes": {"day": None, "week": None, "term": None},
        "notice": 60,
        "horizon": 14,
        "opens": time(9),
        "closes": time(18),
        "slot": 30,
    }
    values.update(kwargs)
    return Rules(**values)


def appt(
    id: str,
    start: datetime,
    minutes: int = 30,
    *,
    schedule: str = "s1",
    tutor: str = "t1",
    buffer: int = 0,
    capacity: int = 1,
    canceled: datetime | None = None,
) -> Appt:
    end = start + timedelta(minutes=minutes)
    return Appt(
        id=id,
        schedule=schedule,
        assignment=f"{schedule}-{tutor}",
        tutor=tutor,
        starts=start,
        ends=end,
        blocked_until=end + timedelta(minutes=buffer),
        canceled_at=canceled,
        capacity=capacity,
        placeholder=False,
    )


def seat(appointment: str, student: str, minutes_after: int = 0, by: str | None = None) -> Seat:
    added = BOOKED + timedelta(minutes=minutes_after)
    return Seat(appointment, student, added, by, None)


def make(
    appts: list[Appt],
    seats: list[Seat],
    *schedules: Rules,
    max_day: int | None = None,
    **extra: Any,
) -> World:
    """A world whose tutors work 9 to 5 every day."""
    return World(
        appointments={a.id: a for a in appts},
        seats=seats,
        schedules={s.id: s for s in schedules or (rules(),)},
        assignments={a.assignment: Post(a.assignment, a.schedule, max_day, None) for a in appts},
        hours={(a.assignment, day): [(time(9), time(17))] for a in appts for day in range(7)},
        **extra,
    )


def problems(w: World) -> dict[str, int]:
    return {name: len(found) for name, found in check.run_all(w).items() if found}


# --- The checks -----------------------------------------------------------------


def test_a_clean_world_passes() -> None:
    a, b = appt("a", at(MONDAY, 10)), appt("b", at(MONDAY, 10, 30))
    assert problems(make([a, b], [seat("a", "s"), seat("b", "u")])) == {}


def test_a_tutor_clash_counts_the_break() -> None:
    first = appt("a", at(MONDAY, 10), buffer=10)
    assert problems(make([first, appt("b", at(MONDAY, 10, 30))], [])) == {"tutor double-booked": 1}
    no_break = appt("a", at(MONDAY, 10))
    assert problems(make([no_break, appt("b", at(MONDAY, 10, 30))], [])) == {}


def test_a_tutor_on_two_schedules_has_one_calendar() -> None:
    a = appt("a", at(MONDAY, 10), schedule="s1")
    b = appt("b", at(MONDAY, 10), schedule="s2")
    found = problems(make([a, b], [], rules("s1"), rules("s2")))
    assert found == {"tutor double-booked": 1}


def test_canceled_appointments_hold_nothing() -> None:
    a = appt("a", at(MONDAY, 10), canceled=BOOKED)
    b = appt("b", at(MONDAY, 10))
    assert problems(make([a, b], [seat("a", "s"), seat("b", "s")])) == {}


def test_a_student_in_two_places() -> None:
    a = appt("a", at(MONDAY, 10), tutor="t1")
    b = appt("b", at(MONDAY, 10), tutor="t2")
    assert problems(make([a, b], [seat("a", "s"), seat("b", "s", 1)])) == {
        "student double-booked": 1
    }


def test_a_group_over_capacity() -> None:
    group = appt("g", at(MONDAY, 12), 60, capacity=2)
    seats = [seat("g", s, i) for i, s in enumerate("xyz")]
    assert problems(make([group], seats)) == {"group over capacity": 1}


def test_a_tutor_cap() -> None:
    appts = [appt(str(h), at(MONDAY, h)) for h in (10, 11, 12)]
    assert problems(make(appts, [], max_day=2)) == {"tutor cap exceeded": 1}


def test_limits_are_replayed_in_booking_order() -> None:
    # The spanning schedule allows one a week across the center; the other has no limit.
    spanning = rules("span", span=True, most={"day": None, "week": 1, "term": None})
    other = rules("other")
    on_span = appt("a", at(MONDAY, 10), schedule="span", tutor="t1")
    on_other = appt("b", at(MONDAY, 14), schedule="other", tutor="t2")
    # Spanning first, then the other: both bookings were within the rules.
    legal = [seat("a", "s", 0), seat("b", "s", 5)]
    assert problems(make([on_span, on_other], legal, spanning, other)) == {}
    # The other first: the spanning booking made two in the week.
    late = [seat("b", "s", 0), seat("a", "s", 5)]
    assert problems(make([on_span, on_other], late, spanning, other)) == {
        "student limit exceeded": 1
    }


def test_minute_limits() -> None:
    weekly = rules(most_minutes={"day": None, "week": 60, "term": None})
    a = appt("a", at(MONDAY, 10), 60, tutor="t1")
    b = appt("b", at(MONDAY + timedelta(days=1), 10), 30, tutor="t2")
    found = problems(make([a, b], [seat("a", "s"), seat("b", "s", 1)], weekly))
    assert found == {"student limit exceeded": 1}


def test_exemptions_and_overrides_lift_limits() -> None:
    daily = rules(most={"day": 1, "week": None, "term": None})
    a = appt("a", at(MONDAY, 10), tutor="t1")
    b = appt("b", at(MONDAY, 14), tutor="t2")
    seats = [seat("a", "s"), seat("b", "s", 1)]
    assert problems(make([a, b], seats, daily)) == {"student limit exceeded": 1}
    assert problems(make([a, b], seats, daily, exemptions=[("s", None, MONDAY)])) == {}
    assert problems(make([a, b], seats, daily, overrides={("b", "s")})) == {}


def test_notice_and_horizon_bind_students_not_staff() -> None:
    soon = appt("a", BOOKED + timedelta(minutes=30))
    far = appt("b", BOOKED + timedelta(days=20))
    by_self = [seat("a", "s", by="s"), seat("b", "u", by="u")]
    found = check.notice_and_horizon(make([soon, far], by_self))
    assert len(found) == 2
    by_staff = [seat("a", "s", by="desk"), seat("b", "u", by="desk")]
    assert check.notice_and_horizon(make([soon, far], by_staff)) == []


def test_outside_hours_and_off_the_grid() -> None:
    late = appt("a", at(MONDAY, 16, 30), 60)  # hours end at 17
    off_grid = appt("b", at(MONDAY, 10, 15), tutor="t2")
    found = check.outside_hours(make([late, off_grid], []))
    assert len(found) == 2


def test_the_database_matches_what_people_were_told() -> None:
    w = make([appt("a", at(MONDAY, 10))], [seat("a", "s")])
    assert check.agreement(w, {("s", "a")}) == []
    assert len(check.agreement(w, set())) == 1
    assert len(check.agreement(w, {("s", "a"), ("u", "a")})) == 1


def test_refusal_messages_are_classified() -> None:
    assert reason_for("Someone else just booked that time.") == "slot_taken"
    assert reason_for("Oct. 16, 2026: That would go over the limit of 1 appointments a day.") == (
        "limit"
    )
    assert reason_for("Something new") == "other"


# --- The seeded center against the real pages -----------------------------------


@pytest.mark.django_db
def test_the_seeded_center_books_through_its_pages(client) -> None:
    manifest = world.build(students=3, front_desk=1)
    assert manifest["tutors"] == 24
    main = next(s for s in manifest["schedules"] if s["key"] == "main")
    day = world.first_weekdays(world.local_today(manifest), 1)[0]
    student = manifest["students"][0]
    client.cookies["sessionid"] = student["session"]

    listing = client.get(f"/centers/loadtest/schedules/{main['id']}/?date={day}&minutes=30")
    assert listing.status_code == 200
    links = [link for link in Page(listing.content.decode()).links if "/book/?" in link]
    assert links, "the driver finds book links on the open times page"
    form_page = client.get(links[0])
    forms = [f for f in Page(form_page.content.decode()).forms if "assignment" in f.fields]
    assert forms and forms[0].fields["modality"] == "in_person"
    booked = client.post(links[0], forms[0].fields)
    assert booked.status_code == 302 and "/appointments/" in booked["Location"]

    # Sending it again, as a double-click does, lands on the same appointment.
    again = client.post(links[0], forms[0].fields)
    assert again.status_code == 302 and again["Location"] == booked["Location"]
    # Another student is refused that time, with a message the driver can read.
    client.cookies["sessionid"] = manifest["students"][1]["session"]
    refused = client.post(links[0], forms[0].fields)
    shown = client.get(refused["Location"]) if refused.status_code == 302 else refused
    assert Page(shown.content.decode()).messages("error", "warning")

    center_id = manifest["center"]["id"]
    appointment = Appointment.objects.get(participants__student_id=student["id"])
    told = {(student["id"], str(appointment.pk))}
    assert not any(check.run_all(check.load(center_id), told).values())


@pytest.mark.django_db
def test_the_checks_read_violations_from_the_database() -> None:
    manifest = world.build(students=3, front_desk=1)
    schedule = Schedule.objects.get(pk=manifest["schedules"][0]["id"])
    assignment = schedule.assignments.order_by("pk").first()
    assert assignment is not None
    day = world.first_weekdays(world.local_today(manifest), 1)[0]
    start = datetime.combine(day, time(9), ZoneInfo(manifest["center"]["time_zone"]))
    appointment = Appointment.objects.create(
        schedule=schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        blocked_until=start + timedelta(minutes=30),
        modality="in_person",
    )
    for person in manifest["students"][:2]:  # two students in a one-person appointment
        Participant.objects.create(appointment=appointment, student_id=person["id"])
    found = check.run_all(check.load(manifest["center"]["id"]), set())
    assert len(found["group over capacity"]) == 1
    assert len(found["database and responses disagree"]) == 2
