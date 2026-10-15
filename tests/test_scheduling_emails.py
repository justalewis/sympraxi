from datetime import UTC, date, datetime, time
from email.message import MIMEPart

import pytest
from django.core import mail
from django.core.mail import EmailMessage, mailers
from django.db import transaction
from django.test import override_settings

from sympraxi.centers.models import Role
from sympraxi.scheduling import ics, services
from sympraxi.scheduling.models import Modality
from tests.factories import CenterFactory
from tests.scheduling_world import assign, at, make_member, make_schedule

QUEUED = override_settings(
    MAILERS={
        "default": {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"},
        "delivery": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    }
)
TUESDAY = date(2027, 1, 12)
NOW = datetime(2027, 1, 11, 13, tzinfo=UTC)


@QUEUED
def test_queued_email_keeps_text_and_binary_attachments() -> None:
    message = EmailMessage("Hello", "Body", "wc@example.edu", ["s@example.edu"])
    calendar = "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    message.attach("appointment.ics", calendar, "text/calendar")
    message.attach("data.bin", b"\x00\x01\xff", "application/octet-stream")
    assert mailers.default.send_messages([message]) == 1
    [sent] = mail.outbox
    assert sent.attachments[0] == ("appointment.ics", calendar, "text/calendar")
    assert sent.attachments[1] == ("data.bin", b"\x00\x01\xff", "application/octet-stream")


@QUEUED
def test_queued_email_refuses_hand_built_mime_parts() -> None:
    message = EmailMessage("Hello", "Body", "wc@example.edu", ["s@example.edu"])
    message.attach(MIMEPart())
    with pytest.raises(ValueError, match="only supports"):
        mailers.default.send_messages([message])


def test_calendar_text_is_escaped_and_folded() -> None:
    assert ics.escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    line = "SUMMARY:" + "Écrire " * 30
    folded = ics.fold(line)
    pieces = folded.split("\r\n")
    assert all(len(p.encode()) <= 75 for p in pieces)
    assert all(p.startswith(" ") for p in pieces[1:])
    assert "".join(p[1:] if i else p for i, p in enumerate(pieces)) == line


@pytest.fixture
def booked(db):
    center = CenterFactory(
        time_zone="America/New_York", name="Write Place", contact_email="help@example.edu"
    )
    schedule = make_schedule(center, date(2027, 1, 4), date(2027, 2, 28))
    tutor = make_member(center, Role.TUTOR, full_name="Tomás Tutor")
    assignment = assign(schedule, tutor, hours=((time(9), time(17)),))
    student = make_member(center, Role.STUDENT, full_name="Sam Private-Name")
    return assignment, student


def book(assignment, student):
    return services.book(
        actor=student,
        assignment=assignment,
        starts_at=at(assignment.schedule, TUESDAY, 10),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[student],
        as_staff=False,
        now=NOW,
    )


@QUEUED
def test_booking_emails_the_student_and_the_tutor(booked, django_capture_on_commit_callbacks):
    assignment, student = booked
    with django_capture_on_commit_callbacks(execute=True):
        appointment = book(assignment, student)
    by_recipient = {m.to[0]: m for m in mail.outbox}
    assert set(by_recipient) == {student.email, assignment.tutor.email}

    to_student = by_recipient[student.email]
    assert "Appointment booked" in to_student.subject
    assert "Tomás Tutor" in to_student.body
    assert "10 a.m." in to_student.body and "EST" in to_student.body
    assert to_student.reply_to == ["help@example.edu"]
    [(name, calendar, mimetype)] = to_student.attachments
    assert (name, mimetype) == ("appointment.ics", "text/calendar")
    assert "METHOD:PUBLISH" in calendar
    assert f"UID:{appointment.pk}@" in calendar
    assert "DTSTART:20270112T150000Z" in calendar
    assert "STATUS:CONFIRMED" in calendar

    # The tutor is told a student booked, not who: they sign in to see that.
    to_tutor = by_recipient[assignment.tutor.email]
    assert "New appointment" in to_tutor.subject
    for text in (to_tutor.subject, to_tutor.body, to_tutor.attachments[0][1]):
        assert "Sam" not in text and student.email not in text


@QUEUED
def test_cancellation_sends_a_cancel_calendar_file(booked, django_capture_on_commit_callbacks):
    assignment, student = booked
    with django_capture_on_commit_callbacks(execute=True):
        appointment = book(assignment, student)
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        services.cancel(actor=student, appointment=appointment, as_staff=False, now=NOW)
    assert {m.to[0] for m in mail.outbox} == {student.email, assignment.tutor.email}
    for message in mail.outbox:
        assert "canceled" in message.subject
        calendar = message.attachments[0][1]
        assert "METHOD:CANCEL" in calendar
        assert "STATUS:CANCELLED" in calendar
        assert "SEQUENCE:1" in calendar
        assert "ORGANIZER" in calendar


@QUEUED
def test_tentative_bookings_say_so(booked, django_capture_on_commit_callbacks):
    assignment, student = booked
    assignment.needs_confirmation = True
    assignment.save()
    with django_capture_on_commit_callbacks(execute=True):
        book(assignment, student)
    to_student = next(m for m in mail.outbox if m.to == [student.email])
    assert "requested" in to_student.subject
    assert "STATUS:TENTATIVE" in to_student.attachments[0][1]


@QUEUED
def test_a_rolled_back_booking_sends_nothing(booked, django_capture_on_commit_callbacks):
    assignment, student = booked
    with (
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        book(assignment, student)
        raise RuntimeError("something later in the request failed")
    assert mail.outbox == []
