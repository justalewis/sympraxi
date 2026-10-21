"""FERPA rules for session records: audited reads, the student export, and isolation."""

import json

import pytest
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import services
from sympraxi.records.models import AttachedResource, SessionRecord, WritingProject
from sympraxi.scheduling.models import AppointmentStatus, Participant
from tests.records_world import (
    appointment_for,
    make_resource,
    make_topics,
    membership,
    standalone,
    topics,
    write_record,
)
from tests.scheduling_world import World, build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def vocabulary() -> None:
    make_topics()


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def url(name: str, world: World, **kwargs: object) -> str:
    return reverse(name, kwargs={"slug": world.center.slug, **kwargs})


def events(action: AuditAction):
    return AuditEvent.objects.filter(action=action)


# --- Every staff read is audited ----------------------------------------------------


def test_every_staff_read_of_a_record_is_audited(client, world) -> None:
    rec = write_record(
        appointment_for(world, world.student), world.student, staff_notes="PRIVATE NOTE TEXT"
    )
    director = make_member(world.center, Role.DIRECTOR)
    student_page = membership(world.center, world.student)

    client.force_login(world.tutor)
    client.get(url("records:detail", world, record_id=rec.pk))
    client.get(url("records:amend", world, record_id=rec.pk))
    client.force_login(director)
    client.get(url("records:detail", world, record_id=rec.pk))
    client.get(url("records:student_history", world, membership_id=student_page.pk))

    reads = list(events(AuditAction.RECORD_VIEWED).order_by("occurred_at", "id"))
    assert [(e.actor_id, e.details["where"]) for e in reads] == [
        (world.tutor.pk, "record page"),
        (world.tutor.pk, "record form"),
        (director.pk, "record page"),
    ]
    for event in reads:
        assert event.target_type == "records.sessionrecord"
        assert event.target_id == str(rec.pk)
        assert event.center_id == world.center.pk
        assert event.details["student"] == str(world.student.pk)
        assert event.ip_address == "127.0.0.1"

    [history] = events(AuditAction.HISTORY_VIEWED)
    assert history.actor_id == director.pk
    assert history.target_id == str(student_page.pk)
    assert history.details["records"] == [str(rec.pk)]

    # The log says who read what, never what it says.
    for event in AuditEvent.objects.all():
        assert "PRIVATE NOTE TEXT" not in json.dumps(event.details)


def test_a_draft_form_read_is_audited_but_a_save_is_logged_as_a_save(client, world) -> None:
    rec = write_record(appointment_for(world, world.student), world.student, final=False)
    client.force_login(world.tutor)
    client.get(url("records:edit", world, record_id=rec.pk))
    assert events(AuditAction.RECORD_VIEWED).count() == 1
    client.post(
        url("records:edit", world, record_id=rec.pk),
        {"summary": "New", "minutes": "30", "topics": ["argument:thesis"], "action": "save"},
    )
    assert events(AuditAction.RECORD_VIEWED).count() == 1
    assert events(AuditAction.RECORD_SAVED).filter(target_id=str(rec.pk)).count() == 2


def test_students_reading_their_own_records_are_not_staff_reads(client, world) -> None:
    rec = write_record(appointment_for(world, world.student), world.student)
    client.force_login(world.student)
    client.get(url("records:my_writing", world))
    client.get(url("records:my_record", world, record_id=rec.pk))
    assert not events(AuditAction.RECORD_VIEWED).exists()
    assert not events(AuditAction.HISTORY_VIEWED).exists()


def test_tutors_read_only_students_they_have_appointments_with(client, world) -> None:
    stranger_tutor = make_member(world.center, Role.TUTOR)
    rec = write_record(appointment_for(world, world.student), world.student)
    history = url(
        "records:student_history", world, membership_id=membership(world.center, world.student).pk
    )
    client.force_login(stranger_tutor)
    assert client.get(url("records:detail", world, record_id=rec.pk)).status_code == 404
    assert client.get(history).status_code == 404
    assert not events(AuditAction.RECORD_VIEWED).exists()

    # An upcoming appointment is enough: that's what the brief is for.
    upcoming = appointment_for(world, world.student, days=3, tutor=stranger_tutor)
    assert client.get(history).status_code == 200
    # Canceled, it isn't.
    upcoming.status = AppointmentStatus.CANCELED
    upcoming.save()
    assert client.get(history).status_code == 404


