"""Where staff may clock in, approved hours that lock, and what payroll receives."""

import csv
import io
from datetime import timedelta

import pytest
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.staff import timeclock
from sympraxi.staff.models import Category, PayrollDetail, TimeEntry, TimesheetApproval
from sympraxi.staff.views import timesheet
from tests.scheduling_world import make_member
from tests.staff_world import at, entry, today

pytestmark = pytest.mark.django_db

INSIDE = "203.0.113.9"
OUTSIDE = "198.51.100.7"


@pytest.fixture
def tutor(center):
    return make_member(center, Role.TUTOR, full_name="Toni Tutor", email="toni@example.edu")


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


def url(name: str, center) -> str:
    return reverse(f"staff:{name}", kwargs={"slug": center.slug})


def clock_in(client, center, address: str):
    return client.post(
        url("clock", center),
        {"action": "in", "category": Category.TUTORING},
        REMOTE_ADDR=address,
        follow=True,
    )


# --- Networks ---------------------------------------------------------------------------


def test_with_no_networks_set_staff_clock_in_from_anywhere(client, center, tutor) -> None:
    client.force_login(tutor)
    clock_in(client, center, OUTSIDE)
    assert TimeEntry.objects.count() == 1


def test_staff_clock_in_and_out_only_from_the_centers_network(client, center, tutor) -> None:
    center.clock_networks = "203.0.113.0/24\n# the library\n2001:db8::/32"
    center.save()
    client.force_login(tutor)
    refused = clock_in(client, center, OUTSIDE)
    assert "center&#x27;s network" in refused.content.decode()
    assert TimeEntry.objects.count() == 0
    assert AuditEvent.objects.filter(action=AuditAction.TIME_CLOCK_REFUSED).count() == 1
    clock_in(client, center, INSIDE)
    assert TimeEntry.objects.count() == 1
    out = client.post(url("clock", center), {"action": "out"}, REMOTE_ADDR=OUTSIDE, follow=True)
    assert "center&#x27;s network" in out.content.decode()
    assert TimeEntry.objects.get().is_open
    client.post(url("clock", center), {"action": "out"}, REMOTE_ADDR=INSIDE)
    assert not TimeEntry.objects.get().is_open


def test_an_ipv6_address_inside_a_listed_range_is_allowed(client, center, tutor) -> None:
    center.clock_networks = "2001:db8::/32"
    center.save()
    client.force_login(tutor)
    clock_in(client, center, "2001:db8::5")
    assert TimeEntry.objects.count() == 1


def test_directors_can_clock_in_from_anywhere(client, center, director) -> None:
    center.clock_networks = "203.0.113.0/24"
    center.save()
    client.force_login(director)
    clock_in(client, center, OUTSIDE)
    assert TimeEntry.objects.count() == 1


def test_a_director_can_still_add_time_for_someone_outside_the_network(
    client, center, tutor, director
) -> None:
    center.clock_networks = "203.0.113.0/24"
    center.save()
    client.force_login(director)
    day = today(center) - timedelta(days=1)
    response = client.post(
        reverse("staff:entry_add", kwargs={"slug": center.slug}),
        {
            "person": str(tutor.pk),
            "category": Category.TUTORING,
            "day": day.isoformat(),
            "start": "09:00",
            "end": "10:00",
            "reason": "Forgot to clock in",
        },
        REMOTE_ADDR=OUTSIDE,
    )
    assert response.status_code == 302 and TimeEntry.objects.count() == 1


def test_the_settings_page_saves_networks_and_rejects_nonsense(client, center, director) -> None:
    client.force_login(director)
    bad = client.post(
        url("settings", center), {"save": "networks", "clock_networks": "not-an-address"}
    )
    assert "isn&#x27;t an IP address" in bad.content.decode()
    center.refresh_from_db()
    assert center.clock_networks == ""
    client.post(
        url("settings", center),
        {"save": "networks", "clock_networks": "  203.0.113.0/24 \n\n198.51.100.7  "},
    )
    center.refresh_from_db()
    assert center.clock_networks == "203.0.113.0/24\n198.51.100.7"
    assert AuditEvent.objects.filter(action=AuditAction.TIME_SETTINGS_CHANGED).count() == 1


