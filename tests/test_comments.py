"""The comment viewer: PDF copies made by the worker, who can open the viewer, and the
rules for comments on a draft."""

import io
import json
import subprocess
from pathlib import Path

import pytest
from django.urls import reverse

from sympraxi.centers.models import Membership, Role
from sympraxi.feedback import services as feedback_services
from sympraxi.feedback.models import Comment, FileRole, SubmissionStatus
from sympraxi.files import convert, services
from sympraxi.files.models import PdfStatus
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export
from sympraxi.records import services as record_services
from tests.feedback_world import add_queue
from tests.files_world import PDF, docx
from tests.records_world import make_topics, topics
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    make_topics()
    return add_queue(build_world(center))


def route(name: str, world, attached) -> str:
    return reverse(f"feedback:{name}", kwargs={"slug": world.center.slug, "file_id": attached.pk})


def post_comment(client, world, attached=None, **fields):
    attached = attached or world.claimed_file
    data = {"page": 1, "quote": "The thesis", "rects": [[0.1, 0.2, 0.3, 0.02]], "body": "Cite it."}
    data.update(fields)
    return client.post(
        route("comments", world, attached), json.dumps(data), content_type="application/json"
    )


def finalize(world) -> None:
    content = record_services.Content(
        minutes=20, summary="Good draft.", topics=topics("argument:thesis")
    )
    record_services.finalize(
        actor=world.tutor, rec=world.claimed.record, content=content, notify=False
    )


# --- Making a PDF copy -------------------------------------------------------------------


def fake_office(monkeypatch, *, code=0, output: bytes | None = PDF):
    """Stand in for LibreOffice: it writes `output` where the real one would."""

    def run(command, **kwargs):
        out = Path(command[command.index("--outdir") + 1])
        source = Path(command[-1])
        if output is not None:
            (out / (source.stem + ".pdf")).write_bytes(output)
        return subprocess.CompletedProcess(command, code, b"", b"boom")

    monkeypatch.setattr(convert.subprocess, "run", run)


def test_the_converter_returns_the_pdf_libreoffice_made(monkeypatch, tmp_path) -> None:
    fake_office(monkeypatch)
    source = tmp_path / "document.docx"
    source.write_bytes(docx())
    assert convert.to_pdf(source, tmp_path, max_bytes=10_000_000) == PDF


@pytest.mark.parametrize(
    ("code", "output", "says"),
    [
        (1, PDF, "exit 1"),
        (0, None, "exit 0"),
        (0, b"not a pdf", "isn't a PDF"),
        (0, PDF, "larger than"),
    ],
)
def test_the_converter_refuses_what_isnt_a_good_pdf(
    monkeypatch, tmp_path, code, output, says
) -> None:
    fake_office(monkeypatch, code=code, output=output)
    source = tmp_path / "document.docx"
    source.write_bytes(docx())
    limit = 10 if says == "larger than" else 10_000_000
    with pytest.raises(convert.ConversionFailed, match=says):
        convert.to_pdf(source, tmp_path, max_bytes=limit)


def test_a_timeout_is_a_failure_not_a_crash(monkeypatch, tmp_path) -> None:
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    monkeypatch.setattr(convert.subprocess, "run", run)
    source = tmp_path / "document.docx"
    source.write_bytes(docx())
    with pytest.raises(convert.ConversionFailed):
        convert.to_pdf(source, tmp_path, max_bytes=1000)


def attach_docx(world, *, django_capture_on_commit_callbacks):
    from tests.files_world import attach

    with django_capture_on_commit_callbacks(execute=True):
        return attach(
            world.appointment, world.student, by=world.student, name="essay.docx", data=docx()
        )


def test_documents_get_no_copy_unless_the_installation_converts(
    world, django_capture_on_commit_callbacks
) -> None:
    attached = attach_docx(
        world, django_capture_on_commit_callbacks=django_capture_on_commit_callbacks
    )
    attached.file.refresh_from_db()
    assert attached.file.pdf_status == PdfStatus.NONE and attached.file.viewable_name == ""


def test_a_document_gets_a_pdf_copy_from_the_worker(
    world, settings, monkeypatch, django_capture_on_commit_callbacks
) -> None:
    settings.SYMPRAXI_CONVERT_DOCUMENTS = True
    fake_office(monkeypatch)
    attached = attach_docx(
        world, django_capture_on_commit_callbacks=django_capture_on_commit_callbacks
    )
    attached.file.refresh_from_db()
    assert attached.file.pdf_status == PdfStatus.READY
    assert attached.file.viewable_name == attached.file.pdf_name != attached.file.storage_name
    assert services.storage().open(attached.file.pdf_name).read() == PDF


