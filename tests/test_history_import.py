from io import StringIO

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import history
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Modality,
    Participant,
    Schedule,
    Term,
    Visibility,
)
from tests.factories import CenterFactory
from tests.scheduling_world import make_member

HEADER = "date,start,end,tutor_email,student_email,status,modality\n"


@pytest.fixture
def world(db):
    center = CenterFactory(time_zone="America/New_York")
    tutor = make_member(center, Role.TUTOR, email="tutor@example.edu")
    student = make_member(center, Role.STUDENT, email="student@example.edu")
    return center, tutor, student


def csv_of(*lines: str) -> bytes:
    return (HEADER + "\n".join(lines) + "\n").encode()


def test_past_appointments_become_attendance_without_any_email(world) -> None:
    center, *_ = world
    data = csv_of(
        "2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,in person",
        "2025-10-15,10:00,10:30,Tutor@Example.edu,student@example.edu,no show,online",
        "2025-10-16,10:00,10:30,tutor@example.edu,student@example.edu,late cancel,",
        "2025-10-17,10:00,10:30,tutor@example.edu,student@example.edu,canceled,",
        "10/18/2025,9:00 AM,9:30 AM,tutor@example.edu,student@example.edu,,",
    )
    result = history.run(center, data)
    assert result.ok and result.imported == 5
    assert mail.outbox == []
    parts = {p.appointment.starts_at.date().isoformat(): p for p in Participant.objects.all()}
    assert parts["2025-10-14"].attendance == Attendance.ATTENDED
    assert parts["2025-10-15"].attendance == Attendance.NO_SHOW
    assert parts["2025-10-15"].appointment.modality == Modality.ONLINE
    assert parts["2025-10-16"].late_cancel and parts["2025-10-16"].canceled_at is not None
    assert parts["2025-10-17"].appointment.status == AppointmentStatus.CANCELED
    assert not parts["2025-10-17"].late_cancel
    assert parts["2025-10-18"].attendance == Attendance.UNMARKED
    first = parts["2025-10-14"].appointment
    assert (first.starts_at.hour, first.ends_at.minute) == (
        18,
        45,
    )  # 14:00 in New York is 18:00 UTC


def test_the_history_sits_on_a_hidden_schedule_and_term(world) -> None:
    center, *_ = world
    history.run(
        center, csv_of("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,")
    )
    schedule = Schedule.objects.get(center=center)
    assert schedule.visibility == Visibility.HIDDEN and schedule.name == history.TERM_NAME
    term = Term.objects.get(center=center)
    assert (term.starts_on.isoformat(), term.ends_on.isoformat()) == ("2025-10-14", "2025-10-14")
    assert schedule.assignments.get().is_active is False


def test_running_the_same_file_twice_adds_nothing(world) -> None:
    center, *_ = world
    data = csv_of("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,")
    history.run(center, data)
    again = history.run(center, data)
    assert (again.imported, again.already) == (0, 1)
    assert Appointment.objects.count() == 1


def test_a_later_file_widens_the_term(world) -> None:
    center, *_ = world
    history.run(
        center, csv_of("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,")
    )
    history.run(
        center, csv_of("2024-09-02,14:00,14:45,tutor@example.edu,student@example.edu,attended,")
    )
    term = Term.objects.get(center=center)
    assert (term.starts_on.isoformat(), term.ends_on.isoformat()) == ("2024-09-02", "2025-10-14")
    assert Schedule.objects.get(center=center).starts_on.isoformat() == "2024-09-02"


@pytest.mark.parametrize(
    ("line", "why"),
    [
        ("tomorrow,14:00,14:45,tutor@example.edu,student@example.edu,attended,", "date"),
        ("2025-10-14,25:00,26:00,tutor@example.edu,student@example.edu,attended,", "time"),
        (
            "2025-10-14,15:00,14:00,tutor@example.edu,student@example.edu,attended,",
            "end is not after",
        ),
        ("2099-01-01,14:00,14:45,tutor@example.edu,student@example.edu,attended,", "future"),
        ("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,maybe,", "status"),
        (
            "2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,hovercraft",
            "modality",
        ),
        ("2025-10-14,14:00,14:45,,student@example.edu,attended,", "email"),
        (
            "2025-10-14,14:00,14:45,nobody@example.edu,student@example.edu,attended,",
            "isn't a tutor",
        ),
        (
            "2025-10-14,14:00,14:45,tutor@example.edu,nobody@example.edu,attended,",
            "isn't a student",
        ),
    ],
)
def test_a_bad_row_is_reported_and_the_good_rows_still_go_in(world, line, why) -> None:
    center, *_ = world
    good = "2025-10-20,14:00,14:45,tutor@example.edu,student@example.edu,attended,"
    result = history.run(center, csv_of(line, good))
    assert result.imported == 1
    assert (
        len(result.problems) == 1 and why in result.problems[0] and "Line 2" in result.problems[0]
    )


def test_a_tutor_cannot_be_in_two_places_at_once(world) -> None:
    center, *_ = world
    other = make_member(center, Role.STUDENT, email="other@example.edu")
    result = history.run(
        center,
        csv_of(
            "2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,",
            "2025-10-14,14:30,15:00,tutor@example.edu,other@example.edu,attended,",
        ),
    )
    assert result.imported == 1 and "already has another appointment" in result.problems[0]
    assert Participant.objects.filter(student=other).count() == 0


def test_a_file_with_missing_columns_is_refused(world) -> None:
    center, *_ = world
    result = history.run(center, b"date,start\n2025-10-14,14:00\n")
    assert "tutor_email" in result.file_problem and Appointment.objects.count() == 0
    assert history.run(center, b"").file_problem == "The file is empty."


def test_a_dry_run_changes_nothing(world) -> None:
    center, *_ = world
    result = history.run(
        center,
        csv_of("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,"),
        dry_run=True,
    )
    assert (
        result.imported == 1 and Appointment.objects.count() == 0 and Schedule.objects.count() == 0
    )


def test_the_import_is_logged(world) -> None:
    center, *_ = world
    history.run(
        center, csv_of("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,")
    )
    event = AuditEvent.objects.get(action=AuditAction.HISTORY_IMPORTED)
    assert event.center == center and event.details["imported"] == 1


def test_the_command(world, tmp_path) -> None:
    center, *_ = world
    path = tmp_path / "history.csv"
    path.write_bytes(
        csv_of("2025-10-14,14:00,14:45,tutor@example.edu,student@example.edu,attended,")
    )
    out = StringIO()
    call_command("import_history", center.slug, path, stdout=out)
    assert "Imported 1 appointments" in out.getvalue()
    with pytest.raises(CommandError):
        call_command("import_history", "no-such-center", path)
    with pytest.raises(CommandError):
        call_command("import_history", center.slug, tmp_path / "missing.csv")
