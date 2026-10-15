"""Late cancellations: the window, who asked, groups, the email, waivers and the cancel page."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.core import mail
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import attendance, services
from sympraxi.scheduling.models import Appointment, Modality, Participant
from tests.attendance_world import participant
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule

pytestmark = pytest.mark.django_db

TUESDAY = date(2027, 1, 12)


@pytest.fixture
def center():
    return CenterFactory(
        time_zone="America/New_York", name="Write Place", contact_email="help@example.edu"
    )


@pytest.fixture
def assignment(center):
    schedule = make_schedule(
        center,
        date(2027, 1, 4),
        date(2027, 2, 28),
        late_cancel_minutes=120,
        change_cutoff_minutes=60,
    )
    tutor = make_member(center, Role.TUTOR, full_name="Tomás Tutor")
    return assign(schedule, tutor, hours=((time(0), time(23, 30)),), capacity=4)


@pytest.fixture
def student(center):
    return make_member(center, Role.STUDENT, full_name="Sam Private-Name")


def booked(assignment, students, start: datetime) -> Appointment:
    a = Appointment.objects.create(
        schedule=assignment.schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        blocked_until=start + timedelta(minutes=30),
        modality=Modality.IN_PERSON,
        capacity=max(1, len(students)),
    )
    for s in students:
        Participant.objects.create(appointment=a, student=s)
    return a


def cancel(appointment, actor, *, minutes_before: int, as_staff=False, **kwargs):
    return services.cancel(
        actor=actor,
        appointment=appointment,
        as_staff=as_staff,
        now=appointment.starts_at - timedelta(minutes=minutes_before),
        **kwargs,
    )


def by_recipient() -> dict:
    return {m.to[0]: m for m in mail.outbox}


def test_canceling_before_the_window_is_not_late(
    assignment, student, django_capture_on_commit_callbacks
) -> None:
    a = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    with django_capture_on_commit_callbacks(execute=True):
        cancel(a, student, minutes_before=121)
    assert not participant(a, student).late_cancel
    message = by_recipient()[student.email]
    assert "late" not in message.body


def test_canceling_inside_the_window_is_late(
    assignment, student, django_capture_on_commit_callbacks
) -> None:
    a = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    with django_capture_on_commit_callbacks(execute=True):
        cancel(a, student, minutes_before=90)
    assert participant(a, student).late_cancel
    event = AuditEvent.objects.get(action=AuditAction.APPOINTMENT_CANCELED)
    assert event.details["late"] is True

    messages = by_recipient()
    assert set(messages) == {student.email, assignment.tutor.email}
    to_student = messages[student.email]
    assert "Appointment canceled" in to_student.subject
    assert (
        "It was canceled less than 2 hours before it started, so the center has recorded it "
        "as a late cancellation." in to_student.body
    )
    assert "The center can waive it." in to_student.body
    assert "METHOD:CANCEL" in to_student.attachments[0][1]
    assert "—" not in to_student.body
    # The tutor's email is the usual one: no word about lateness, and no name.
    to_tutor = messages[assignment.tutor.email]
    assert "late" not in to_tutor.body
    assert "Sam" not in to_tutor.body


def test_without_a_window_nothing_is_late(assignment, student) -> None:
    schedule = assignment.schedule
    schedule.late_cancel_minutes = 0
    schedule.save()
    a = booked(assignment, [student], at(schedule, TUESDAY, 10))
    cancel(a, student, minutes_before=61, notify=False)
    assert not participant(a, student).late_cancel


def test_staff_record_a_late_cancellation_only_when_the_student_asked(
    assignment, student, center
) -> None:
    desk = make_member(center, Role.FRONT_DESK)
    schedule = assignment.schedule
    for_the_center = booked(assignment, [student], at(schedule, TUESDAY, 10))
    cancel(for_the_center, desk, minutes_before=30, as_staff=True, notify=False)
    assert not participant(for_the_center, student).late_cancel
    asked = booked(assignment, [student], at(schedule, TUESDAY, 11))
    cancel(asked, desk, minutes_before=30, as_staff=True, student_asked=True, notify=False)
    assert participant(asked, student).late_cancel
    # Outside the window, asking makes no difference.
    early = booked(assignment, [student], at(schedule, TUESDAY, 12))
    cancel(early, desk, minutes_before=180, as_staff=True, student_asked=True, notify=False)
    assert not participant(early, student).late_cancel


def test_staff_cannot_record_one_late_cancellation_for_a_whole_group(
    assignment, student, center
) -> None:
    other = make_member(center, Role.STUDENT)
    a = booked(assignment, [student, other], at(assignment.schedule, TUESDAY, 10))
    with pytest.raises(services.BookingError) as refused:
        cancel(
            a,
            make_member(center, Role.FRONT_DESK),
            minutes_before=30,
            as_staff=True,
            student_asked=True,
        )
    assert refused.value.code == "late_group"
    assert not Participant.objects.filter(appointment=a, canceled_at__isnull=False).exists()


def test_a_student_who_leaves_a_group_late_is_the_only_one_marked(
    assignment, student, center, django_capture_on_commit_callbacks
) -> None:
    other = make_member(center, Role.STUDENT, full_name="Lee Other-Student")
    a = booked(assignment, [student, other], at(assignment.schedule, TUESDAY, 10))
    with django_capture_on_commit_callbacks(execute=True):
        cancel(a, student, minutes_before=90)
    assert participant(a, student).late_cancel
    assert participant(a, other).canceled_at is None
    a.refresh_from_db()
    assert a.is_active
    # Only the student who left hears about it; the group goes on.
    assert [m.to for m in mail.outbox] == [[student.email]]
    assert "Lee" not in mail.outbox[0].body


def test_the_email_gives_the_running_count_when_there_is_a_limit(
    assignment, student, center, django_capture_on_commit_callbacks
) -> None:
    center.late_cancel_block_limit = 3
    center.save()
    a = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    with django_capture_on_commit_callbacks(execute=True):
        cancel(a, student, minutes_before=90)
    body = by_recipient()[student.email].body
    assert (
        "That makes 1 late cancellation this term. Once there are 3, online booking is paused "
        "until the term ends." in body
    )


def test_a_waived_late_cancellation_stays_on_the_record(assignment, student, center) -> None:
    a = booked(assignment, [student], at(assignment.schedule, TUESDAY, 10))
    cancel(a, student, minutes_before=90, notify=False)
    director = make_member(center, Role.DIRECTOR)
    attendance.waive(actor=director, appointment=a, student_id=student.pk, reason="Snow day.")
    p = participant(a, student)
    assert p.late_cancel and p.waived_at is not None
    event = AuditEvent.objects.get(action=AuditAction.ATTENDANCE_WAIVED)
    assert event.details["what"] == "late_cancel"


# --- The cancel page ---------------------------------------------------------------


def soon(assignment, students, minutes: int) -> Appointment:
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=minutes)
    return booked(assignment, students, start)


def cancel_url(appointment) -> str:
    return reverse(
        "scheduling:appointment_cancel",
        kwargs={"slug": appointment.schedule.center.slug, "appointment_id": appointment.pk},
    )


def test_students_are_warned_before_a_late_cancellation(client, assignment, student) -> None:
    client.force_login(student)
    later = soon(assignment, [student], 6 * 60)
    assert "late cancellation" not in client.get(cancel_url(later)).content.decode()
    inside = soon(assignment, [student], 90)
    text = " ".join(client.get(cancel_url(inside)).content.decode().split())
    assert (
        "The appointment starts in less than 2 hours, so canceling now is recorded as a late "
        "cancellation." in text
    )
    response = client.post(cancel_url(inside))
    assert response.status_code == 302
    assert participant(inside, student).late_cancel


def test_staff_say_whether_the_student_asked(client, assignment, student, center) -> None:
    client.force_login(make_member(center, Role.FRONT_DESK))
    outside = soon(assignment, [student], 6 * 60)
    assert "student_asked" not in client.get(cancel_url(outside)).content.decode()
    inside = soon(assignment, [student], 90)
    page = client.get(cancel_url(inside)).content.decode()
    assert "The student asked to cancel" in page
    client.post(cancel_url(inside), {"student_asked": "on"})
    assert participant(inside, student).late_cancel
    unasked = soon(assignment, [student], 100)
    client.post(cancel_url(unasked))
    p = participant(unasked, student)
    assert p.canceled_at is not None and not p.late_cancel