def test_a_failed_conversion_leaves_the_original_alone(
    world, settings, monkeypatch, django_capture_on_commit_callbacks
) -> None:
    settings.SYMPRAXI_CONVERT_DOCUMENTS = True
    fake_office(monkeypatch, code=1)
    attached = attach_docx(
        world, django_capture_on_commit_callbacks=django_capture_on_commit_callbacks
    )
    attached.file.refresh_from_db()
    assert attached.file.pdf_status == PdfStatus.FAILED
    assert attached.file.viewable_name == "" and services.storage().exists(
        attached.file.storage_name
    )


def test_a_stalled_conversion_is_asked_for_again(
    world, settings, monkeypatch, django_capture_on_commit_callbacks
) -> None:
    from datetime import timedelta

    from django.utils import timezone

    from sympraxi.files.models import StoredFile

    settings.SYMPRAXI_CONVERT_DOCUMENTS = True
    attached = attach_docx(
        world, django_capture_on_commit_callbacks=django_capture_on_commit_callbacks
    )
    StoredFile.objects.filter(pk=attached.file_id).update(
        pdf_status=PdfStatus.PENDING, uploaded_at=timezone.now() - timedelta(minutes=10)
    )
    fake_office(monkeypatch)
    assert services.retry_scans() == 1
    attached.file.refresh_from_db()
    assert attached.file.pdf_status == PdfStatus.READY


def test_pdfs_are_their_own_viewable_copy(world) -> None:
    assert world.claimed_file.file.viewable_name == world.claimed_file.file.storage_name


# --- The viewer ------------------------------------------------------------------------


def test_the_tutor_opens_the_viewer_with_the_tools_to_comment(client, world) -> None:
    client.force_login(world.tutor)
    page = client.get(route("viewer", world, world.claimed_file)).content.decode()
    assert 'id="viewer-config"' in page and "Comment on the selected text" in page
    assert AuditEvent.objects.filter(action=AuditAction.FILE_VIEWED).exists()


def test_the_pdf_is_a_download_no_browser_runs(client, world) -> None:
    client.force_login(world.tutor)
    response = client.get(route("pdf", world, world.claimed_file))
    assert response.status_code == 200 and b"".join(response.streaming_content) == PDF
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert (
        response["X-Content-Type-Options"] == "nosniff" and "no-store" in response["Cache-Control"]
    )


def test_a_document_with_no_copy_says_why(client, world) -> None:
    data = docx()
    attached = feedback_services.add_file(
        world.claimed,
        by=world.student,
        role=FileRole.DRAFT,
        upload=(io.BytesIO(data), "essay.docx", len(data)),
    )
    client.force_login(world.tutor)
    page = client.get(route("viewer", world, attached)).content.decode()
    assert "Only PDF files can be shown here." in page
    assert client.get(route("pdf", world, attached)).status_code == 404


def test_other_people_cant_open_the_viewer(client, world) -> None:
    waiting = world.submission_file
    client.force_login(world.tutor)  # the tutor doesn't have this one
    assert client.get(route("viewer", world, waiting)).status_code == 404
    client.force_login(make_member(world.center, Role.STUDENT))
    assert client.get(route("pdf", world, waiting)).status_code == 404
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(route("viewer", world, waiting)).status_code == 403


# --- Comments ----------------------------------------------------------------------------


def test_the_tutor_comments_on_a_draft_they_have(client, world) -> None:
    client.force_login(world.tutor)
    response = post_comment(client, world)
    assert response.status_code == 201
    made = Comment.objects.get()
    assert (made.page, made.quote, made.body, made.author) == (
        1,
        "The thesis",
        "Cite it.",
        world.tutor,
    )
    assert made.rects == [[0.1, 0.2, 0.3, 0.02]]
    assert response.json()["mine"] is True
    assert AuditEvent.objects.filter(action=AuditAction.COMMENT_ADDED).exists()
    listed = client.get(route("comments", world, world.claimed_file)).json()["comments"]
    assert [c["body"] for c in listed] == ["Cite it."]


