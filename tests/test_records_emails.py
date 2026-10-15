"""The email a student gets when their session summary is ready."""

import pytest
from django.core import mail
from django.db import transaction
from django.test import override_settings
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.records import services
from tests.records_world import appointment_for, topics, write_record
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

QUEUED = override_settings(
    MAILERS={
        "default": {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"},
        "delivery": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    },
    SYMPRAXI_BASE_URL="https://writing.example.edu",
)

SUMMARY = "You found your thesis in the last paragraph and moved it up."
NEXT_STEPS = "Rewrite each topic sentence to point back to it."
NOTES = "Seemed stressed about the deadline."


@pytest.fixture
def world(center):
    center.name = "Write Place"
    center.contact_email = "help@example.edu"
    center.save()
    return build_world(center)


def finalize(world, student=None, appointment=None):
    student = student or world.student
    appointment = appointment or appointment_for(world, student)
    rec = services.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=student
    )
    return services.finalize(
        actor=world.tutor,
        rec=rec,
        content=services.Content(
            minutes=30,
            summary=SUMMARY,
            next_steps=NEXT_STEPS,
            staff_notes=NOTES,
            topics=topics("argument:thesis"),
        ),
    )


@QUEUED
def test_the_email_says_the_summary_is_ready_and_links_to_it(
    world, django_capture_on_commit_callbacks
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        rec = finalize(world)
    [message] = mail.outbox
    assert message.to == [world.student.email]
    assert message.subject == "[Sympraxi] Your session summary is ready"
    assert message.reply_to == ["help@example.edu"]
    link = "https://writing.example.edu" + reverse(
        "records:my_record", kwargs={"slug": world.center.slug, "record_id": rec.pk}
    )
    assert link in message.body
    assert world.tutor.display_name in message.body
    assert "Write Place" in message.body
    # By default the email carries no session content at all.
    for text in (SUMMARY, NEXT_STEPS, NOTES):
        assert text not in message.body
    assert chr(0x2014) not in message.body  # no em dashes


@QUEUED
def test_a_center_can_put_the_summary_in_the_email(
    world, django_capture_on_commit_callbacks
) -> None:
    world.center.summary_email_includes_text = True
    # Staff notes never go in an email, even where students may read them online.
    world.center.students_see_staff_notes = True
    world.center.save()
    with django_capture_on_commit_callbacks(execute=True):
        finalize(world)
    [message] = mail.outbox
    assert SUMMARY in message.body
    assert NEXT_STEPS in message.body
    assert NOTES not in message.body


@QUEUED
def test_drafts_send_nothing(world, django_capture_on_commit_callbacks) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        write_record(appointment_for(world, world.student), world.student, final=False)
    assert mail.outbox == []


@QUEUED
def test_a_rolled_back_finalize_sends_nothing(world, django_capture_on_commit_callbacks) -> None:
    with (
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        finalize(world)
        raise RuntimeError("something later in the request failed")
    assert mail.outbox == []


@QUEUED
def test_in_a_group_each_student_gets_only_their_own_email(
    world, django_capture_on_commit_callbacks
) -> None:
    other = make_member(world.center, Role.STUDENT)
    group = appointment_for(world, [world.student, other], capacity=3)
    with django_capture_on_commit_callbacks(execute=True):
        mine = finalize(world, world.student, group)
    [message] = mail.outbox
    assert message.to == [world.student.email]
    assert other.email not in message.body and other.display_name not in message.body
    assert str(mine.pk) in message.body
