"""The time clock: clocking in and out, the rules the database keeps, the midnight
clock-out, directors' corrections with reasons, and timesheets."""

import csv
import io
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.staff import timeclock
from sympraxi.staff.models import Category, TimeEntry
from sympraxi.staff.views import timesheet
from tests.scheduling_world import make_member
from tests.staff_world import at, entry, today

pytestmark = pytest.mark.django_db


def audit(action: AuditAction) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


@pytest.fixture
def tutor(center):
    return make_member(center, Role.TUTOR, full_name="Toni Tutor")


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


def clock_url(center) -> str:
    return reverse("staff:clock", kwargs={"slug": center.slug})


def entries_url(center) -> str:
    return reverse("staff:entries", kwargs={"slug": center.slug})


# --- Clocking in and out -----------------------------------------------------------


def test_a_tutor_clocks_in_and_out(client, center, tutor) -> None:
    client.force_login(tutor)
    client.post(clock_url(center), {"action": "in", "category": Category.TRAINING, "note": "CRLA"})
    [open_entry] = TimeEntry.objects.all()
    assert open_entry.is_open and open_entry.category == Category.TRAINING
    page = client.get(clock_url(center)).content.decode()
    assert "clocked in since" in page and "Clock out" in page

    response = client.post(clock_url(center), {"action": "out"}, follow=True)
    assert "You&#x27;re clocked out" in response.content.decode()
    open_entry.refresh_from_db()
    assert not open_entry.is_open
    assert [e.details for e in audit(AuditAction.TIME_CLOCKED_IN)] == [{"category": "training"}]
    assert len(audit(AuditAction.TIME_CLOCKED_OUT)) == 1
    # The note's words stay out of the audit log.
    assert "CRLA" not in str([e.details for e in AuditEvent.objects.all()])


def test_clocking_in_twice_or_out_without_being_in_says_so(client, center, tutor) -> None:
    client.force_login(tutor)
    response = client.post(clock_url(center), {"action": "out"}, follow=True)
    assert "You aren&#x27;t clocked in." in response.content.decode()
    client.post(clock_url(center), {"action": "in", "category": Category.TUTORING})
    response = client.post(
        clock_url(center), {"action": "in", "category": Category.TUTORING}, follow=True
    )
    assert "You&#x27;re already clocked in." in response.content.decode()
    assert TimeEntry.objects.count() == 1


def test_the_page_shows_this_week_and_last_week(client, center, tutor) -> None:
    day = today(center)
    entry(center, tutor, day - timedelta(days=7), (9, 0), (10, 30))
    entry(center, tutor, day - timedelta(days=60), (9, 0), (17, 0))  # too old to show
    client.force_login(tutor)
    page = client.get(clock_url(center)).content.decode()
    assert "Total: 1.50 hours." in page
    assert "8.00" not in page


def test_staff_see_only_their_own_time(client, center, tutor) -> None:
    other = make_member(center, Role.TUTOR, full_name="Other Tutor")
    entry(center, other, today(center) - timedelta(days=1), (9, 0), (12, 0))
    client.force_login(tutor)
    page = client.get(clock_url(center)).content.decode()
    assert "3.00" not in page


# --- What the database refuses ------------------------------------------------------


def test_one_open_entry_per_person_in_a_center(center, tutor) -> None:
    day = today(center) - timedelta(days=1)
    entry(center, tutor, day, (9, 0), None)
    with pytest.raises(IntegrityError), transaction.atomic():
        entry(center, tutor, day, (13, 0), None)


def test_entries_for_a_person_never_overlap(center, tutor) -> None:
    day = today(center) - timedelta(days=1)
    entry(center, tutor, day, (9, 0), (11, 0))
    entry(center, tutor, day, (11, 0), (12, 0))  # back to back is fine
    with pytest.raises(IntegrityError), transaction.atomic():
        entry(center, tutor, day, (10, 0), (10, 30))
    # Removed entries don't count.
    TimeEntry.objects.filter(started_at=at(center, day, 9)).update(removed_at=timezone.now())
    entry(center, tutor, day, (10, 0), (10, 30))