def test_the_settings_page_saves_payroll_details(client, center, tutor, director) -> None:
    client.force_login(director)
    client.post(
        url("settings", center),
        {
            "save": "payroll",
            f"id_{tutor.pk}": "E1042",
            f"code_{tutor.pk}": "STU-TUTOR",
            f"id_{director.pk}": "",
            f"code_{director.pk}": "",
        },
    )
    detail = PayrollDetail.objects.get(person=tutor)
    assert (detail.payroll_id, detail.pay_code) == ("E1042", "STU-TUTOR")
    page = client.get(url("settings", center)).content.decode()
    assert "E1042" in page
    event = AuditEvent.objects.filter(action=AuditAction.TIME_SETTINGS_CHANGED).get()
    assert "E1042" not in str(event.details)


# --- Approvals ---------------------------------------------------------------------------


def worked(center, person, days_ago: int = 5):
    return entry(center, person, today(center) - timedelta(days=days_ago), (9, 0), (12, 30))


def period(center, first_ago: int = 7, last_ago: int = 1):
    return today(center) - timedelta(days=first_ago), today(center) - timedelta(days=last_ago)


def test_a_director_approves_a_persons_hours_for_a_period(client, center, tutor, director) -> None:
    worked(center, tutor)
    first, last = period(center)
    client.force_login(director)
    response = client.post(
        url("approvals", center),
        {
            "action": "approve",
            "person": str(tutor.pk),
            "first": first.isoformat(),
            "last": last.isoformat(),
        },
        follow=True,
    )
    assert "Hours approved." in response.content.decode()
    approval = TimesheetApproval.objects.get()
    assert (approval.person, approval.hours, approval.approved_by) == (
        tutor,
        timeclock.to_hours(timedelta(hours=3.5)),
        director,
    )
    event = AuditEvent.objects.get(action=AuditAction.TIME_APPROVED)
    assert event.details["hours"] == "3.50"
    page = client.get(url("approvals", center), {"first": first, "last": last}).content.decode()
    assert "Withdraw approval" in page and "Approve Toni Tutor" not in page


def test_approved_hours_cannot_be_added_changed_or_removed(client, center, tutor, director) -> None:
    kept = worked(center, tutor)
    first, last = period(center)
    client.force_login(director)
    client.post(
        url("approvals", center),
        {"action": "approve", "person": str(tutor.pk), "first": first, "last": last},
    )
    day = today(center) - timedelta(days=4)
    added = client.post(
        reverse("staff:entry_add", kwargs={"slug": center.slug}),
        {
            "person": str(tutor.pk),
            "category": Category.TUTORING,
            "day": day.isoformat(),
            "start": "13:00",
            "end": "14:00",
            "reason": "Late",
        },
    )
    assert "are approved" in added.content.decode() and TimeEntry.objects.count() == 1
    edit_url = reverse("staff:entry_edit", kwargs={"slug": center.slug, "entry_id": kept.pk})
    changed = client.post(
        edit_url,
        {
            "person": str(tutor.pk),
            "category": Category.TUTORING,
            "day": (today(center) - timedelta(days=5)).isoformat(),
            "start": "09:00",
            "end": "17:00",
            "reason": "Longer",
        },
    )
    assert "are approved" in changed.content.decode()
    kept.refresh_from_db()
    assert kept.ended_at == at(center, today(center) - timedelta(days=5), 12, 30)
    with pytest.raises(timeclock.TimeClockError):
        timeclock.require_unlocked(center, tutor.pk, kept.started_at)


def test_another_persons_hours_stay_open_to_change(client, center, tutor, director) -> None:
    worked(center, tutor)
    other = make_member(center, Role.TUTOR, full_name="Olu Other")
    open_one = worked(center, other)
    first, last = period(center)
    client.force_login(director)
    client.post(
        url("approvals", center),
        {"action": "approve", "person": str(tutor.pk), "first": first, "last": last},
    )
    timeclock.require_unlocked(center, other.pk, open_one.started_at)  # no error


def test_withdrawing_needs_a_reason_and_unlocks_the_hours(client, center, tutor, director) -> None:
    kept = worked(center, tutor)
    first, last = period(center)
    client.force_login(director)
    client.post(
        url("approvals", center),
        {"action": "approve", "person": str(tutor.pk), "first": first, "last": last},
    )
    approval = TimesheetApproval.objects.get()
    client.post(
        url("approvals", center),
        {"action": "withdraw", "w-approval": str(approval.pk), "w-reason": ""},
    )
    approval.refresh_from_db()
    assert approval.is_active
    client.post(
        url("approvals", center),
        {"action": "withdraw", "w-approval": str(approval.pk), "w-reason": "Wrong week"},
    )
    approval.refresh_from_db()
    assert not approval.is_active and approval.withdrawn_by == director
    event = AuditEvent.objects.get(action=AuditAction.TIME_APPROVAL_WITHDRAWN)
    assert event.details["reason"] == "Wrong week"
    timeclock.require_unlocked(center, tutor.pk, kept.started_at)  # no error now


