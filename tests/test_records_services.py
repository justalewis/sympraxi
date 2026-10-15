"""Writing, finalizing and amending session records, and the database lock on final ones."""

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import services
from sympraxi.records.models import (
    Amendment,
    AttachedResource,
    RecordStatus,
    RecordTopic,
    SessionRecord,
    WritingProject,
)
from tests.records_world import appointment_for, make_resource, topics, write_record
from tests.scheduling_world import World, build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def content(**fields) -> services.Content:
    values = {
        "minutes": 30,
        "summary": "We worked on your thesis.",
        "next_steps": "Rewrite the topic sentences.",
        "staff_notes": "Ready to move on to evidence.",
        "topics": topics("argument:thesis", "revising"),
    }
    values.update(fields)
    return services.Content(**values)


# --- Drafts and finalizing ------------------------------------------------------


def test_a_draft_can_be_saved_again_and_again(world) -> None:
    appointment = appointment_for(world, world.student)
    rec = services.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=world.student
    )
    assert rec.status == RecordStatus.DRAFT
    assert rec.tutor == world.tutor and rec.held_at == appointment.starts_at
    services.save_draft(actor=world.tutor, rec=rec, content=content(summary="First try."))
    services.save_draft(actor=world.tutor, rec=rec, content=content(summary="Second try."))
    rec.refresh_from_db()
    assert rec.summary == "Second try."
    assert {t.slug for t in rec.topics.all()} == {"argument:thesis", "revising"}
    # Starting again returns the same draft rather than a second record.
    again = services.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=world.student
    )
    assert again.pk == rec.pk


def test_records_only_for_students_in_the_appointment(world) -> None:
    appointment = appointment_for(world, world.student)
    stranger = make_member(world.center, Role.STUDENT)
    with pytest.raises(services.RecordError) as refused:
        services.start_for_appointment(actor=world.tutor, appointment=appointment, student=stranger)
    assert refused.value.code == "not_in_it"


def test_finalizing_needs_a_summary_a_concern_and_a_session_that_happened(world) -> None:
    upcoming = appointment_for(world, world.student, days=2)
    rec = services.start_for_appointment(
        actor=world.tutor, appointment=upcoming, student=world.student
    )
    with pytest.raises(services.RecordError) as refused:
        services.finalize(
            actor=world.tutor,
            rec=rec,
            content=content(summary=" ", topics=topics("revising")),
            notify=False,
        )
    message = refused.value.message
    assert "summary" in message and "concern" in message and "hasn't started" in message
    rec.refresh_from_db()
    assert rec.status == RecordStatus.DRAFT


def test_finalizing_records_who_and_when(world) -> None:
    appointment = appointment_for(world, world.student)
    rec = write_record(appointment, world.student)
    assert rec.status == RecordStatus.FINAL
    assert rec.finalized_by == world.tutor
    assert rec.finalized_at is not None
    events = AuditEvent.objects.filter(target_id=str(rec.pk)).values_list("action", flat=True)
    assert AuditAction.RECORD_FINALIZED in events
    with pytest.raises(services.RecordError):
        services.finalize(actor=world.tutor, rec=rec, notify=False)


def test_a_project_must_belong_to_the_student(world) -> None:
    appointment = appointment_for(world, world.student)
    rec = services.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=world.student
    )
    someone_else = make_member(world.center, Role.STUDENT)
    theirs = WritingProject.objects.create(center=world.center, student=someone_else, title="X")
    with pytest.raises(services.RecordError):
        services.save_draft(actor=world.tutor, rec=rec, content=content(project=theirs))


# --- The database lock on final records -------------------------------------------


@pytest.fixture
def final(world):
    resource = make_resource("argument:thesis")
    rec = write_record(
        appointment_for(world, world.student),
        world.student,
        staff_notes="Original notes.",
        resources=[(resource, "Original note.")],
    )
    return rec


def refused(fn) -> None:
    with pytest.raises(DatabaseError, match="final session record"), transaction.atomic():
        fn()


def test_a_final_record_cannot_be_changed_silently(final) -> None:
    refused(lambda: SessionRecord.objects.filter(pk=final.pk).update(summary="Quietly changed."))
    final.staff_notes = "Rewritten."
    refused(final.save)
    refused(lambda: SessionRecord.objects.filter(pk=final.pk).update(status="draft"))
    refused(lambda: final.topics.add(*topics("citation:apa")))
    refused(lambda: RecordTopic.objects.filter(record=final).delete())
    refused(lambda: AttachedResource.objects.filter(record=final).update(note="Changed."))
    refused(
        lambda: AttachedResource.objects.create(
            record=final, resource=make_resource("citation"), added_by=final.tutor
        )
    )
    refused(lambda: SessionRecord.objects.filter(pk=final.pk).delete())
    final.refresh_from_db()
    assert final.summary == "We worked on your thesis."
    assert final.staff_notes == "Original notes."


