"""The full-center export: building it, what it holds, who gets it, and its link expiring."""

import csv
import io
import json
import zipfile
from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.forms import surveys
from sympraxi.forms.models import FormKind, Response
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.reports import center_export
from sympraxi.reports.models import CenterExport, ExportStatus
from tests.attendance_world import add_attendance
from tests.factories import CenterFactory
from tests.forms_world import SURVEY, SURVEY_ANSWERS, add_forms, anonymous, make_form
from tests.records_world import add_records
from tests.reports_world import ready_export
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

QUEUED = override_settings(
    MAILERS={
        "default": {"BACKEND": "sympraxi.core.mail.QueuedEmailBackend"},
        "delivery": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"},
    },
)

FILES = {
    "manifest.json",
    "center.json",
    "people.json",
    "terms.json",
    "rooms.json",
    "schedules.json",
    "appointments.json",
    "appointments.csv",
    "exemptions.json",
    "booking_blocks.json",
    "writing_projects.json",
    "session_records.json",
    "forms.json",
    "form_responses.json",
    "survey_results.json",
    "resources.json",
    "time_entries.json",
    "files.json",
    "submissions.json",
    "submission_files.json",
    "comments.json",
    "goals.json",
    "room_documents.json",
    "room_versions.json",
    "disclosures.json",
    "arrivals.json",
    "timesheet_approvals.json",
    "payroll_details.json",
    "waiting_list.json",
    "workshops.json",
    "workshop_registrations.json",
    "audit_events.json",
    "audit_events.csv",
}


@pytest.fixture
def world(center):
    return add_forms(add_attendance(add_records(build_world(center))))


@pytest.fixture
def director(client, world):
    user = make_member(world.center, Role.DIRECTOR, full_name="Dana Director")
    client.force_login(user)
    return user


def page_url(world) -> str:
    return reverse("reports:center_export", kwargs={"slug": world.center.slug})


def download_url(export) -> str:
    return reverse(
        "reports:center_export_download",
        kwargs={"slug": export.center.slug, "export_id": export.pk},
    )


def build(client, world) -> CenterExport:
    """Ask for an export as the signed-in director. The test task backend runs it now."""
    response = client.post(page_url(world))
    assert response.status_code == 302
    return CenterExport.objects.filter(center=world.center).latest("requested_at")


def opened(export) -> zipfile.ZipFile:
    with center_export.storage().open(export.file_name, "rb") as f:
        return zipfile.ZipFile(io.BytesIO(f.read()))


def read_json(archive: zipfile.ZipFile, name: str):
    return json.loads(archive.read(name))


@QUEUED
def test_a_director_asks_and_the_worker_builds_it(client, director, world) -> None:
    export = build(client, world)
    assert export.status == ExportStatus.READY
    assert export.requested_by == director
    assert export.expires_at - export.ready_at == timedelta(hours=24)
    archive = opened(export)
    assert set(archive.namelist()) == FILES
    manifest = read_json(archive, "manifest.json")
    assert manifest["format"] == "sympraxi.center-export"
    assert manifest["requested_by"]["email"] == director.email
    assert manifest["files"] == export.counts
    assert export.size == center_export.storage().size(export.file_name)
    # The requester is told it's ready, with a link to the page, not to the file.
    (message,) = mail.outbox
    assert message.to == [director.email]
    assert page_url(world) in message.body
    assert str(export.pk) not in message.body


def test_what_it_holds(client, director, world) -> None:
    archive = opened(build(client, world))
    people = {p["user_id"]: p for p in read_json(archive, "people.json")}
    assert people[str(world.student.pk)]["role"] == "student"
    appointments = {a["id"]: a for a in read_json(archive, "appointments.json")}
    missed = appointments[str(world.missed.pk)]
    assert missed["students"][0]["attendance"] == "no_show"
    assert missed["students"][0]["student"]["id"] == str(world.blocked_student.pk)
    schedules = read_json(archive, "schedules.json")
    assert schedules[0]["tutors"][0]["weekly_hours"]
    records = {r["id"]: r for r in read_json(archive, "session_records.json")}
    amended = records[str(world.record.pk)]
    assert amended["staff_notes"] == "Talked fast, seemed rushed."
    assert amended["amendments"][0]["reason"] == "Added a detail."
    assert records[str(world.draft.pk)]["status"] == "draft"
    assert records[str(world.answered_record.pk)]["report_answers"][0]["answers"]
    responses = read_json(archive, "form_responses.json")
    assert {r["kind"] for r in responses} == {"registration", "intake"}
    assert read_json(archive, "booking_blocks.json")[0]["id"] == str(world.block.pk)
    forms = {f["kind"] for f in read_json(archive, "forms.json")}
    assert forms == {"registration", "intake", "report", "survey"}
    rows = list(csv.DictReader(io.StringIO(archive.read("appointments.csv").decode("utf-8-sig"))))
    assert any(
        r["appointment_id"] == str(world.missed.pk) and r["attendance"] == "no_show" for r in rows
    )


def test_surveys_are_only_summarized(client, director, world) -> None:
    archive = opened(build(client, world))
    (result,) = [r for r in read_json(archive, "survey_results.json") if r["shown"]]
    assert result["responses"] == 5
    helpful = next(q for q in result["questions"] if q["label"] == "How helpful was the session?")
    assert {o["label"]: o["count"] for o in helpful["options"]} == {
        "Very helpful": 3,
        "Somewhat helpful": 1,
        "Not helpful": 1,
    }
    # No survey response, its ID or its date appears anywhere in the file.
    everything = b"".join(archive.read(n) for n in archive.namelist()).decode("utf-8")
    for response in Response.objects.filter(form__kind=FormKind.SURVEY):
        assert str(response.pk) not in everything
    assert "answered_on" not in everything
    assert world.survey_token not in everything
    assert "/survey/" not in everything