@pytest.mark.parametrize(
    "setup",
    ["open", "midnight", "today", "overlap"],
)
def test_hours_that_are_not_ready_cannot_be_approved(
    client, center, tutor, director, setup
) -> None:
    first, last = period(center)
    if setup == "open":
        entry(center, tutor, today(center) - timedelta(days=3), (9, 0), None)
    elif setup == "midnight":
        entry(
            center,
            tutor,
            today(center) - timedelta(days=3),
            (9, 0),
            (23, 59),
            closed_automatically=True,
        )
    elif setup == "overlap":
        worked(center, tutor)
        timeclock.approve_period(center, tutor, first, last, request=_request(director))
    else:
        last = today(center)
        worked(center, tutor)
    client.force_login(director)
    client.post(
        url("approvals", center),
        {"action": "approve", "person": str(tutor.pk), "first": first, "last": last},
    )
    assert TimesheetApproval.objects.count() == (1 if setup == "overlap" else 0)


def _request(user):
    from django.test import RequestFactory

    request = RequestFactory().post("/")
    request.user = user
    return request


def test_approving_a_stranger_is_refused(client, center, director) -> None:
    from tests.factories import UserFactory

    client.force_login(director)
    first, last = period(center)
    client.post(
        url("approvals", center),
        {"action": "approve", "person": str(UserFactory().pk), "first": first, "last": last},
    )
    assert TimesheetApproval.objects.count() == 0


def test_an_approval_is_only_the_directors_own_centers(
    client, center, other_center, director
) -> None:
    outsider = make_member(other_center, Role.TUTOR)
    entry(other_center, outsider, today(other_center) - timedelta(days=3), (9, 0), (10, 0))
    first, last = period(center)
    client.force_login(director)
    client.post(
        url("approvals", center),
        {"action": "approve", "person": str(outsider.pk), "first": first, "last": last},
    )
    assert TimesheetApproval.objects.count() == 0


# --- What payroll receives --------------------------------------------------------------------


def test_the_timesheet_carries_payroll_ids_and_approval(client, center, tutor, director) -> None:
    worked(center, tutor)
    PayrollDetail.objects.create(center=center, person=tutor, payroll_id="E1042", pay_code="STU")
    first, last = period(center)
    before, _ = timesheet(center, first, last)
    assert (
        before.tables[0].rows[0][2:4] == ["E1042", "STU"] and before.tables[0].rows[0][-1] == "No"
    )
    timeclock.approve_period(center, tutor, first, last, request=_request(director))
    after, listed = timesheet(center, first, last)
    assert after.tables[0].rows[0][-1] == "Yes" and after.tables[1].rows[0][-1] == "Yes"
    assert listed[0].is_approved


def test_a_period_part_approved_reads_partly(center, tutor, director) -> None:
    entry(center, tutor, today(center) - timedelta(days=6), (9, 0), (10, 0))
    entry(center, tutor, today(center) - timedelta(days=2), (9, 0), (10, 0))
    first, last = period(center)
    timeclock.approve_period(
        center, tutor, first, today(center) - timedelta(days=4), request=_request(director)
    )
    report, _ = timesheet(center, first, last)
    assert report.tables[0].rows[0][-1] == "Partly"


def test_the_download_can_be_limited_to_approved_hours(client, center, tutor, director) -> None:
    worked(center, tutor)
    other = make_member(center, Role.TUTOR, full_name="Olu Other", email="olu@example.edu")
    worked(center, other, days_ago=4)
    first, last = period(center)
    timeclock.approve_period(center, tutor, first, last, request=_request(director))
    client.force_login(director)

    def names(params: dict[str, str]) -> set[str]:
        response = client.get(url("entries", center), {"format": "csv", **params})
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        return {row[0] for row in rows if row[:1] in (["Toni Tutor"], ["Olu Other"])}

    assert names({}) == {"Toni Tutor", "Olu Other"}
    assert names({"approved": "1"}) == {"Toni Tutor"}


def test_the_new_pages_are_for_directors_only(client, center, tutor) -> None:
    client.force_login(tutor)
    for name in ("approvals", "settings"):
        assert client.get(url(name, center)).status_code == 403