def test_an_amendment_changes_the_record_and_keeps_the_old_version(world, final) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    added = make_resource("citation:apa", title="APA basics")
    old_resource = final.attachments.get().resource
    amendment = services.amend(
        actor=director,
        rec=final,
        content=content(
            summary="We worked on your thesis and your APA citations.",
            staff_notes="Original notes.",
            topics=topics("argument:thesis", "citation:apa", "revising"),
            resources=[(old_resource, "Original note."), (added, "Page numbers.")],
        ),
        reason="Forgot to mention the citation work.",
    )
    final.refresh_from_db()
    assert final.summary == "We worked on your thesis and your APA citations."
    assert final.status == RecordStatus.FINAL
    assert {t.slug for t in final.topics.all()} == {"argument:thesis", "citation:apa", "revising"}
    assert final.attachments.count() == 2

    assert amendment.author == director
    assert amendment.reason == "Forgot to mention the citation work."
    assert set(amendment.changes) == {"summary", "next_steps", "topics", "resources"}
    assert amendment.changes["summary"]["before"] == "We worked on your thesis."
    assert "citation:apa" in amendment.changes["topics"]["after"]
    assert "citation:apa" not in amendment.changes["topics"]["before"]
    assert [r["title"] for r in amendment.changes["resources"]["after"]][-1] == "APA basics"

    event = AuditEvent.objects.get(action=AuditAction.RECORD_AMENDED, target_id=str(final.pk))
    assert event.details["fields"] == ["next_steps", "resources", "summary", "topics"]
    # The audit log says what changed, never the text.
    assert "APA citations" not in str(event.details)


def test_an_amendment_needs_a_reason_and_a_change(world, final) -> None:
    same = services.Content.of(final)
    with pytest.raises(services.RecordError) as no_reason:
        services.amend(actor=world.tutor, rec=final, content=content(summary="New."), reason=" ")
    assert no_reason.value.code == "no_reason"
    with pytest.raises(services.RecordError) as unchanged:
        services.amend(actor=world.tutor, rec=final, content=same, reason="Checking.")
    assert unchanged.value.code == "unchanged"
    with pytest.raises(services.RecordError):
        services.amend(actor=world.tutor, rec=final, content=content(summary=""), reason="Oops.")
    assert not Amendment.objects.exists()


def test_the_amendment_opens_only_its_own_change(world, final) -> None:
    """Once amend() returns, the record is locked again, even in the same transaction."""
    with transaction.atomic():
        services.amend(
            actor=world.tutor, rec=final, content=content(summary="Amended."), reason="Typo."
        )
        refused(lambda: SessionRecord.objects.filter(pk=final.pk).update(summary="Sneaky."))
    final.refresh_from_db()
    assert final.summary == "Amended."


def test_an_amendment_cannot_change_who_the_record_is_about(world, final) -> None:
    amendment = Amendment.objects.create(
        record=final, author=world.tutor, reason="Test.", changes={"x": {}}
    )
    other = make_member(world.center, Role.STUDENT)

    def move() -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('sympraxi.amendment', %s, true)", [str(amendment.pk)])
        SessionRecord.objects.filter(pk=final.pk).update(student=other)

    refused(move)


def test_amendments_are_append_only(world, final) -> None:
    amendment = services.amend(
        actor=world.tutor, rec=final, content=content(summary="Amended."), reason="Typo."
    )
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        Amendment.objects.filter(pk=amendment.pk).update(reason="Different reason.")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        Amendment.objects.filter(pk=amendment.pk).delete()


def test_drafts_stay_editable_and_deletable(world) -> None:
    rec = write_record(appointment_for(world, world.student), world.student, final=False)
    SessionRecord.objects.filter(pk=rec.pk).update(summary="Changed in place.")
    rec.topics.add(*topics("citation"))
    rec.delete()
    assert not SessionRecord.objects.filter(pk=rec.pk).exists()


# --- Group appointments -------------------------------------------------------------


def test_group_copies_are_separate_drafts_without_notes_or_project(world) -> None:
    others = [make_member(world.center, Role.STUDENT) for _ in range(2)]
    group = appointment_for(world, [world.student, *others], capacity=4)
    project = WritingProject.objects.create(center=world.center, student=world.student, title="P")
    resource = make_resource("argument:thesis")
    rec = write_record(
        group,
        world.student,
        final=False,
        staff_notes="Only about the first student.",
        project=project,
        resources=[(resource, "Read this.")],
    )
    # One student already has a record of their own; it isn't overwritten.
    write_record(group, others[1], final=False, summary="Written separately.")

    copies = services.copy_to_group(actor=world.tutor, rec=rec)
    assert [c.student for c in copies] == [others[0]]
    copy = copies[0]
    assert copy.status == RecordStatus.DRAFT
    assert copy.summary == rec.summary and copy.next_steps == rec.next_steps
    assert copy.staff_notes == "" and copy.project is None
    assert copy.copied_from == rec
    assert {t.slug for t in copy.topics.all()} == {t.slug for t in rec.topics.all()}
    assert [(a.resource, a.note) for a in copy.attachments.all()] == [(resource, "Read this.")]
    assert SessionRecord.objects.get(appointment=group, student=others[1]).summary == (
        "Written separately."
    )


def test_off_schedule_records(world) -> None:
    held = timezone.now() - timedelta(hours=2)
    rec = services.start_standalone(
        actor=world.tutor, center=world.center, student=world.student, held_at=held, minutes=15
    )
    assert rec.appointment is None and rec.tutor == world.tutor
    services.finalize(actor=world.tutor, rec=rec, content=content(minutes=15), notify=False)
    rec.refresh_from_db()
    assert rec.is_final and rec.minutes == 15