def test_an_entry_ends_after_it_starts(center, tutor) -> None:
    day = today(center) - timedelta(days=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        entry(center, tutor, day, (11, 0), (9, 0))


# --- Midnight -----------------------------------------------------------------------


def test_anyone_still_clocked_in_is_clocked_out_at_midnight(center, tutor) -> None:
    yesterday = today(center) - timedelta(days=1)
    forgotten = entry(center, tutor, yesterday, (15, 0), None)
    now = at(center, today(center), 0, 30)
    assert timeclock.close_forgotten(now) == 1
    forgotten.refresh_from_db()
    assert forgotten.ended_at == at(center, today(center), 0, 0)
    assert forgotten.closed_automatically
    [closed] = audit(AuditAction.TIME_CLOSED_AUTOMATICALLY)
    assert closed.actor_label == "scheduler" and closed.center == center
    assert timeclock.close_forgotten(now) == 0


def test_todays_open_entries_stay_open(center, tutor) -> None:
    day = today(center) - timedelta(days=1)
    entry(center, tutor, day, (9, 0), None)
    assert timeclock.close_forgotten(at(center, day, 23, 59)) == 0


def test_the_scheduler_sweep_clocks_out_forgotten_entries(center, tutor) -> None:
    from sympraxi.scheduling import scheduler

    entry(center, tutor, today(center) - timedelta(days=2), (15, 0), None)
    result = scheduler.sweep(base_url="https://wc.example.edu")
    assert result.clocked_out == 1


# --- Directors' corrections ---------------------------------------------------------


def entry_data(center, **values):
    day = today(center) - timedelta(days=1)
    return {
        "category": Category.MEETING,
        "day": day.isoformat(),
        "start": "13:00",
        "end": "14:30",
        "note": "",
        "reason": "Forgot to clock in",
        **values,
    }


def test_a_director_adds_time_someone_forgot(client, center, tutor, director) -> None:
    client.force_login(director)
    url = reverse("staff:entry_add", kwargs={"slug": center.slug})
    response = client.post(url, entry_data(center, person=str(tutor.pk)))
    assert response.status_code == 302
    [added] = TimeEntry.objects.all()
    assert added.person == tutor and added.added_by == director
    assert added.hours == Decimal("1.50")
    [event] = audit(AuditAction.TIME_ENTRY_ADDED)
    assert event.details["reason"] == "Forgot to clock in"
    assert event.details["entry"]["category"] == "meeting"


def test_an_end_earlier_than_the_start_is_the_next_day(client, center, tutor, director) -> None:
    client.force_login(director)
    url = reverse("staff:entry_add", kwargs={"slug": center.slug})
    day = today(center) - timedelta(days=2)
    client.post(
        url,
        entry_data(center, person=str(tutor.pk), day=day.isoformat(), start="22:00", end="01:00"),
    )
    [added] = TimeEntry.objects.all()
    assert added.ended_at == at(center, day + timedelta(days=1), 1)
    assert added.hours == Decimal("3.00")


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"start": "06:00", "end": "23:00"}, "longer than 16 hours"),
        ({"day": "2099-01-01"}, "before it has been worked"),
        ({"reason": ""}, "This field is required"),
        ({"person": ""}, "Choose someone"),
    ],
)
def test_entries_that_cant_be_added(client, center, tutor, director, changes, says) -> None:
    client.force_login(director)
    data = entry_data(center, person=str(tutor.pk))
    data.update(changes)
    response = client.post(reverse("staff:entry_add", kwargs={"slug": center.slug}), data)
    assert response.status_code == 200
    assert says in response.content.decode()
    assert not TimeEntry.objects.exists()


def test_an_overlapping_entry_is_refused_with_a_reason(client, center, tutor, director) -> None:
    entry(center, tutor, today(center) - timedelta(days=1), (13, 0), (14, 0))
    client.force_login(director)
    response = client.post(
        reverse("staff:entry_add", kwargs={"slug": center.slug}),
        entry_data(center, person=str(tutor.pk)),
    )
    assert "overlaps another entry" in response.content.decode()
    assert TimeEntry.objects.count() == 1


def test_only_the_centers_staff_can_be_chosen(client, center, director) -> None:
    student = make_member(center, Role.STUDENT)
    client.force_login(director)
    response = client.post(
        reverse("staff:entry_add", kwargs={"slug": center.slug}),
        entry_data(center, person=str(student.pk)),
    )
    assert response.status_code == 200
    assert not TimeEntry.objects.exists()


def test_a_director_fixes_a_midnight_clock_out(client, center, tutor, director) -> None:
    yesterday = today(center) - timedelta(days=1)
    fixed = entry(center, tutor, yesterday, (15, 0), None)
    timeclock.close_forgotten(at(center, today(center), 0, 30))
    client.force_login(director)
    url = reverse("staff:entry_edit", kwargs={"slug": center.slug, "entry_id": fixed.pk})
    assert "clocked out at midnight" in client.get(url).content.decode()
    client.post(
        url,
        {
            "category": Category.TUTORING,
            "day": yesterday.isoformat(),
            "start": "15:00",
            "end": "18:00",
            "note": "",
            "reason": "Left at six",
        },
    )
    fixed.refresh_from_db()
    assert fixed.ended_at == at(center, yesterday, 18)
    assert not fixed.closed_automatically
    assert fixed.changed_by == director
    [changed] = audit(AuditAction.TIME_ENTRY_CHANGED)
    assert changed.details["before"]["ended_at"] == at(center, today(center), 0).isoformat()
    assert changed.details["after"]["ended_at"] == at(center, yesterday, 18).isoformat()
    assert changed.details["reason"] == "Left at six"