def test_terms_with_too_few_survey_responses_show_nothing(client, center) -> None:
    world = build_world(center)
    survey = make_form(center, FormKind.SURVEY, questions=SURVEY)
    for _ in range(surveys.MIN_RESPONSES - 1):
        anonymous(survey, SURVEY_ANSWERS)
    client.force_login(make_member(center, Role.DIRECTOR))
    results = read_json(opened(build(client, world)), "survey_results.json")
    assert results == [
        {
            "survey": survey.title,
            "term": world.term.name,
            "counted_from": world.term.starts_on.isoformat(),
            "counted_before": None,
            "shown": False,
        }
    ]


def test_other_centers_are_left_out(client, director, world) -> None:
    other = add_records(build_world(CenterFactory()))
    archive = opened(build(client, world))
    everything = b"".join(archive.read(n) for n in archive.namelist()).decode("utf-8")
    assert str(other.student.pk) not in everything
    assert str(other.record.pk) not in everything
    assert other.center.slug not in everything


def test_asking_and_downloading_are_audited(client, director, world) -> None:
    export = build(client, world)
    asked = AuditEvent.objects.get(action=AuditAction.CENTER_EXPORT_REQUESTED)
    assert (asked.actor_id, asked.center_id, asked.target_id) == (
        director.pk,
        world.center.pk,
        str(export.pk),
    )
    # The export's own audit log includes the request for it.
    audit = read_json(opened(export), "audit_events.json")
    assert any(e["action"] == AuditAction.CENTER_EXPORT_REQUESTED for e in audit)
    response = client.get(download_url(export))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/zip"
    assert "no-store" in response["Cache-Control"]
    assert response["Content-Disposition"].startswith(
        f'attachment; filename="{world.center.slug}-export-'
    )
    body = b"".join(response.streaming_content)
    assert zipfile.ZipFile(io.BytesIO(body)).namelist()
    downloaded = AuditEvent.objects.get(action=AuditAction.CENTER_EXPORT_DOWNLOADED)
    assert (downloaded.actor_id, downloaded.target_id) == (director.pk, str(export.pk))


def test_any_director_of_the_center_can_download(client, world) -> None:
    export = ready_export(world.center)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(download_url(export)).status_code == 200


@pytest.mark.parametrize("role", ["front_desk", "tutor", "student", "site_admin"])
def test_no_one_else_can_ask_or_download(client, person_with_role, world, role) -> None:
    export = ready_export(world.center)
    client.force_login(person_with_role(role, world.center))
    assert client.get(page_url(world)).status_code == 403
    assert client.post(page_url(world)).status_code == 403
    assert client.get(download_url(export)).status_code == 403
    assert CenterExport.objects.count() == 1
    assert not AuditEvent.objects.filter(action=AuditAction.CENTER_EXPORT_DOWNLOADED).exists()


def test_directors_of_other_centers_get_404(client, person_with_role, world) -> None:
    export = ready_export(world.center)
    client.force_login(person_with_role("director", CenterFactory()))
    assert client.get(download_url(export)).status_code == 404


def test_one_export_at_a_time(client, director, world) -> None:
    waiting = CenterExport.objects.create(center=world.center, requested_by=director)
    response = client.post(page_url(world), follow=True)
    assert "An export is already being built." in response.content.decode()
    assert list(CenterExport.objects.all()) == [waiting]
    assert not AuditEvent.objects.filter(action=AuditAction.CENTER_EXPORT_REQUESTED).exists()


def test_an_expired_link_downloads_nothing(client, director, world) -> None:
    export = ready_export(world.center, hours=-1)
    response = client.get(download_url(export))
    assert response.status_code == 410
    assert "This export can" in response.content.decode()
    assert not AuditEvent.objects.filter(action=AuditAction.CENTER_EXPORT_DOWNLOADED).exists()


def test_a_missing_file_downloads_nothing(client, director, world) -> None:
    export = ready_export(world.center)
    center_export.storage().delete(export.file_name)
    assert client.get(download_url(export)).status_code == 410


def test_the_scheduler_deletes_expired_files(world) -> None:
    live = ready_export(world.center)
    old = ready_export(CenterFactory(), hours=-1)
    stuck = CenterExport.objects.create(center=world.center)
    CenterExport.objects.filter(pk=stuck.pk).update(
        requested_at=timezone.now() - center_export.STUCK_AFTER - timedelta(minutes=1)
    )
    assert center_export.expire(timezone.now()) == 1
    old.refresh_from_db()
    assert (old.status, old.file_name) == (ExportStatus.EXPIRED, "")
    assert not center_export.storage().exists(center_export.file_name(old))
    assert center_export.storage().exists(live.file_name)
    stuck.refresh_from_db()
    assert stuck.status == ExportStatus.FAILED


def test_a_failed_build_is_marked_and_can_be_tried_again(
    client, director, world, monkeypatch
) -> None:
    def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(center_export, "write", broken)
    export = build(client, world)
    assert export.status == ExportStatus.FAILED
    assert export.file_name == ""
    monkeypatch.undo()
    assert build(client, world).status == ExportStatus.READY


def test_the_page_lists_recent_exports(client, director, world) -> None:
    ready = ready_export(world.center, by=director)
    expired = ready_export(world.center, by=director, hours=-1)
    page = client.get(page_url(world)).content.decode()
    assert download_url(ready) in page
    assert download_url(expired) not in page
    assert "Build a new export" in page