# --- The FERPA export -----------------------------------------------------------------


@pytest.fixture
def full_history(world):
    """A student with every kind of record, plus a group mate whose records stay out."""
    student = world.student
    mate = make_member(world.center, Role.STUDENT, full_name="Gemma Groupmate")
    project = WritingProject.objects.create(
        center=world.center, student=student, title="Capstone", course="ENG 300"
    )
    resource = make_resource("argument:thesis", title="Thesis tips")
    one_to_one = appointment_for(world, student, days=-6)
    amended = write_record(
        one_to_one,
        student,
        summary="First summary.",
        staff_notes="Staff only: first visit.",
        project=project,
        resources=[(resource, "Read the examples.")],
    )
    services.amend(
        actor=world.tutor,
        rec=amended,
        content=services.Content(
            minutes=30,
            summary="Corrected summary.",
            next_steps="Revise the introduction.",
            staff_notes="Staff only: first visit.",
            topics=topics("argument:thesis", "revising"),
            project=project,
            resources=[(resource, "Read the examples.")],
        ),
        reason="Fixed a wrong detail.",
    )
    group = appointment_for(world, [student, mate], days=-4, capacity=4)
    group_record = write_record(group, student, summary="Group summary for the student.")
    mate_record = write_record(
        group, mate, summary="MATE SUMMARY", staff_notes="MATE NOTES", next_steps="MATE STEPS"
    )
    draft = write_record(
        appointment_for(world, student, days=-2), student, final=False, summary="A draft."
    )
    canceled = appointment_for(world, student, days=-3, status=AppointmentStatus.CANCELED)
    off_schedule = standalone(world.center, student, world.tutor, final=True, days=-5)
    left = appointment_for(world, [student, mate], days=-7, capacity=4)
    Participant.objects.filter(appointment=left, student=student).update(canceled_at=left.starts_at)
    return {
        "student": student,
        "mate": mate,
        "project": project,
        "records": [amended, group_record, draft, off_schedule],
        "mate_record": mate_record,
        "appointments": [one_to_one, group, canceled, left, draft.appointment, world.appointment],
    }


def test_the_json_export_holds_everything_about_the_student(client, world, full_history) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    student_membership = membership(world.center, full_history["student"])
    response = client.get(url("records:export_json", world, membership_id=student_membership.pk))
    assert response.status_code == 200
    assert response["Content-Disposition"] == 'attachment; filename="student-record.json"'
    data = json.loads(response.content)

    assert data["format"] == "sympraxi.student-record" and data["version"] == 10
    assert data["disclosures"] == []
    assert data["student"]["email"] == full_history["student"].email
    assert data["exported_by"]["email"] == director.email
    assert [p["title"] for p in data["writing_projects"]] == ["Capstone"]

    assert {a["id"] for a in data["appointments"]} == {
        str(a.pk) for a in full_history["appointments"]
    }
    by_id = {a["id"]: a for a in data["appointments"]}
    group = full_history["appointments"][1]
    assert by_id[str(group.pk)]["group_size"] == 2
    canceled = full_history["appointments"][2]
    assert by_id[str(canceled.pk)]["status"] == "canceled"
    left = full_history["appointments"][3]
    assert by_id[str(left.pk)]["left_at"] is not None

    records = {r["id"]: r for r in data["session_records"]}
    assert set(records) == {str(r.pk) for r in full_history["records"]}
    amended = records[str(full_history["records"][0].pk)]
    assert amended["summary"] == "Corrected summary."
    assert amended["staff_notes"] == "Staff only: first visit."
    assert amended["writing_project"]["title"] == "Capstone"
    assert [t["slug"] for t in amended["topics"]] == ["argument:thesis", "revising"]
    [attached] = amended["resources"]
    assert attached["title"] == "Thesis tips" and attached["note"] == "Read the examples."
    assert attached["license"] == "CC-BY-4.0" and attached["url"].startswith("https://")
    [amendment] = amended["amendments"]
    assert amendment["reason"] == "Fixed a wrong detail."
    assert amendment["by"]["email"] == world.tutor.email
    assert amendment["changes"]["summary"] == {
        "before": "First summary.",
        "after": "Corrected summary.",
    }
    assert records[str(full_history["records"][2].pk)]["status"] == "draft"
    assert records[str(full_history["records"][3].pk)]["appointment_id"] is None

    # Every record and attachment the database holds for this student is in it.
    assert SessionRecord.objects.filter(student=full_history["student"]).count() == len(records)
    assert AttachedResource.objects.filter(record__student=full_history["student"]).count() == sum(
        len(r["resources"]) for r in records.values()
    )

    # The group mate is nowhere in it.
    body = response.content.decode()
    mate = full_history["mate"]
    for text in (mate.full_name, mate.email, str(mate.pk), "MATE SUMMARY", "MATE NOTES"):
        assert text not in body
    assert str(full_history["mate_record"].pk) not in body

    [event] = AuditEvent.objects.filter(action=AuditAction.STUDENT_RECORD_EXPORTED)
    assert event.actor_id == director.pk
    assert event.details == {"student": str(full_history["student"].pk), "format": "json"}


