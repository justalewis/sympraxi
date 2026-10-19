"""The written-feedback queue: sending a draft, claiming, writing back, who sees what,
automatic assignment, overdue alerts, turnaround, and the exports."""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Membership, Role
from sympraxi.feedback import services
from sympraxi.feedback.models import FileRole, QueueSettings, Submission, SubmissionStatus
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export
from sympraxi.records import services as record_services
from sympraxi.records.models import SessionRecord
from tests.feedback_world import add_queue, open_queue, send
from tests.files_world import PDF, TXT
from tests.records_world import make_topics, topics
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    make_topics()
    return add_queue(build_world(center))


def url(name: str, world, **kwargs) -> str:
    return reverse(f"feedback:{name}", kwargs={"slug": world.center.slug, **kwargs})


def detail(world, submission) -> str:
    return url("detail", world, submission_id=submission.pk)


def finalize(rec: SessionRecord, tutor) -> None:
    content = record_services.Content(
        minutes=25,
        summary="Your argument is clear; tighten the second paragraph.",
        topics=topics("argument:thesis"),
    )
    record_services.finalize(actor=tutor, rec=rec, content=content, notify=False)


# --- Sending a draft -------------------------------------------------------------------


def post_draft(client, world, **fields):
    data = {
        "title": "Essay 3",
        "course": "ENG 101",
        "wanted": "Help with my conclusion.",
        "day": (services.today(world.center) + timedelta(days=4)).isoformat(),
        "file": SimpleUploadedFile("essay.pdf", PDF),
    }
    data.update(fields)
    return client.post(url("submit", world), data)


def test_a_student_sends_a_draft(client, world) -> None:
    client.force_login(world.student)
    response = post_draft(client, world)
    assert response.status_code == 302
    submission = Submission.objects.get(title="Essay 3")
    assert submission.status == SubmissionStatus.WAITING
    assert submission.tutor is None and submission.record is None
    assert response["Location"] == detail(world, submission)
    assert submission.files.get().file.name == "essay.pdf"
    assert AuditEvent.objects.filter(action=AuditAction.SUBMISSION_SENT).exists()
    assert "Essay 3" in client.get(url("mine", world)).content.decode()


def test_a_closed_queue_takes_nothing(client, world) -> None:
    QueueSettings.objects.update(is_open=False)
    client.force_login(world.student)
    assert "isn't taking drafts" in client.get(url("submit", world)).content.decode()
    client.post(url("submit", world), {"title": "x"})
    assert not Submission.objects.filter(title="x").exists()


@pytest.mark.parametrize("days", [0, 1, 400])
def test_the_date_must_leave_time_and_not_be_absurd(client, world, days) -> None:
    client.force_login(world.student)
    day = (services.today(world.center) + timedelta(days=days)).isoformat()
    response = post_draft(client, world, day=day)
    assert response.status_code == 200
    assert not Submission.objects.filter(title="Essay 3").exists()


def test_a_file_that_isnt_what_it_says_is_refused(client, world) -> None:
    client.force_login(world.student)
    response = post_draft(client, world, file=SimpleUploadedFile("essay.pdf", b"MZ program"))
    assert "isn&#x27;t a PDF" in response.content.decode()
    assert not Submission.objects.filter(title="Essay 3").exists()


def test_only_students_can_send(client, world) -> None:
    client.force_login(world.tutor)
    assert client.get(url("submit", world)).status_code == 403


def test_a_student_adds_drafts_up_to_the_limit(client, world) -> None:
    open_queue(world.center, max_files=2)
    client.force_login(world.student)
    add = url("add_file", world, submission_id=world.submission.pk)
    client.post(add, {"file": SimpleUploadedFile("two.txt", TXT)})
    assert world.submission.files.count() == 2
    response = client.post(add, {"file": SimpleUploadedFile("three.txt", TXT)})
    assert "can have 2 files" in response.content.decode()
    assert world.submission.files.count() == 2


# --- The queue and claiming ------------------------------------------------------------


def test_tutors_see_waiting_work_without_the_student(client, world) -> None:
    client.force_login(world.tutor)
    page = client.get(url("queue", world)).content.decode()
    assert "Essay 2, first draft" in page and "Is my argument clear?" in page
    assert world.student.display_name not in page.split("Waiting for a tutor")[1]
    assert "draft.pdf" not in page


def test_directors_see_names_and_everyones_work(client, world, person_with_role) -> None:
    client.force_login(person_with_role("director", world.center))
    page = client.get(url("queue", world)).content.decode()
    assert world.student.display_name in page
    assert "With other tutors" in page and "Essay 1" in page


def test_the_draft_stays_closed_until_a_tutor_claims_it(client, world) -> None:
    client.force_login(world.tutor)
    assert client.get(detail(world, world.submission)).status_code == 404
    file_url = url("download", world, file_id=world.submission_file.pk)
    assert client.get(file_url).status_code == 404
    client.post(url("claim", world, submission_id=world.submission.pk))
    assert client.get(detail(world, world.submission)).status_code == 200
    download = client.get(file_url)
    assert download.status_code == 200 and b"".join(download.streaming_content) == PDF


