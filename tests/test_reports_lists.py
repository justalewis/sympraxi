"""The front desk's master lists: what they show, what they download, and the audit."""

import csv
import html
import io
import json
import zipfile
from datetime import date

import pytest
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.reports import lists, tables
from sympraxi.reports.forms import Period
from tests.reports_world import FALL, book, counted_center
from tests.scheduling_world import at, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world():
    return counted_center()


@pytest.fixture
def desk(client, world):
    user = make_member(world.center, Role.FRONT_DESK)
    client.force_login(user)
    return user


def appointments_url(world) -> str:
    return reverse("reports:appointment_list", kwargs={"slug": world.center.slug})


def students_url(world) -> str:
    return reverse("reports:student_list", kwargs={"slug": world.center.slug})


def rows_of(response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


# --- Appointments -----------------------------------------------------------------


def test_one_day(client, desk, world) -> None:
    response = client.get(appointments_url(world), {"date": "2025-09-15", "span": "day"})
    assert response.status_code == 200
    days = response.context["found"].days
    assert [(day, [a.pk for a in shown]) for day, shown in days] == [
        (date(2025, 9, 15), [world.appointments["a6"].pk])
    ]
    page = response.content.decode()
    assert "Student 3" in page
    assert "Student 4" in page
    assert "Monday, September 15, 2025" in page


def test_a_week_leaves_out_canceled_appointments_unless_asked(client, desk, world) -> None:
    week = {"date": "2025-09-10", "span": "week"}
    a = world.appointments
    found = client.get(appointments_url(world), week).context["found"]
    assert [x.pk for _day, shown in found.days for x in shown] == [
        a["a1"].pk,
        a["a2"].pk,
        a["a3"].pk,
    ]
    found = client.get(appointments_url(world), {**week, "canceled": "on"}).context["found"]
    shown = [x.pk for _day, day_rows in found.days for x in day_rows]
    assert shown == [a[n].pk for n in ("a1", "a2", "a3", "a4", "a5")]


def test_one_schedule(client, desk, world) -> None:
    query = {"date": "2025-09-10", "span": "week", "canceled": "on", "schedule": world.online.pk}
    found = client.get(appointments_url(world), query).context["found"]
    assert [x.pk for _day, shown in found.days for x in shown] == [world.appointments["a5"].pk]


def test_viewing_is_audited_with_the_students_named(client, desk, world) -> None:
    client.get(appointments_url(world), {"date": "2025-09-10", "span": "week"})
    event = AuditEvent.objects.get(action=AuditAction.LIST_VIEWED)
    assert event.actor_id == desk.pk
    assert event.center_id == world.center.pk
    assert event.details["list"] == "appointments"
    assert event.details["format"] == "page"
    assert (event.details["first"], event.details["last"]) == ("2025-09-08", "2025-09-14")
    assert sorted(event.details["students"]) == sorted([str(world.s1.pk), str(world.s2.pk)])


def test_the_download_is_audited_and_matches_the_page(client, desk, world) -> None:
    response = client.get(
        appointments_url(world), {"date": "2025-09-15", "span": "week", "format": "csv"}
    )
    assert response["Content-Disposition"] == (
        'attachment; filename="appointments-2025-09-15-to-2025-09-21.csv"'
    )
    assert "no-store" in response["Cache-Control"]
    rows = rows_of(response)
    assert [(r["Date"], r["Starts"], r["Tutor"], r["Students"]) for r in rows] == [
        ("2025-09-15", "10:00", "Ana Tutor", "Student 3; Student 4"),
        ("2025-09-16", "10:00", "Ana Tutor", "Student 1"),
        ("2025-09-17", "10:00", "Ben Tutor", ""),
        ("2025-09-18", "10:00", "Ana Tutor", "Student 2"),
    ]
    assert rows[0]["Attendance"] == "Student 3: came; Student 4: not marked"
    assert rows[1]["Status"] == "Booked, walk-in"
    assert rows[2]["Status"] == "Booked, placeholder"
    event = AuditEvent.objects.get(action=AuditAction.LIST_EXPORTED)
    assert event.details["format"] == "csv"
    assert len(event.details["students"]) == 4


def test_json_download(client, desk, world) -> None:
    response = client.get(appointments_url(world), {"date": "2025-09-15", "format": "json"})
    data = json.loads(response.content)
    assert data["format"] == "sympraxi.report.appointment-list"
    assert data["time_zone"] == "America/Chicago"
    (table,) = data["tables"]
    assert table["rows"][0]["students"] == "Student 3; Student 4"


def test_a_long_list_is_cut_short_and_says_so(client, desk, world, monkeypatch) -> None:
    monkeypatch.setattr(lists, "APPOINTMENT_LIMIT", 2)
    response = client.get(appointments_url(world), {"date": "2025-09-15", "span": "week"})
    assert response.context["found"].truncated
    assert sum(len(shown) for _day, shown in response.context["found"].days) == 2
    assert "Only the first 2 appointments are shown." in response.content.decode()


def test_tutors_and_students_cannot_open_the_lists(client, world) -> None:
    for role in (Role.TUTOR, Role.STUDENT):
        client.force_login(make_member(world.center, role))
        assert client.get(appointments_url(world)).status_code == 403
        assert client.get(students_url(world)).status_code == 403
    assert not AuditEvent.objects.filter(action=AuditAction.LIST_VIEWED).exists()


# --- Students and their visits -------------------------------------------------------


def test_visit_counts(world) -> None:
    period = Period(FALL[0], FALL[1], world.term, None)
    members = list(lists.students(world.center, period))
    rows = [lists.student_row(m, world.in_person.tz) for m in members]
    assert [(r[0], *r[4:]) for r in rows] == [
        ("Student 1", 2, 2, 0, 0, date(2025, 7, 1), date(2025, 9, 16)),
        ("Student 2", 3, 2, 1, 0, date(2025, 9, 9), date(2025, 9, 18)),
        ("Student 3", 2, 1, 0, 1, date(2025, 9, 15), date(2025, 9, 15)),
        ("Student 4", 2, 0, 0, 0, None, None),
    ]


def test_visit_counts_on_one_schedule(world) -> None:
    period = Period(FALL[0], FALL[1], world.term, world.online)
    members = list(lists.students(world.center, period))
    assert [(m.user.full_name, m.booked, m.came) for m in members] == [("Student 4", 1, 0)]


def test_the_student_list_page_is_paged_and_audited(client, desk, world, monkeypatch) -> None:
    monkeypatch.setattr("sympraxi.reports.views.STUDENTS_PER_PAGE", 3)
    query = {"term": str(world.term.pk)}
    first = client.get(students_url(world), query)
    assert first.status_code == 200
    assert [r[0] for r in first.context["table"].rows] == ["Student 1", "Student 2", "Student 3"]
    second = client.get(students_url(world), {**query, "page": "2"})
    assert [r[0] for r in second.context["table"].rows] == ["Student 4"]
    events = list(AuditEvent.objects.filter(action=AuditAction.LIST_VIEWED).order_by("id"))
    assert [e.details["page"] for e in events] == [1, 2]
    assert events[1].details["students"] == [str(world.s4.pk)]


def test_the_student_download_has_every_student(client, desk, world, monkeypatch) -> None:
    monkeypatch.setattr("sympraxi.reports.views.STUDENTS_PER_PAGE", 1)
    response = client.get(students_url(world), {"term": str(world.term.pk), "format": "csv"})
    rows = rows_of(response)
    assert [r["Name"] for r in rows] == ["Student 1", "Student 2", "Student 3", "Student 4"]
    assert rows[0]["First visit"] == "2025-07-01"
    assert rows[3]["First visit"] == ""
    event = AuditEvent.objects.get(action=AuditAction.LIST_EXPORTED)
    assert len(event.details["students"]) == 4


def test_names_that_look_like_formulas_stay_text(client, desk, world) -> None:
    sneaky = make_member(world.center, Role.STUDENT, full_name='=HYPERLINK("https://x.test","y")')
    sneaky.preferred_name = "+1 555"
    sneaky.save()
    assignment = world.in_person.assignments.first()
    book(world.in_person, assignment, at(world.in_person, date(2025, 9, 22), 9), [sneaky])
    response = client.get(students_url(world), {"term": str(world.term.pk), "format": "csv"})
    row = next(r for r in rows_of(response) if "HYPERLINK" in r["Name"])
    assert row["Name"] == '\'=HYPERLINK("https://x.test","y")'
    assert row["Goes by"] == "'+1 555"
    listed = client.get(appointments_url(world), {"date": "2025-09-22", "format": "csv"})
    assert rows_of(listed)[0]["Students"] == "'+1 555"


def test_the_xlsx_download_runs_no_formulas(client, desk, world) -> None:
    sneaky = make_member(world.center, Role.STUDENT, full_name="=1+1")
    assignment = world.in_person.assignments.first()
    book(world.in_person, assignment, at(world.in_person, date(2025, 9, 22), 9), [sneaky])
    response = client.get(students_url(world), {"term": str(world.term.pk), "format": "xlsx"})
    assert response["Content-Type"] == tables.CONTENT_TYPES["xlsx"]
    assert response["Content-Disposition"] == (
        'attachment; filename="students-2025-09-01-to-2025-12-15.xlsx"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as workbook:
        sheets = [
            workbook.read(name).decode()
            for name in workbook.namelist()
            if name.startswith("xl/worksheets/sheet")
        ]
        strings = html.unescape(workbook.read("xl/sharedStrings.xml").decode())
    assert not any("<f>" in sheet for sheet in sheets)
    assert "<t>'=1+1</t>" in strings
    assert AuditEvent.objects.get(action=AuditAction.LIST_EXPORTED).details["format"] == "xlsx"