def test_the_printable_export_matches(client, world, full_history) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    student_membership = membership(world.center, full_history["student"])
    response = client.get(url("records:export", world, membership_id=student_membership.pk))
    text = " ".join(response.content.decode().split())
    for expected in (
        full_history["student"].email,
        "Capstone",
        "Corrected summary.",
        "First summary.",  # the version before the amendment
        "Fixed a wrong detail.",
        "Staff only: first visit.",
        "Read the examples.",
        "A draft.",
        "Group summary for the student.",
        "group of 2",
        "Left the group",
        "Canceled",
    ):
        assert expected in text, expected
    for hidden in ("Gemma Groupmate", "MATE SUMMARY", "MATE NOTES"):
        assert hidden not in text
    assert chr(0x2014) not in text  # no em dashes
    assert AuditEvent.objects.get(action=AuditAction.STUDENT_RECORD_EXPORTED).details["format"] == (
        "page"
    )


def test_only_directors_export(client, world) -> None:
    student_membership = membership(world.center, world.student)
    export = url("records:export_json", world, membership_id=student_membership.pk)
    client.force_login(world.tutor)
    assert client.get(export).status_code == 403
    client.force_login(world.student)
    assert client.get(export).status_code == 403
    assert not AuditEvent.objects.filter(action=AuditAction.STUDENT_RECORD_EXPORTED).exists()


# --- Students never see each other ----------------------------------------------------


def test_group_members_see_only_their_own_records(client, world) -> None:
    mate = make_member(world.center, Role.STUDENT, full_name="Gemma Groupmate")
    group = appointment_for(world, [world.student, mate], capacity=4)
    mine = write_record(group, world.student, summary="MY SUMMARY", next_steps="MY STEPS")
    theirs = write_record(
        group, mate, summary="MATE SUMMARY", next_steps="MATE STEPS", staff_notes="MATE NOTES"
    )
    world.center.students_see_staff_notes = True
    world.center.save()

    client.force_login(world.student)
    my_writing = client.get(url("records:my_writing", world)).content.decode()
    assert "MY SUMMARY" in my_writing
    for text in ("MATE SUMMARY", "MATE STEPS", "MATE NOTES", "Gemma Groupmate"):
        assert text not in my_writing
    assert client.get(url("records:my_record", world, record_id=theirs.pk)).status_code == 404
    assert client.get(url("records:my_record", world, record_id=mine.pk)).status_code == 200

    appointment_page = client.get(
        url("scheduling:appointment", world, appointment_id=group.pk)
    ).content.decode()
    assert url("records:my_record", world, record_id=mine.pk) in appointment_page
    assert str(theirs.pk) not in appointment_page
    assert "Gemma Groupmate" not in appointment_page

    # The tutor sees both, each separately.
    client.force_login(world.tutor)
    page = client.get(
        url("scheduling:appointment", world, appointment_id=group.pk)
    ).content.decode()
    assert url("records:detail", world, record_id=mine.pk) in page
    assert url("records:detail", world, record_id=theirs.pk) in page


def test_a_student_who_left_a_group_has_no_record_started_for_them(client, world) -> None:
    mate = make_member(world.center, Role.STUDENT)
    group = appointment_for(world, [world.student, mate], capacity=4)
    Participant.objects.filter(appointment=group, student=mate).update(canceled_at=group.starts_at)
    rec = write_record(group, world.student, final=False)
    assert services.copy_to_group(actor=world.tutor, rec=rec) == []
    client.force_login(world.tutor)
    response = client.get(
        url("records:start", world, appointment_id=group.pk), {"student": str(mate.pk)}
    )
    assert response.status_code == 404
