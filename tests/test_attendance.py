"""Marking attendance, the possible no-show flag, final records, waivers and the
no-show email."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.core import mail

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import attendance
from sympraxi.scheduling.attendance import AttendanceError
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Modality,
    Participant,
)
from tests.attendance_world import participant
from tests.factories import CenterFactory
from tests.records_world import standalone, write_record
from tests.scheduling_world import assign, at, make_member, make_schedule

pytestmark = pytest.mark.django_db

TUESDAY = date(2027, 1, 12)


@pytest.fixture
def center():
    return CenterFactory(
        time_zone="America/New_York", name="Write Place", contact_email="help@example.edu"
    )


@pytest.fixture
def schedule(center):
    return make_schedule(center, date(2027, 1, 4), date(2027, 2, 28), no_show_grace_minutes=15)


@pytest.fixture
def assignment(schedule):
    tutor = make_member(schedule.center, Role.TUTOR, full_name="Tomás Tutor")
    return assign(schedule, tutor, hours=((time(0), time(23, 30)),), capacity=4)


@pytest.fixture
def student(center):
    return make_member(center, Role.STUDENT, full_name="Sam Private-Name")


def appointment(assignment, students, start: datetime, **fields) -> Appointment:
    a = Appointment.objects.create(
        schedule=assignment.schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        blocked_until=start + timedelta(minutes=30),
        modality=Modality.IN_PERSON,
        capacity=max(1, len(students)),
        **fields,
    )
    for s in students:
        Participant.objects.create(appointment=a, student=s)
    return a


def past(assignment, students, *, minutes_ago: int = 60, **fields) -> Appointment:
    start = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=minutes_ago)
    return appointment(assignment, students, start, **fields)


# --- Marking ---------------------------------------------------------------------------


def test_the_tutor_marks_who_came(
    assignment, student, center, django_capture_on_commit_callbacks
) -> None:
    other = make_member(center, Role.STUDENT, full_name="Lee Other-Student")
    a = past(assignment, [student, other])
    with django_capture_on_commit_callbacks(execute=True):
        changed = attendance.mark(
            actor=assignment.tutor,
            appointment=a,
            marks={student.pk: Attendance.ATTENDED, other.pk: Attendance.NO_SHOW},
        )
    assert changed == 2
    assert participant(a, student).attendance == Attendance.ATTENDED
    no_show = participant(a, other)
    assert no_show.attendance == Attendance.NO_SHOW
    assert no_show.attendance_marked_by == assignment.tutor
    # The appointment itself is unchanged: its time was held either way.
    a.refresh_from_db()
    assert a.status == AppointmentStatus.SCHEDULED
    events = AuditEvent.objects.filter(action=AuditAction.ATTENDANCE_MARKED)
    assert {(e.details["student"], e.details["after"]) for e in events} == {
        (str(student.pk), "attended"),
        (str(other.pk), "no_show"),
    }
    # Only the no-show gets an email, and only about themselves.
    [message] = mail.outbox
    assert message.to == [other.email]
    assert "Sam" not in message.body


def test_marking_the_same_thing_again_changes_nothing(assignment, student) -> None:
    a = past(assignment, [student])
    marks = {student.pk: Attendance.ATTENDED}
    assert attendance.mark(actor=assignment.tutor, appointment=a, marks=marks) == 1
    assert attendance.mark(actor=assignment.tutor, appointment=a, marks=marks) == 0
    assert AuditEvent.objects.filter(action=AuditAction.ATTENDANCE_MARKED).count() == 1


def test_attendance_waits_for_the_start(assignment, student) -> None:
    soon = appointment(assignment, [student], datetime.now(UTC) + timedelta(minutes=5))
    with pytest.raises(AttendanceError) as refused:
        attendance.mark(
            actor=assignment.tutor, appointment=soon, marks={student.pk: Attendance.NO_SHOW}
        )
    assert refused.value.code == "not_started"


def test_only_the_tutor_of_the_appointment_and_staff_can_mark(assignment, student, center) -> None:
    a = past(assignment, [student])
    marks = {student.pk: Attendance.ATTENDED}
    for outsider in (make_member(center, Role.TUTOR), student):
        with pytest.raises(AttendanceError) as refused:
            attendance.mark(actor=outsider, appointment=a, marks=marks)
        assert refused.value.code == "not_allowed"
    attendance.mark(actor=make_member(center, Role.FRONT_DESK), appointment=a, marks=marks)
    assert participant(a, student).attendance == Attendance.ATTENDED


def test_canceled_appointments_and_students_who_left_cannot_be_marked(
    assignment, student, center
) -> None:
    other = make_member(center, Role.STUDENT)
    a = past(assignment, [student, other])
    Participant.objects.filter(appointment=a, student=other).update(canceled_at=a.starts_at)
    with pytest.raises(AttendanceError) as refused:
        attendance.mark(actor=assignment.tutor, appointment=a, marks={other.pk: Attendance.NO_SHOW})
    assert refused.value.code == "not_in_it"
    Appointment.objects.filter(pk=a.pk).update(status=AppointmentStatus.CANCELED)
    with pytest.raises(AttendanceError) as refused:
        attendance.mark(
            actor=assignment.tutor, appointment=a, marks={student.pk: Attendance.NO_SHOW}
        )
    assert refused.value.code == "canceled"


# --- A final session record means the student came ---------------------------------------


def test_finalizing_a_record_marks_the_student_as_here(assignment, student) -> None:
    a = past(assignment, [student])
    write_record(a, student)
    p = participant(a, student)
    assert p.attendance == Attendance.ATTENDED
    assert p.attendance_marked_by == assignment.tutor
    event = AuditEvent.objects.get(action=AuditAction.ATTENDANCE_MARKED)
    assert event.details["source"] == "session record"


def test_a_student_with_a_final_record_cannot_be_a_no_show(assignment, student) -> None:
    a = past(assignment, [student])
    write_record(a, student)
    with pytest.raises(AttendanceError) as refused:
        attendance.mark(
            actor=assignment.tutor, appointment=a, marks={student.pk: Attendance.NO_SHOW}
        )
    assert refused.value.code == "has_record"


def test_a_final_record_overrides_a_no_show(assignment, student) -> None:
    a = past(assignment, [student])
    attendance.mark(actor=assignment.tutor, appointment=a, marks={student.pk: Attendance.NO_SHOW})
    write_record(a, student)
    assert participant(a, student).attendance == Attendance.ATTENDED


def test_a_record_for_a_meeting_that_was_not_booked_marks_nothing(
    assignment, student, center
) -> None:
    rec = standalone(center, student, assignment.tutor)
    assert not attendance.record_came(rec, actor=assignment.tutor, now=datetime.now(UTC))


# --- Possible no-shows ---------------------------------------------------------------


def test_a_student_is_flagged_once_the_grace_period_passes(assignment, student) -> None:
    start = at(assignment.schedule, TUESDAY, 10)
    a = appointment(assignment, [student], start)
    assert attendance.flag_possible_no_shows(start + timedelta(minutes=14, seconds=59)) == 0
    assert attendance.flag_possible_no_shows(start + timedelta(minutes=15)) == 1
    p = participant(a, student)
    assert p.is_possible_no_show
    assert p.flagged_at == start + timedelta(minutes=15)
    # Flagging is all it does: no mark, no email, and nothing counts yet.
    assert p.attendance == Attendance.UNMARKED
    assert mail.outbox == []
    # A second sweep, or a second scheduler, leaves it alone.
    assert attendance.flag_possible_no_shows(start + timedelta(minutes=16)) == 0


def test_each_schedule_sets_its_own_grace_period(assignment, student, schedule) -> None:
    schedule.no_show_grace_minutes = 30
    schedule.save()
    start = at(schedule, TUESDAY, 10)
    appointment(assignment, [student], start)
    assert attendance.flag_possible_no_shows(start + timedelta(minutes=29)) == 0
    assert attendance.flag_possible_no_shows(start + timedelta(minutes=30)) == 1


def test_who_is_never_flagged(assignment, student, center, schedule) -> None:
    start = at(schedule, TUESDAY, 9)
    later = start + timedelta(hours=8)
    marked = appointment(assignment, [student], start)
    Participant.objects.filter(appointment=marked).update(attendance=Attendance.ATTENDED)
    appointment(assignment, [student], start + timedelta(hours=1), is_tentative=True)
    appointment(assignment, [], start + timedelta(hours=2), is_placeholder=True)
    canceled = appointment(assignment, [student], start + timedelta(hours=3))
    Participant.objects.filter(appointment=canceled).update(canceled_at=start)
    Appointment.objects.filter(pk=canceled.pk).update(status=AppointmentStatus.CANCELED)
    drafted = appointment(assignment, [student], start + timedelta(hours=4))
    write_record(drafted, student, final=False, now=later)
    left = appointment(
        assignment, [student, make_member(center, Role.STUDENT)], start + timedelta(hours=5)
    )
    Participant.objects.filter(appointment=left, student=student).update(canceled_at=start)
    flagged = attendance.flag_possible_no_shows(later)
    # Only the other student in the group appointment `left`.
    assert flagged == 1
    assert Participant.objects.get(flagged_at__isnull=False).appointment == left


def test_the_sweep_does_not_reach_back_past_two_days(assignment, student, schedule) -> None:
    start = at(schedule, TUESDAY, 10)
    appointment(assignment, [student], start)
    assert attendance.flag_possible_no_shows(start + timedelta(days=2, minutes=1)) == 0


def test_staff_confirm_a_possible_no_show(
    assignment, student, center, django_capture_on_commit_callbacks
) -> None:
    a = past(assignment, [student], minutes_ago=30)
    attendance.flag_possible_no_shows()
    desk = make_member(center, Role.FRONT_DESK)
    with django_capture_on_commit_callbacks(execute=True):
        attendance.mark(actor=desk, appointment=a, marks={student.pk: Attendance.NO_SHOW})
    p = participant(a, student)
    assert p.is_no_show and not p.is_possible_no_show
    assert p.flag_cleared_at is None  # confirmed, not cleared
    assert [m.to for m in mail.outbox] == [[student.email]]


@pytest.mark.parametrize("answer", [Attendance.ATTENDED, Attendance.UNMARKED])
def test_staff_clear_a_possible_no_show(assignment, student, center, answer) -> None:
    a = past(assignment, [student], minutes_ago=30)
    attendance.flag_possible_no_shows()
    desk = make_member(center, Role.FRONT_DESK)
    assert attendance.mark(actor=desk, appointment=a, marks={student.pk: answer}) == 1
    p = participant(a, student)
    assert not p.is_possible_no_show
    assert p.flag_cleared_by == desk
    assert p.attendance == answer
    # Cleared stays cleared.
    assert attendance.flag_possible_no_shows() == 0
    assert mail.outbox == []


# --- The no-show email -----------------------------------------------------------------


def test_the_no_show_email_is_gentle_and_about_one_person(
    assignment, student, center, django_capture_on_commit_callbacks
) -> None:
    a = appointment(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    with django_capture_on_commit_callbacks(execute=True):
        attendance.mark(
            actor=assignment.tutor,
            appointment=a,
            marks={student.pk: Attendance.NO_SHOW},
            now=a.starts_at + timedelta(minutes=20),
        )
    [message] = mail.outbox
    assert message.subject == "[Sympraxi] We missed you on Jan 12"
    assert "We missed you at your appointment at Write Place on Tuesday, January 12" in (
        message.body
    )
    assert "10 a.m. EST" in message.body
    assert "with Tomás Tutor" in message.body
    assert "That makes 1 missed appointment this term." in message.body
    assert "reply to this email" in message.body
    assert f"/centers/{center.slug}/schedules/" in message.body
    assert message.reply_to == ["help@example.edu"]
    assert "—" not in message.body


def test_no_email_when_the_no_show_is_undone_before_it_goes(
    assignment, student, django_capture_on_commit_callbacks
) -> None:
    a = past(assignment, [student])
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        attendance.mark(
            actor=assignment.tutor, appointment=a, marks={student.pk: Attendance.NO_SHOW}
        )
    attendance.mark(actor=assignment.tutor, appointment=a, marks={student.pk: Attendance.ATTENDED})
    for callback in callbacks:
        callback()
    assert mail.outbox == []


# --- Waivers -------------------------------------------------------------------------------


def test_front_desk_waive_a_no_show_with_a_reason(assignment, student, center) -> None:
    a = past(assignment, [student])
    attendance.mark(actor=assignment.tutor, appointment=a, marks={student.pk: Attendance.NO_SHOW})
    desk = make_member(center, Role.FRONT_DESK)
    with pytest.raises(AttendanceError) as refused:
        attendance.waive(actor=desk, appointment=a, student_id=student.pk, reason="  ")
    assert refused.value.code == "reason"
    with pytest.raises(AttendanceError) as refused:
        attendance.waive(
            actor=assignment.tutor, appointment=a, student_id=student.pk, reason="Sick"
        )
    assert refused.value.code == "not_allowed"
    attendance.waive(
        actor=desk, appointment=a, student_id=student.pk, reason="Family emergency; called us."
    )
    p = participant(a, student)
    assert p.waived_by == desk
    assert p.waiver_reason == "Family emergency; called us."
    assert p.attendance == Attendance.NO_SHOW  # still on the record
    event = AuditEvent.objects.get(action=AuditAction.ATTENDANCE_WAIVED)
    assert event.details == {"student": str(student.pk), "what": "no_show"}
    # The reason stays out of the audit log, like an exemption's note.
    assert "emergency" not in str(event.details)
    with pytest.raises(AttendanceError) as refused:
        attendance.waive(actor=desk, appointment=a, student_id=student.pk, reason="Again")
    assert refused.value.code == "nothing"


def test_there_is_nothing_to_waive_for_a_student_who_came(assignment, student, center) -> None:
    a = past(assignment, [student])
    with pytest.raises(AttendanceError) as refused:
        attendance.waive(
            actor=make_member(center, Role.DIRECTOR),
            appointment=a,
            student_id=student.pk,
            reason="Just in case",
        )
    assert refused.value.code == "nothing"
