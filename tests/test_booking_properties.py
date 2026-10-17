"""No sequence of bookings, cancellations and changes can double-book a tutor or break a limit.

Hypothesis plays random sequences of operations against the real booking service
and the real database: students and the front desk book, cancel and move
appointments on two schedules that share a tutor, while the clock moves forward
across a daylight-saving switch. After every step the invariants below are
checked straight from the tables, computed independently of the service code.

Set HYPOTHESIS_PROFILE=thorough for a longer, randomized run.
"""

import os
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from django.db import transaction
from hypothesis import HealthCheck, event, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    Bundle,
    RuleBasedStateMachine,
    consumes,
    invariant,
    multiple,
    rule,
    run_state_machine_as_test,
)

from sympraxi.centers.models import Role
from sympraxi.scheduling import services
from sympraxi.scheduling.availability import open_times
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Exemption, Participant
from sympraxi.scheduling.timeutils import first_instant
from tests.factories import CenterFactory
from tests.scheduling_world import assign, make_member, make_schedule

NY = ZoneInfo("America/New_York")

LIMITS = {
    "max_per_day": 2,
    "max_per_week": 3,
    "max_per_term": 4,
    "max_minutes_per_day": 60,
    "max_minutes_per_week": 120,
    "max_minutes_per_term": 150,
}
TUTOR_DAY_CAP = 1
# Bookings crowd into Friday to Sunday of the first week (Sunday is the
# daylight-saving switch), so students collide with themselves and reach their
# limits often. The schedules run two weeks.
DAYS = st.sampled_from([4, 5, 6])

# What the operations did, across every run of the machine in this process.
OUTCOMES: Counter[str] = Counter()

# Grid positions (30-minute steps from midnight) inside the tutors' hours, which
# include the small hours of the daylight-saving night.
SLOTS = [*range(0, 9), *range(18, 22)]

# Why a student may still be refused a time from their own list: the list
# doesn't know their limits or their other appointments.
STUDENTS_OWN_REASONS = {"student_busy", "already_in"}


def _record(outcome: str) -> None:
    event(outcome)
    OUTCOMES[outcome] += 1


def _count(prefix: str) -> int:
    return sum(n for outcome, n in OUTCOMES.items() if outcome.startswith(prefix))