@pytest.mark.parametrize(
    "fields",
    [
        {"body": "   "},
        {"body": "x" * 2001},
        {"page": 0},
        {"page": "one"},
        {"page": True},
        {"rects": [[0.1, 0.2, 0.3]]},
        {"rects": [["a", 0, 0, 0]]},
        {"rects": [[0, 0, 0, 0]] * 51},
        {"quote": 5},
    ],
)
def test_a_malformed_comment_is_refused(client, world, fields) -> None:
    client.force_login(world.tutor)
    assert post_comment(client, world, **fields).status_code == 400
    assert not Comment.objects.exists()


def test_rects_are_kept_inside_the_page(client, world) -> None:
    client.force_login(world.tutor)
    post_comment(client, world, rects=[[-1, 0.5, 2, 3]])
    assert Comment.objects.get().rects == [[0.0, 0.5, 1.0, 1.0]]


def test_only_the_tutor_who_has_it_comments(client, world) -> None:
    for person in (
        world.student,
        make_member(world.center, Role.TUTOR),
        make_member(world.center, Role.DIRECTOR),
    ):
        client.force_login(person)
        assert post_comment(client, world).status_code in (403, 404)
    assert not Comment.objects.exists()


def test_nobody_comments_on_a_waiting_submission_or_a_returned_one(client, world) -> None:
    client.force_login(world.tutor)
    assert post_comment(client, world, world.submission_file).status_code == 404
    finalize(world)
    assert post_comment(client, world).status_code == 403


def test_the_student_sees_comments_only_after_the_return(client, world) -> None:
    client.force_login(world.tutor)
    post_comment(client, world, body="Cite your source.")
    client.force_login(world.student)
    assert client.get(route("comments", world, world.claimed_file)).json() == {"comments": []}
    page = client.get(route("viewer", world, world.claimed_file)).content.decode()
    assert "Cite your source." not in page and "appear here once your feedback is returned" in page
    finalize(world)
    world.claimed.refresh_from_db()
    assert world.claimed.status == SubmissionStatus.RETURNED
    page = client.get(route("viewer", world, world.claimed_file)).content.decode()
    assert "Cite your source." in page and "Comment on the selected text" not in page
    assert [
        c["body"]
        for c in client.get(route("comments", world, world.claimed_file)).json()["comments"]
    ] == ["Cite your source."]


def test_directors_read_comments_at_any_time(client, world) -> None:
    client.force_login(world.tutor)
    post_comment(client, world)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert len(client.get(route("comments", world, world.claimed_file)).json()["comments"]) == 1


def test_a_tutor_removes_their_own_comment_until_the_return(client, world) -> None:
    client.force_login(world.tutor)
    comment_id = post_comment(client, world).json()["id"]
    other = make_member(world.center, Role.TUTOR)
    client.force_login(other)
    assert (
        client.delete(
            route("comments", world, world.claimed_file) + f"?comment={comment_id}"
        ).status_code
        == 404
    )
    client.force_login(world.tutor)
    response = client.delete(
        route("comments", world, world.claimed_file) + f"?comment={comment_id}"
    )
    assert response.status_code == 200 and not Comment.objects.exists()
    assert AuditEvent.objects.filter(action=AuditAction.COMMENT_REMOVED).exists()
    comment_id = post_comment(client, world).json()["id"]
    finalize(world)
    response = client.delete(
        route("comments", world, world.claimed_file) + f"?comment={comment_id}"
    )
    assert response.status_code == 403 and Comment.objects.exists()


def test_comments_are_listed_on_the_file_page_link(client, world) -> None:
    client.force_login(world.tutor)
    page = client.get(
        reverse(
            "feedback:detail", kwargs={"slug": world.center.slug, "submission_id": world.claimed.pk}
        )
    ).content.decode()
    assert "Read and comment" in page


# --- Exports ------------------------------------------------------------------------------


def test_the_student_export_includes_the_comments(client, world) -> None:
    client.force_login(world.tutor)
    post_comment(client, world, body="Cite your source.")
    membership = Membership.objects.get(center=world.center, user=world.student, role=Role.STUDENT)
    data = export.build(membership, exported_by=make_member(world.center, Role.DIRECTOR))
    entry = next(s for s in data["submissions"] if s["title"] == "Essay 1")
    [comments] = [f["comments"] for f in entry["files"]]
    assert comments[0]["body"] == "Cite your source." and comments[0]["page"] == 1