def test_claiming_starts_the_feedback_record(client, world) -> None:
    client.force_login(world.tutor)
    client.post(url("claim", world, submission_id=world.submission.pk))
    submission = Submission.objects.get(pk=world.submission.pk)
    assert (submission.status, submission.tutor) == (SubmissionStatus.CLAIMED, world.tutor)
    rec = submission.record
    assert rec is not None
    assert (rec.student, rec.tutor, rec.appointment) == (world.student, world.tutor, None)
    assert AuditEvent.objects.filter(action=AuditAction.SUBMISSION_CLAIMED).exists()


def test_only_one_tutor_wins_a_claim(world) -> None:
    other = make_member(world.center, Role.TUTOR)
    services.claim(world.submission.pk, world.tutor)
    with pytest.raises(services.QueueError, match="Someone else"):
        services.claim(world.submission.pk, other)


def test_front_desk_staff_have_no_part_in_it(client, world) -> None:
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(url("queue", world)).status_code == 403
    assert client.get(detail(world, world.submission)).status_code == 403


def test_another_student_cant_open_it(client, world) -> None:
    client.force_login(make_member(world.center, Role.STUDENT))
    assert client.get(detail(world, world.submission)).status_code == 404
    file_url = url("download", world, file_id=world.submission_file.pk)
    assert client.get(file_url).status_code == 404


# --- Writing back and returning --------------------------------------------------------


def test_final_feedback_returns_the_submission(client, world) -> None:
    rec = world.claimed.record
    finalize(rec, world.tutor)
    world.claimed.refresh_from_db()
    assert world.claimed.status == SubmissionStatus.RETURNED
    rec.refresh_from_db()
    assert world.claimed.returned_at == rec.finalized_at
    assert AuditEvent.objects.filter(action=AuditAction.SUBMISSION_RETURNED).exists()
    client.force_login(world.student)
    page = client.get(detail(world, world.claimed)).content.decode()
    assert "Read your feedback" in page
    assert (
        reverse("records:my_record", kwargs={"slug": world.center.slug, "record_id": rec.pk})
        in page
    )


def test_a_commented_file_reaches_the_student_only_on_return(client, world) -> None:
    client.force_login(world.tutor)
    add = url("add_file", world, submission_id=world.claimed.pk)
    client.post(add, {"file": SimpleUploadedFile("commented.pdf", PDF)})
    sent_back = world.claimed.files.get(role=FileRole.RETURNED)
    file_url = url("download", world, file_id=sent_back.pk)
    assert client.get(file_url).status_code == 200
    client.force_login(world.student)
    assert client.get(file_url).status_code == 404
    assert "commented.pdf" not in client.get(detail(world, world.claimed)).content.decode()
    finalize(world.claimed.record, world.tutor)
    assert client.get(file_url).status_code == 200
    assert "commented.pdf" in client.get(detail(world, world.claimed)).content.decode()


def test_a_tutor_who_doesnt_have_it_cant_send_files_back(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    response = client.get(url("add_file", world, submission_id=world.claimed.pk))
    assert "Files are added by the student" in response.content.decode()


def test_files_can_be_removed_until_the_feedback_is_returned(client, world) -> None:
    client.force_login(world.student)
    remove = url("remove_file", world, file_id=world.submission_file.pk)
    client.post(remove)
    world.submission_file.file.refresh_from_db()
    assert world.submission_file.file.removed_at is not None


# --- Canceling and assigning -----------------------------------------------------------


def test_a_student_cancels_while_nobody_has_it(client, world) -> None:
    client.force_login(world.student)
    client.post(url("cancel", world, submission_id=world.submission.pk))
    world.submission.refresh_from_db()
    assert world.submission.status == SubmissionStatus.CANCELED
    with pytest.raises(services.QueueError):
        services.claim(world.submission.pk, world.tutor)


def test_a_student_cant_cancel_once_a_tutor_has_it_but_a_director_can(client, world) -> None:
    client.force_login(world.student)
    client.post(url("cancel", world, submission_id=world.claimed.pk))
    world.claimed.refresh_from_db()
    assert world.claimed.status == SubmissionStatus.CLAIMED
    client.force_login(make_member(world.center, Role.DIRECTOR))
    client.post(url("cancel", world, submission_id=world.claimed.pk))
    world.claimed.refresh_from_db()
    assert world.claimed.status == SubmissionStatus.CANCELED


def test_a_director_assigns_and_the_tutor_is_emailed_without_names(
    client, world, mailoutbox, django_capture_on_commit_callbacks
) -> None:
    other = make_member(world.center, Role.TUTOR)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            url("assign", world, submission_id=world.submission.pk), {"tutor": str(other.pk)}
        )
    world.submission.refresh_from_db()
    assert (world.submission.status, world.submission.tutor) == (SubmissionStatus.CLAIMED, other)
    [mail] = [m for m in mailoutbox if m.to == [other.email]]
    assert world.student.display_name not in mail.body
    assert "Essay 2" not in mail.body and "Sign in to see it" in mail.body