class BookingMachine(RuleBasedStateMachine):
    # Set by the test: the Monday the schedules start, and whether limits span the center.
    first_day: date = date(2026, 10, 26)
    span: bool = False

    appointments = Bundle("appointments")

    def __init__(self) -> None:
        super().__init__()
        self.savepoint = transaction.savepoint_create()
        center = CenterFactory(time_zone="America/New_York")
        last = self.first_day + timedelta(days=13)
        common = {
            "opens_at": time(0),
            "closes_at": time(23, 30),
            "slot_minutes": 30,
            "buffer_minutes": 10,
            "min_notice_minutes": 60,
            "horizon_days": 10,
            "change_cutoff_minutes": 120,
            "limits_span_center": self.span,
            **LIMITS,
        }
        self.schedules = [
            make_schedule(center, self.first_day, last, name="In person", **common),
            make_schedule(center, self.first_day, last, name="Online", **common),
        ]
        hours = ((time(0), time(4)), (time(9), time(13)))
        shared = make_member(center, Role.TUTOR)
        self.assignments = [
            # One tutor on both schedules: the database must stop cross-schedule overlaps.
            assign(
                self.schedules[0],
                shared,
                hours=hours,
                offers_online=True,
                meeting_url="https://meet.example.edu/shared",
            ),
            assign(
                self.schedules[1],
                shared,
                hours=hours,
                offers_in_person=False,
                offers_online=True,
                meeting_url="https://meet.example.edu/shared",
            ),
            # A group of two.
            assign(self.schedules[0], hours=hours, capacity=2),
            # A tutor with a daily cap.
            assign(
                self.schedules[1],
                hours=hours,
                offers_in_person=False,
                offers_online=True,
                meeting_url="https://meet.example.edu/capped",
                max_per_day=TUTOR_DAY_CAP,
            ),
        ]
        self.students = [make_member(center, Role.STUDENT) for _ in range(3)]
        # The third student is exempt for the first five days.
        Exemption.objects.create(
            center=center, student=self.students[2], expires_on=self.first_day + timedelta(days=5)
        )
        self.staff = make_member(center, Role.FRONT_DESK)
        self.now = first_instant(self.first_day, time(7), NY)

    def teardown(self) -> None:
        transaction.savepoint_rollback(self.savepoint)

    # --- Operations -----------------------------------------------------------------

    def _start(self, day: int, slot: int) -> datetime:
        return first_instant(self.first_day + timedelta(days=day), time(0), NY) + timedelta(
            minutes=30 * slot
        )

    @rule(
        target=appointments,
        student=st.integers(0, 2),
        assignment=st.integers(0, 3),
        day=DAYS,
        slot=st.sampled_from(SLOTS),
        minutes=st.sampled_from([30, 60]),
        by_staff=st.booleans(),
        online=st.booleans(),
    )
    def book(self, student, assignment, day, slot, minutes, by_staff, online):
        chosen = self.assignments[assignment]
        modality = chosen.modalities[int(online) % len(chosen.modalities)]
        person = self.students[student]
        try:
            appointment = services.book(
                actor=self.staff if by_staff else person,
                assignment=chosen,
                starts_at=self._start(day, slot),
                minutes=minutes,
                modality=modality,
                students=[person],
                as_staff=by_staff,
                now=self.now,
                notify=False,
            )
        except services.BookingError as exc:
            _record(f"book refused: {exc.code}")
            return multiple()
        _record("book: ok (group)" if appointment.capacity > 1 else "book: ok")
        return appointment.pk

    @rule(
        target=appointments,
        student=st.integers(0, 2),
        schedule=st.integers(0, 1),
        day=DAYS,
        minutes=st.sampled_from([30, 60]),
        pick=st.integers(0, 10_000),
    )
    def book_from_the_list(self, student, schedule, day, minutes, pick):
        """A student books a time their list offers, which no tutor rule may refuse."""
        chosen = self.schedules[schedule]
        listed = open_times(
            chosen,
            self.first_day + timedelta(days=day),
            minutes,
            earliest=self.now + timedelta(minutes=chosen.min_notice_minutes),
        )
        if not listed:
            _record("book from the list: nothing listed")
            return multiple()
        # Ties are listed by tutor name, which changes from run to run; Hypothesis
        # needs the same draw to pick the same time.
        listed.sort(key=lambda o: (o.starts_at, self.assignments.index(o.assignment)))
        offered = listed[pick % len(listed)]
        person = self.students[student]
        try:
            appointment = services.book(
                actor=person,
                assignment=offered.assignment,
                starts_at=offered.starts_at,
                minutes=minutes,
                modality=offered.modalities[0],
                students=[person],
                as_staff=False,
                now=self.now,
                notify=False,
            )
        except services.BookingError as exc:
            mine = exc.code in STUDENTS_OWN_REASONS or exc.code.startswith(("limit_", "minutes_"))
            assert mine, (exc.code, exc.message, offered)
            _record(f"book from the list refused: {exc.code}")
            return multiple()
        _record("book from the list: ok")
        return appointment.pk

    @rule(appointment=appointments, by_staff=st.booleans())
    def cancel(self, appointment, by_staff):
        current = Appointment.objects.get(pk=appointment)
        participant = current.participants.filter(canceled_at__isnull=True).first()
        actor = self.staff if by_staff or participant is None else participant.student
        try:
            services.cancel(
                actor=actor,
                appointment=current,
                as_staff=actor == self.staff,
                now=self.now,
                notify=False,
            )
            _record("cancel: ok")
        except services.BookingError as exc:
            _record(f"cancel refused: {exc.code}")

    @rule(
        appointment=appointments,
        day=DAYS,
        slot=st.sampled_from(SLOTS),
        minutes=st.sampled_from([30, 60]),
        by_staff=st.booleans(),
    )
    def reschedule(self, appointment, day, slot, minutes, by_staff):
        current = Appointment.objects.get(pk=appointment)
        participant = current.participants.filter(canceled_at__isnull=True).first()
        actor = self.staff if by_staff or participant is None else participant.student
        try:
            services.reschedule(
                actor=actor,
                appointment=current,
                starts_at=self._start(day, slot),
                minutes=minutes,
                as_staff=actor == self.staff,
                now=self.now,
                notify=False,
            )
            _record("reschedule: ok")
        except services.BookingError as exc:
            _record(f"reschedule refused: {exc.code}")

    @rule(appointment=consumes(appointments))
    def forget(self, appointment):
        """Keep the bundle from growing without bound."""

    @rule(minutes=st.integers(15, 24 * 60))
    def advance_clock(self, minutes):
        self.now += timedelta(minutes=minutes)

    # --- Invariants -------------------------------------------------------------

    def _active(self):
        return Appointment.objects.exclude(status=AppointmentStatus.CANCELED)

    @invariant()
    def no_tutor_is_ever_double_booked(self):
        busy = defaultdict(list)
        for tutor_id, start, until in self._active().values_list(
            "tutor_id", "starts_at", "blocked_until"
        ):
            busy[tutor_id].append((start, until))
        for intervals in busy.values():
            intervals.sort()
            for (_, until), (next_start, _) in pairwise(intervals):
                assert until <= next_start, intervals

    @invariant()
    def no_student_is_in_two_places(self):
        mine = defaultdict(list)
        for student, start, end in self._participations():
            mine[student].append((start, end))
        for intervals in mine.values():
            intervals.sort()
            for (_, end), (next_start, _) in pairwise(intervals):
                assert end <= next_start, intervals

    @invariant()
    def groups_stay_within_capacity(self):
        for appointment in self._active():
            assert appointment.active_participants().count() <= appointment.capacity

    @invariant()
    def tutor_caps_hold(self):
        capped = self.assignments[3]
        per_day = defaultdict(int)
        for start in self._active().filter(assignment=capped).values_list("starts_at", flat=True):
            per_day[start.astimezone(NY).date()] += 1
        assert all(n <= TUTOR_DAY_CAP for n in per_day.values()), per_day

    @invariant()
    def student_limits_hold(self):
        # The exempt student is left out: appointments inside an exemption can
        # legitimately push a later window past the limit.
        for student in self.students[:2]:
            rows = [r for r in self._participations(with_schedule=True) if r[0] == student.pk]
            groups = (
                [rows] if self.span else [[r for r in rows if r[3] == s.pk] for s in self.schedules]
            )
            for group in groups:
                self._check_windows(group)

    def _check_windows(self, rows):
        windows = {"day": defaultdict(list), "week": defaultdict(list), "term": defaultdict(list)}
        for _, start, end, _ in rows:
            local = start.astimezone(NY).date()
            minutes = (end - start) / timedelta(minutes=1)
            windows["day"][local].append(minutes)
            windows["week"][local - timedelta(days=local.weekday())].append(minutes)
            windows["term"]["term"].append(minutes)
        for period, buckets in windows.items():
            for key, lengths in buckets.items():
                assert len(lengths) <= LIMITS[f"max_per_{period}"], (period, key, lengths)
                assert sum(lengths) <= LIMITS[f"max_minutes_per_{period}"], (period, key, lengths)

    def _participations(self, with_schedule: bool = False):
        rows = (
            Participant.objects.filter(canceled_at__isnull=True)
            .exclude(appointment__status=AppointmentStatus.CANCELED)
            .filter(appointment__is_placeholder=False)
        )
        if with_schedule:
            return list(
                rows.values_list(
                    "student_id",
                    "appointment__starts_at",
                    "appointment__ends_at",
                    "appointment__schedule_id",
                )
            )
        return list(
            rows.values_list("student_id", "appointment__starts_at", "appointment__ends_at")
        )


