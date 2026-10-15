"""The session-record pages, driven the way a browser would."""

from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records.models import RecordStatus, Resource, SessionRecord, WritingProject
from tests.records_world import (
    appointment_for,
    make_resource,
    make_topics,
    membership,
    topics,
    write_record,
)
from tests.scheduling_world import World, build_world, make_member

pytestmark = pytest.mark.django_db

QUEUED = override_settings(
    MAILERS={
        "default": {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"},
        "delivery": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    }
)


@pytest.fixture(autouse=True)
def vocabulary() -> None:
    make_topics()


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def url(name: str, world: World, **kwargs: object) -> str:
    return reverse(name, kwargs={"slug": world.center.slug, **kwargs})


def text_of(response) -> str:
    return " ".join(response.content.decode().split())


def form_data(**fields: object) -> dict[str, object]:
    data: dict[str, object] = {
        "summary": "We read your draft aloud and found the thesis.",
        "next_steps": "Move the thesis to the first paragraph.",
        "staff_notes": "Ready to work on evidence next time.",
        "minutes": "30",
        "topics": ["argument:thesis", "revising"],
        "project": "",
        "action": "save",
    }
    data.update(fields)
    return data


# --- Writing a record -------------------------------------------------------------


@QUEUED
def test_a_tutor_writes_saves_and_finalizes_a_record(
    client, world, django_capture_on_commit_callbacks
) -> None:
    topics("argument:thesis")
    appointment = appointment_for(world, world.student)
    client.force_login(world.tutor)
    start = url("records:start", world, appointment_id=appointment.pk)

    page = client.get(start)
    assert page.status_code == 200
    assert "Students can ask to see everything written here" in text_of(page)
    assert "FERPA" in text_of(page)

    response = client.post(start, form_data(student=str(world.student.pk)))
    rec = SessionRecord.objects.get(appointment=appointment, student=world.student)
    assert response.status_code == 302
    assert response["Location"] == url("records:edit", world, record_id=rec.pk)
    assert rec.status == RecordStatus.DRAFT
    assert mail.outbox == []

    # Opening the start page again goes to the draft instead of a second record.
    assert client.get(start)["Location"] == url("records:edit", world, record_id=rec.pk)

    edit = url("records:edit", world, record_id=rec.pk)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(edit, form_data(action="finalize"))
    rec.refresh_from_db()
    assert rec.status == RecordStatus.FINAL
    assert response["Location"] == url("records:detail", world, record_id=rec.pk)
    assert [m.to for m in mail.outbox] == [[world.student.email]]
    # A final record's edit page leads to the amendment form.
    assert client.get(edit)["Location"] == url("records:amend", world, record_id=rec.pk)


def test_finalizing_an_incomplete_record_keeps_it_as_a_draft(client, world) -> None:
    topics("revising")
    appointment = appointment_for(world, world.student)
    client.force_login(world.tutor)
    response = client.post(
        url("records:start", world, appointment_id=appointment.pk),
        form_data(
            student=str(world.student.pk), summary="", topics=["revising"], action="finalize"
        ),
        follow=True,
    )
    rec = SessionRecord.objects.get(appointment=appointment)
    assert rec.status == RecordStatus.DRAFT
    text = text_of(response)
    assert "Saved as a draft" in text
    assert "Write a summary for the student." in text
    assert "Tag at least one concern." in text


def test_a_group_record_can_start_drafts_for_the_others(client, world) -> None:
    topics("argument:thesis")
    others = [make_member(world.center, Role.STUDENT) for _ in range(2)]
    group = appointment_for(world, [world.student, *others], capacity=4)
    client.force_login(world.tutor)
    start = url("records:start", world, appointment_id=group.pk)
    # With several students, the page needs to know whose record it is.
    assert client.get(start)["Location"] == url(
        "scheduling:appointment", world, appointment_id=group.pk
    )
    page = client.get(start, {"student": world.student.pk})
    assert "Also start drafts for the other 2 students" in text_of(page)
    client.post(start, form_data(student=str(world.student.pk), copy_to_group="on"))
    records = SessionRecord.objects.filter(appointment=group)
    assert records.count() == 3
    assert set(records.values_list("status", flat=True)) == {RecordStatus.DRAFT}
    copies = records.exclude(student=world.student)
    assert set(copies.values_list("staff_notes", flat=True)) == {""}


def test_without_javascript_attaching_redraws_the_form_unsaved(client, world) -> None:
    resource = make_resource("argument:thesis", title="Thesis tips")
    appointment = appointment_for(world, world.student)
    client.force_login(world.tutor)
    start = url("records:start", world, appointment_id=appointment.pk)
    response = client.post(
        start,
        form_data(student=str(world.student.pk), summary="Half written", attach=str(resource.pk)),
    )
    assert response.status_code == 200
    html = response.content.decode()
    assert f'name="resources" value="{resource.pk}"' in html
    assert "Half written" in html
    assert "Attached Thesis tips. Save the record to keep it." in text_of(response)
    assert not SessionRecord.objects.exists()
    # No error messages on a redraw: nothing was submitted yet.
    assert "usa-error-message" not in html

    # Saving keeps the attachment and its note, in order.
    other = make_resource("argument:thesis", title="Second")
    client.post(
        start,
        form_data(
            student=str(world.student.pk),
            resources=[str(resource.pk), str(other.pk)],
            **{f"note-{resource.pk}": "Read the first half."},
        ),
    )
    rec = SessionRecord.objects.get()
    assert [(a.resource.title, a.note) for a in rec.attachments.all()] == [
        ("Thesis tips", "Read the first half."),
        ("Second", ""),
    ]

    # And "take it off" removes one.
    client.post(
        url("records:edit", world, record_id=rec.pk),
        form_data(resources=[str(resource.pk), str(other.pk)], remove=[str(other.pk)]),
    )
    assert [a.resource.title for a in rec.attachments.all()] == ["Thesis tips"]


def test_without_javascript_the_search_redraws_with_results(client, world) -> None:
    make_resource("citation:apa", title="APA reference list")
    appointment = appointment_for(world, world.student)
    client.force_login(world.tutor)
    response = client.post(
        url("records:start", world, appointment_id=appointment.pk),
        form_data(student=str(world.student.pk), resource_q="reference", search="1"),
    )
    text = text_of(response)
    assert "Matching “reference”" in text
    assert "APA reference list" in text
    assert not SessionRecord.objects.exists()


def test_a_resource_from_another_center_cannot_be_attached(client, world, other_center) -> None:
    theirs = make_resource("argument:thesis", center=other_center)
    appointment = appointment_for(world, world.student)
    client.force_login(world.tutor)
    client.post(
        url("records:start", world, appointment_id=appointment.pk),
        form_data(student=str(world.student.pk), resources=[str(theirs.pk)]),
    )
    assert SessionRecord.objects.get().attachments.count() == 0


def test_htmx_suggestions_follow_the_topics(client, world) -> None:
    make_resource("citation:apa", title="APA reference list")
    attached = make_resource("citation:apa", title="Already on the record")
    client.force_login(world.tutor)
    response = client.post(
        url("records:resource_panel", world),
        {"topics": ["citation:apa"], "resources": [str(attached.pk)]},
        HTTP_HX_REQUEST="true",
    )
    html = response.content.decode()
    assert "<html" not in html
    assert "APA reference list" in html
    assert "Already on the record" not in html
    assert 'id="resource-status" hx-swap-oob="innerHTML"' in html
    assert "1 suggestion for the topics you chose." in text_of(response)


def test_htmx_attach_returns_one_row(client, world) -> None:
    resource = make_resource("argument:thesis", title="Thesis tips")
    client.force_login(world.tutor)
    attach = url("records:attach_row", world)
    response = client.post(attach, {"attach": str(resource.pk)}, HTTP_HX_REQUEST="true")
    html = response.content.decode()
    assert html.count('name="resources"') == 1
    assert f'id="note-{resource.pk}"' in html
    assert "Attached Thesis tips" in html
    assert 'id="attached-empty"' in html

    again = client.post(
        attach,
        {"attach": str(resource.pk), "resources": [str(resource.pk)]},
        HTTP_HX_REQUEST="true",
    )
    assert 'name="resources"' not in again.content.decode()
    assert "already attached" in again.content.decode()


def test_amending_through_the_page_needs_a_reason(client, world) -> None:
    rec = write_record(appointment_for(world, world.student), world.student)
    client.force_login(world.tutor)
    amend = url("records:amend", world, record_id=rec.pk)
    data = form_data(summary="A better summary.", action="amend")
    response = client.post(amend, data)
    assert response.status_code == 200
    assert "This field is required" in text_of(response)
    rec.refresh_from_db()
    assert rec.summary == "We worked on your thesis."

    response = client.post(amend, {**data, "reason": "The first summary was unclear."})
    assert response["Location"] == url("records:detail", world, record_id=rec.pk)
    rec.refresh_from_db()
    assert rec.summary == "A better summary."
    page = text_of(client.get(url("records:detail", world, record_id=rec.pk)))
    assert "Changes after it was finalized" in page
    assert "The first summary was unclear." in page
    assert "We worked on your thesis." in page  # the earlier version stays readable


def test_a_new_project_can_be_started_from_the_record(client, world) -> None:
    appointment = appointment_for(world, world.student)
    client.force_login(world.tutor)
    start = url("records:start", world, appointment_id=appointment.pk)
    response = client.post(start, form_data(student=str(world.student.pk), project="new"))
    assert "Give the new project a title." in text_of(response)
    client.post(
        start,
        form_data(
            student=str(world.student.pk),
            project="new",
            project_title="Lab report 2",
            project_course="CHEM 101",
            project_due_on="2030-01-15",
        ),
    )
    project = WritingProject.objects.get()
    assert (project.student, project.title, project.course) == (
        world.student,
        "Lab report 2",
        "CHEM 101",
    )
    assert SessionRecord.objects.get().project == project


def test_a_meeting_that_was_not_booked(client, world) -> None:
    client.force_login(world.tutor)
    new = url("records:new", world)
    when = timezone.localtime(timezone.now() - timedelta(hours=1), world.schedule.tz)
    response = client.post(
        new,
        {
            "student": str(world.student.pk),
            "held_at": when.strftime("%Y-%m-%dT%H:%M"),
            "minutes": "20",
        },
    )
    rec = SessionRecord.objects.get()
    assert response["Location"] == url("records:edit", world, record_id=rec.pk)
    assert rec.appointment is None and rec.tutor == world.tutor and rec.minutes == 20
    future = timezone.localtime(timezone.now() + timedelta(days=1), world.schedule.tz)
    response = client.post(
        new,
        {
            "student": str(world.student.pk),
            "held_at": future.strftime("%Y-%m-%dT%H:%M"),
            "minutes": "20",
        },
    )
    assert "after the meeting has started" in text_of(response)


# --- Lists, briefs and status -----------------------------------------------------


def test_records_to_write_lists_past_appointments_without_a_final_record(client, world) -> None:
    other_tutor = make_member(world.center, Role.TUTOR)
    done = appointment_for(world, world.student, days=-1)
    write_record(done, world.student)
    draft = appointment_for(
        world, make_member(world.center, Role.STUDENT, full_name="Dee Draft"), days=-2
    )
    write_record(draft, draft.participants.get().student, final=False)
    missing = appointment_for(
        world, make_member(world.center, Role.STUDENT, full_name="Mo Missing"), days=-3
    )
    appointment_for(
        world,
        make_member(world.center, Role.STUDENT, full_name="Other Tutors Student"),
        days=-4,
        tutor=other_tutor,
    )
    # The upcoming appointment from the world isn't listed.

    client.force_login(world.tutor)
    text = text_of(client.get(url("records:to_write", world)))
    assert "Dee Draft" in text and "Finish it" in text
    assert "Mo Missing" in text and "Write it" in text
    assert world.student.display_name not in text  # final already
    assert "Other Tutors Student" not in text  # another tutor's appointment

    desk = make_member(world.center, Role.FRONT_DESK)
    client.force_login(desk)
    text = text_of(client.get(url("records:to_write", world)))
    assert "Other Tutors Student" in text and "Mo Missing" in text
    assert "Write it" not in text and "Finish it" not in text
    assert url("records:start", world, appointment_id=missing.pk) not in text


def test_the_front_desk_sees_whether_a_record_exists_not_what_it_says(client, world) -> None:
    appointment = appointment_for(world, world.student, days=-1)
    write_record(appointment, world.student, summary="PRIVATE SUMMARY", staff_notes="PRIVATE NOTES")
    desk = make_member(world.center, Role.FRONT_DESK)
    client.force_login(desk)

    page = text_of(client.get(url("scheduling:appointment", world, appointment_id=appointment.pk)))
    assert f"{world.student.display_name}: final" in page
    assert "PRIVATE" not in page
    assert "Before the session" not in page

    day = timezone.localtime(appointment.starts_at, world.schedule.tz).date()
    sheet = text_of(
        client.get(url("scheduling:day_sheet", world, schedule_id=world.schedule.pk), {"date": day})
    )
    assert f"{world.student.display_name}: final" in sheet
    assert "PRIVATE" not in sheet
    rec = SessionRecord.objects.get()
    assert client.get(url("records:detail", world, record_id=rec.pk)).status_code == 403


def test_the_tutor_sees_a_brief_before_the_session(client, world) -> None:
    project = WritingProject.objects.create(
        center=world.center, student=world.student, title="Capstone essay", course="ENG 300"
    )
    WritingProject.objects.create(
        center=world.center, student=world.student, title="Finished essay", is_finished=True
    )
    for days, summary in ((-10, "Oldest"), (-8, "Second"), (-6, "Third"), (-4, "Newest")):
        write_record(
            appointment_for(world, world.student, days=days), world.student, summary=summary
        )
    write_record(
        appointment_for(world, world.student, days=-2), world.student, final=False, summary="Draft"
    )
    client.force_login(world.tutor)
    page = text_of(
        client.get(url("scheduling:appointment", world, appointment_id=world.appointment.pk))
    )
    assert f"Before the session: {world.student.display_name}" in page
    assert "Capstone essay" in page and "Finished essay" not in page
    assert "Newest" in page and "Third" in page and "Second" in page
    assert "Oldest" not in page and "Draft" not in page
    assert project.title in page
    event = AuditEvent.objects.get(action=AuditAction.HISTORY_VIEWED)
    assert event.details["where"] == "appointment brief"
    assert event.details["student"] == str(world.student.pk)


def test_a_student_sees_a_link_to_their_own_summary_on_the_appointment(client, world) -> None:
    appointment = appointment_for(world, world.student, days=-1)
    rec = write_record(appointment, world.student)
    client.force_login(world.student)
    page = client.get(url("scheduling:appointment", world, appointment_id=appointment.pk))
    html = page.content.decode()
    assert url("records:my_record", world, record_id=rec.pk) in html
    assert "Before the session" not in html
    assert "Session records" not in html


# --- The student's pages ----------------------------------------------------------


def test_my_writing_groups_summaries_by_project(client, world) -> None:
    project = WritingProject.objects.create(
        center=world.center, student=world.student, title="Capstone essay", course="ENG 300"
    )
    resource = make_resource("argument:thesis", title="Thesis tips")
    write_record(
        appointment_for(world, world.student, days=-3),
        world.student,
        summary="Project summary",
        next_steps="Project next step",
        staff_notes="SECRET STAFF NOTE",
        project=project,
        resources=[(resource, "Read the examples.")],
    )
    write_record(
        appointment_for(world, world.student, days=-2), world.student, summary="Loose summary"
    )
    write_record(
        appointment_for(world, world.student, days=-1),
        world.student,
        final=False,
        summary="DRAFT TEXT",
    )
    client.force_login(world.student)
    response = client.get(url("records:my_writing", world))
    text = text_of(response)
    assert "Capstone essay" in text and "ENG 300" in text
    assert "Project summary" in text and "Project next step" in text
    assert "Thesis tips" in text and "Read the examples." in text
    assert "Sessions not linked to a project" in text and "Loose summary" in text
    assert text.index("Project summary") < text.index("Sessions not linked to a project")
    assert "DRAFT TEXT" not in text
    assert "SECRET STAFF NOTE" not in text
    assert "copy of everything it keeps" in text

    world.center.students_see_staff_notes = True
    world.center.save()
    text = text_of(client.get(url("records:my_writing", world)))
    assert "SECRET STAFF NOTE" in text
    assert "Notes your tutor wrote for center staff" in text


def test_a_student_cannot_open_drafts_or_anyone_elses_record(client, world) -> None:
    other = make_member(world.center, Role.STUDENT)
    theirs = write_record(appointment_for(world, other, days=-2), other)
    draft = write_record(appointment_for(world, world.student, days=-1), world.student, final=False)
    client.force_login(world.student)
    assert client.get(url("records:my_record", world, record_id=theirs.pk)).status_code == 404
    assert client.get(url("records:my_record", world, record_id=draft.pk)).status_code == 404
    assert client.get(url("records:detail", world, record_id=theirs.pk)).status_code == 403


# --- Director tools ---------------------------------------------------------------


def test_record_settings_are_audited(client, world) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    response = client.post(url("records:settings", world), {"students_see_staff_notes": "on"})
    assert response.status_code == 302
    world.center.refresh_from_db()
    assert world.center.students_see_staff_notes
    assert not world.center.summary_email_includes_text
    event = AuditEvent.objects.get(action=AuditAction.RECORD_SETTINGS_CHANGED)
    assert event.details == {"students_see_staff_notes": True}


def test_a_director_adds_a_link_that_tutors_then_see_suggested(client, world, other_center) -> None:
    topics("citation:apa")
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    new = url("records:resource_create", world)
    response = client.post(
        new,
        {
            "title": "Our APA handout",
            "url": "http://example.edu/apa",
            "summary": "How our center formats APA references.",
            "license": "all-rights-reserved",
            "language": "en",
            "topics": ["citation:apa"],
            "is_active": "on",
        },
    )
    assert "Use an https:// address." in text_of(response)
    response = client.post(
        new,
        {
            "title": "Our APA handout",
            "url": "https://example.edu/apa",
            "summary": "How our center formats APA references.",
            "license": "all-rights-reserved",
            "language": "en",
            "topics": ["citation:apa"],
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    resource = Resource.objects.get()
    assert resource.center == world.center and resource.catalog_id is None
    assert resource.publisher == world.center.name

    client.force_login(world.tutor)
    panel = client.post(url("records:resource_panel", world), {"topics": ["citation:apa"]})
    assert "Our APA handout" in panel.content.decode()
    outsider = make_member(other_center, Role.TUTOR)
    client.force_login(outsider)
    panel = client.post(
        reverse("records:resource_panel", kwargs={"slug": other_center.slug}),
        {"topics": ["citation:apa"]},
    )
    assert "Our APA handout" not in panel.content.decode()


def test_a_tutor_can_edit_the_project_of_a_student_they_tutor(client, world) -> None:
    project = WritingProject.objects.create(center=world.center, student=world.student, title="Old")
    client.force_login(world.tutor)
    response = client.post(
        url("records:project_edit", world, project_id=project.pk),
        {"title": "New title", "course": "", "instructor": "", "due_on": "", "is_finished": "on"},
    )
    assert response["Location"] == url(
        "records:student_history", world, membership_id=membership(world.center, world.student).pk
    )
    project.refresh_from_db()
    assert project.title == "New title" and project.is_finished
    assert AuditEvent.objects.filter(action=AuditAction.PROJECT_SAVED).exists()


def test_the_center_page_links_to_the_records_pages(client, world) -> None:
    client.force_login(world.student)
    student_page = client.get(world.center.get_absolute_url()).content.decode()
    assert url("records:my_writing", world) in student_page
    assert url("records:to_write", world) not in student_page
    client.force_login(world.tutor)
    tutor_page = client.get(world.center.get_absolute_url()).content.decode()
    assert url("records:to_write", world) in tutor_page
    assert url("records:new", world) in tutor_page
    assert url("records:resources", world) not in tutor_page
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    director_page = client.get(world.center.get_absolute_url()).content.decode()
    assert url("records:resources", world) in director_page
    assert url("records:settings", world) in director_page