def test_an_open_entry_can_be_changed_and_left_open(client, center, tutor, director) -> None:
    started = timezone.now() - timedelta(hours=1)
    open_entry = TimeEntry.objects.create(
        center=center, person=tutor, category=Category.TUTORING, started_at=started
    )
    client.force_login(director)
    local = timezone.localtime(started, timezone=ZoneInfo(center.time_zone))
    client.post(
        reverse("staff:entry_edit", kwargs={"slug": center.slug, "entry_id": open_entry.pk}),
        {
            "category": Category.OFFICE,
            "day": local.date().isoformat(),
            "start": local.strftime("%H:%M"),
            "end": "",
            "note": "",
            "reason": "Front desk, not tutoring",
        },
    )
    open_entry.refresh_from_db()
    assert open_entry.is_open and open_entry.category == Category.OFFICE


def test_a_director_removes_an_entry_with_a_reason(client, center, tutor, director) -> None:
    removed = entry(center, tutor, today(center) - timedelta(days=1), (9, 0), (10, 0))
    client.force_login(director)
    url = reverse("staff:entry_edit", kwargs={"slug": center.slug, "entry_id": removed.pk})
    response = client.post(url, {"remove": "1", "remove-reason": "Entered twice"})
    assert response.status_code == 302
    removed.refresh_from_db()
    assert removed.removed_at is not None and removed.removed_by == director
    [event] = audit(AuditAction.TIME_ENTRY_REMOVED)
    assert event.details["reason"] == "Entered twice"
    assert event.details["before"]["category"] == "tutoring"
    # A removed entry's page is gone, and it no longer counts.
    assert client.get(url).status_code == 404
    assert "1.00" not in client.get(entries_url(center)).content.decode()


# --- Timesheets ---------------------------------------------------------------------


def test_the_timesheet_totals_hours_by_person_and_category(client, center, tutor, director) -> None:
    day = today(center) - timedelta(days=1)
    entry(center, tutor, day, (9, 0), (11, 0), Category.TUTORING)
    entry(center, tutor, day, (11, 0), (11, 45), Category.TRAINING)
    entry(center, director, day, (9, 0), (10, 0), Category.MEETING)
    TimeEntry.objects.create(
        center=center, person=director, category=Category.OFFICE, started_at=timezone.now()
    )
    report, _entries = timesheet(center, day, today(center))
    totals = {row[0]: row for row in report.tables[0].rows}
    tutoring, training, *_others, total, count, _approved = totals["Toni Tutor"][4:]
    assert (tutoring, training, total, count) == (
        Decimal("2.00"),
        Decimal("0.75"),
        Decimal("2.75"),
        2,
    )
    assert totals["Dana Director"][-3:-1] == [Decimal("1.00"), 2]  # the open entry adds no hours
    client.force_login(director)
    page = client.get(entries_url(center)).content.decode()
    assert "Clocked in now" in page and "Dana Director" in page


def test_the_timesheet_downloads(client, center, tutor, director) -> None:
    entry(center, tutor, today(center) - timedelta(days=1), (9, 0), (11, 0))
    client.force_login(director)
    response = client.get(entries_url(center), {"format": "csv"})
    assert response["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert any(row[:1] == ["Toni Tutor"] and "2.00" in row for row in rows)
    xlsx = client.get(entries_url(center), {"format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"


def test_an_entry_counts_on_the_day_it_started(center, tutor) -> None:
    day = today(center) - timedelta(days=3)
    late = entry(center, tutor, day, (23, 0), None)
    late.ended_at = at(center, day + timedelta(days=1), 1)
    late.save()
    found = list(timeclock.entries_started_between(center, day, day))
    assert found == [late]
    assert not list(
        timeclock.entries_started_between(center, day + timedelta(1), day + timedelta(1))
    )


# --- Who may ------------------------------------------------------------------------


def test_students_have_no_time_clock(client, center) -> None:
    client.force_login(make_member(center, Role.STUDENT))
    assert client.get(clock_url(center)).status_code == 403


def test_tutors_cant_see_timesheets(client, center, tutor) -> None:
    client.force_login(tutor)
    assert client.get(entries_url(center)).status_code == 403


def test_the_center_page_links_to_the_time_clock(client, center, tutor, director) -> None:
    client.force_login(tutor)
    page = client.get(reverse("centers:detail", kwargs={"slug": center.slug})).content.decode()
    assert clock_url(center) in page and entries_url(center) not in page
    client.force_login(director)
    page = client.get(reverse("centers:detail", kwargs={"slug": center.slug})).content.decode()
    assert clock_url(center) in page and entries_url(center) in page