# The default run plays the same sequences every time, so CI is repeatable and a
# failure reproduces. HYPOTHESIS_PROFILE=thorough plays about twelve times as
# many, chosen at random, and remembers failures in .hypothesis/.
RUNS = {
    "default": settings(
        derandomize=True,
        max_examples=25,
        stateful_step_count=40,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    ),
    "thorough": settings(
        max_examples=300,
        stateful_step_count=50,
        deadline=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize(
    "first_day",
    [date(2026, 10, 26), date(2027, 3, 8)],
    ids=["fall-back-week", "spring-forward-week"],
)
@pytest.mark.parametrize("span", [False, True], ids=["per-schedule", "center-wide"])
def test_no_sequence_breaks_the_rules(first_day: date, span: bool) -> None:
    machine = type("Machine", (BookingMachine,), {"first_day": first_day, "span": span})
    run = RUNS.get(os.environ.get("HYPOTHESIS_PROFILE", "default"), RUNS["default"])
    OUTCOMES.clear()
    run_state_machine_as_test(machine, settings=run)
    # Guard against a machine that only ever gets refused: the rules must have been
    # exercised with real bookings, cancellations and changes.
    print(dict(OUTCOMES))
    for prefix in ("book: ok", "book from the list: ok", "cancel: ok", "reschedule: ok"):
        assert _count(prefix) > 0, (prefix, OUTCOMES)
    limit_refusals = sum(
        _count(f"{op} refused: {kind}_")
        for op in ("book", "reschedule")
        for kind in ("limit", "minutes")
    )
    assert limit_refusals > 0, OUTCOMES