def test_reassigning_moves_the_unfinished_feedback(world) -> None:
    other = make_member(world.center, Role.TUTOR)
    rec = world.claimed.record
    services.assign(world.claimed.pk, other, by=None)
    rec.refresh_from_db()
    world.claimed.refresh_from_db()
    assert (rec.tutor, world.claimed.tutor) == (other, other)
    assert world.claimed.record == rec


def test_only_staff_of_the_center_can_be_assigned(world) -> None:
    with pytest.raises(services.QueueError, match="can't take"):
        services.assign(world.submission.pk, make_member(world.center, Role.STUDENT), by=None)


# --- The scheduler's jobs --------------------------------------------------------------


def test_automatic_assignment_shares_the_load(world) -> None:
    Submission.objects.filter(pk=world.claimed.pk).update(status=SubmissionStatus.CANCELED)
    other = make_member(world.center, Role.TUTOR)
    open_queue(world.center, auto_assign=True, max_open_per_tutor=1)
    second = send(world.center, world.student, title="Essay 4")
    third = send(world.center, world.student, title="Essay 5")
    assert services.auto_assign() == 2
    got = {
        s.pk: s.tutor for s in Submission.objects.filter(pk__in=[world.submission.pk, second.pk])
    }
    assert set(got.values()) == {world.tutor, other}
    third.refresh_from_db()
    assert third.status == SubmissionStatus.WAITING  # everyone is at their limit
    assert services.auto_assign() == 0


def test_nothing_is_assigned_unless_the_center_asks(world) -> None:
    assert services.auto_assign() == 0
    world.submission.refresh_from_db()
    assert world.submission.status == SubmissionStatus.WAITING


def test_an_overdue_submission_is_flagged_once(world, mailoutbox) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    Submission.objects.update(due_at=timezone.now() - timedelta(hours=1))
    assert services.overdue_alerts(base_url="https://example.edu") == 2
    to = {tuple(m.to) for m in mailoutbox}
    assert to == {(world.tutor.email,), (director.email,)}
    for mail in mailoutbox:
        assert world.student.display_name not in mail.body
        assert "Essay" not in mail.body
    assert services.overdue_alerts(base_url="https://example.edu") == 0
    assert len(mailoutbox) == 2


def test_returned_and_canceled_work_is_never_overdue(world, mailoutbox) -> None:
    finalize(world.claimed.record, world.tutor)
    Submission.objects.filter(pk=world.submission.pk).update(status=SubmissionStatus.CANCELED)
    Submission.objects.update(due_at=timezone.now() - timedelta(hours=1))
    assert services.overdue_alerts(base_url="https://example.edu") == 0


# --- Turnaround ------------------------------------------------------------------------


def test_turnaround_counts_what_came_back_and_how_fast(world) -> None:
    finalize(world.claimed.record, world.tutor)
    Submission.objects.filter(pk=world.claimed.pk).update(
        created_at=timezone.now() - timedelta(hours=30)
    )
    today = services.today(world.center)
    stats = services.turnaround(world.center, today - timedelta(days=3), today)
    assert (stats.sent, stats.returned, stats.open, stats.on_time) == (2, 1, 1, 1)
    assert stats.median_hours is not None and 29 < stats.median_hours < 31
    assert [(r.tutor, r.returned) for r in stats.by_tutor] == [(world.tutor, 1)]


def test_the_turnaround_page_is_for_directors(client, world) -> None:
    client.force_login(world.tutor)
    assert client.get(url("turnaround", world)).status_code == 403
    client.force_login(make_member(world.center, Role.DIRECTOR))
    page = client.get(url("turnaround", world)).content.decode()
    assert "Typical time to return" in page


def test_settings_are_saved_and_audited(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    client.post(
        url("settings", world),
        {"is_open": "on", "lead_days": 3, "max_files": 4, "max_open_per_tutor": 2},
    )
    queue = services.settings_for(world.center)
    assert (queue.lead_days, queue.max_files, queue.auto_assign) == (3, 4, False)
    assert AuditEvent.objects.filter(action=AuditAction.QUEUE_SETTINGS_CHANGED).exists()


def test_the_student_export_lists_their_submissions(world) -> None:
    membership = Membership.objects.get(center=world.center, user=world.student, role=Role.STUDENT)
    data = export.build(membership, exported_by=make_member(world.center, Role.DIRECTOR))
    assert {s["title"] for s in data["submissions"]} == {"Essay 2, first draft", "Essay 1"}
    waiting = next(s for s in data["submissions"] if s["title"] == "Essay 2, first draft")
    assert waiting["status"] == "Waiting for a tutor" and waiting["tutor"] is None
    assert [f["name"] for f in waiting["files"]] == ["draft.pdf"]
    assert "storage_name" not in str(waiting)


def test_the_claim_page_asks_before_claiming(client, world) -> None:
    client.force_login(world.tutor)
    page = client.get(url("claim", world, submission_id=world.submission.pk))
    assert "Claim it" in page.content.decode()
    world.submission.refresh_from_db()
    assert world.submission.status == SubmissionStatus.WAITING
