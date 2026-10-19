"""Files on appointments: what's accepted, who opens what, downloads that can't run,
virus scans with ClamAV, removal, and the exports."""

import io
import socketserver
import threading
from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Membership, Role
from sympraxi.files import checks, services
from sympraxi.files.models import AppointmentFile, FileKind, ScanStatus, StoredFile
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Participant
from tests.files_world import DOC, PDF, RTF, TXT, attach, docx, odt, zip_bomb
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    return build_world(center)


def page(world) -> str:
    return reverse(
        "scheduling:appointment",
        kwargs={"slug": world.center.slug, "appointment_id": world.appointment.pk},
    )


def upload_url(world) -> str:
    return reverse(
        "files:upload",
        kwargs={"slug": world.center.slug, "appointment_id": world.appointment.pk},
    )


def download_url(world, attached) -> str:
    return reverse("files:download", kwargs={"slug": world.center.slug, "file_id": attached.pk})


def post_file(client, world, name: str, data: bytes, **fields):
    upload = SimpleUploadedFile(name, data)
    return client.post(upload_url(world), {"file": upload, "note": "", **fields})


# --- What's accepted ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "data", "kind"),
    [
        ("essay.pdf", PDF, FileKind.PDF),
        ("essay.docx", docx(), FileKind.DOCX),
        ("essay.DOC", DOC, FileKind.DOC),
        ("essay.odt", odt(), FileKind.ODT),
        ("essay.rtf", RTF, FileKind.RTF),
        ("notes.txt", TXT, FileKind.TXT),
    ],
)
def test_each_kind_of_writing_is_accepted(name, data, kind) -> None:
    assert checks.identify(name, io.BytesIO(data)) == kind


@pytest.mark.parametrize(
    ("name", "data", "says"),
    [
        ("essay.pdf", b"MZ\x90\x00 a program", "isn't a PDF"),
        ("essay.exe", b"MZ\x90\x00", "Sympraxi accepts"),
        ("essay.docx", PDF, "isn't a Word document"),
        ("essay.docx", docx(macros=True), "has macros"),
        ("essay.docx", zip_bomb(), "unpacks to far more"),
        ("essay.txt", b"text\x00with a NUL", "isn't a Plain text"),
        ("essay.pdf", b"", "is empty"),
    ],
)
def test_files_that_arent_what_they_say_are_refused(name, data, says) -> None:
    with pytest.raises(checks.Refused, match=says):
        checks.identify(name, io.BytesIO(data))


def test_names_are_cleaned_up() -> None:
    assert checks.clean_name("C:\\Users\\sam\\Draft\x07 two.pdf") == "Draft two.pdf"
    assert checks.clean_name("../../etc/passwd.txt") == "passwd.txt"
    assert checks.clean_name("   ") == "file"
    assert len(checks.clean_name("a" * 300 + ".docx")) == 200


def test_files_over_the_limit_are_refused(client, world, settings) -> None:
    settings.SYMPRAXI_UPLOAD_MAX_MB = 1
    client.force_login(world.student)
    response = post_file(client, world, "big.txt", b"a" * (1024 * 1024 + 1))
    assert "larger than 1 MB" in response.content.decode()
    assert not StoredFile.objects.exists()


# --- Attaching and opening ----------------------------------------------------------


def test_a_student_attaches_a_draft_and_opens_it(client, world) -> None:
    client.force_login(world.student)
    response = post_file(client, world, "My draft.pdf", PDF, note="Second draft")
    assert response.status_code == 302
    attached = AppointmentFile.objects.get()
    assert (attached.student, attached.file.uploaded_by) == (world.student, world.student)
    assert attached.file.scan_status == ScanStatus.NOT_SCANNED  # no scanner in tests
    assert attached.file.storage_name and "My draft" not in attached.file.storage_name
    assert "My draft.pdf" in client.get(page(world)).content.decode()

    download = client.get(download_url(world, attached))
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment;")
    assert download["Content-Security-Policy"] == "sandbox; default-src 'none'"
    assert "no-store" in download["Cache-Control"]
    assert b"".join(download.streaming_content) == PDF
    actions = set(AuditEvent.objects.values_list("action", flat=True))
    assert {AuditAction.FILE_UPLOADED, AuditAction.FILE_DOWNLOADED} <= actions


def test_a_refused_file_says_why_and_keeps_nothing(client, world) -> None:
    client.force_login(world.student)
    response = post_file(client, world, "essay.pdf", b"MZ not a pdf")
    assert response.status_code == 200
    assert "isn&#x27;t a PDF" in response.content.decode()
    assert not AppointmentFile.objects.exists()


def test_in_a_group_each_students_files_are_theirs(client, world) -> None:
    other = make_member(world.center, Role.STUDENT, full_name="Other Student")
    Appointment.objects.filter(pk=world.appointment.pk).update(capacity=2)
    Participant.objects.create(appointment=world.appointment, student=other)
    mine = attach(world.appointment, world.student, by=world.student, name="mine.pdf")
    client.force_login(other)
    assert "mine.pdf" not in client.get(page(world)).content.decode()
    assert client.get(download_url(world, mine)).status_code == 404


def test_tutors_and_directors_open_files_but_front_desk_staff_dont(client, world) -> None:
    attached = attach(world.appointment, world.student, by=world.student)
    client.force_login(world.tutor)
    assert client.get(download_url(world, attached)).status_code == 200
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(download_url(world, attached)).status_code == 200
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(download_url(world, attached)).status_code == 403
    assert "draft.pdf" not in client.get(page(world)).content.decode()


def test_a_tutor_attaches_a_file_for_a_student_in_a_group(client, world) -> None:
    other = make_member(world.center, Role.STUDENT, full_name="Other Student")
    Appointment.objects.filter(pk=world.appointment.pk).update(capacity=2)
    Participant.objects.create(appointment=world.appointment, student=other)
    client.force_login(world.tutor)
    post_file(client, world, "comments.docx", docx(), student=str(other.pk))
    assert AppointmentFile.objects.get().student == other


def test_nothing_can_be_attached_to_a_canceled_appointment(client, world) -> None:
    Appointment.objects.filter(pk=world.appointment.pk).update(status=AppointmentStatus.CANCELED)
    client.force_login(world.student)
    post_file(client, world, "essay.pdf", PDF)
    assert not AppointmentFile.objects.exists()


# --- Virus scans --------------------------------------------------------------------


class FakeClamd(socketserver.BaseRequestHandler):
    answer = b"stream: OK\0"

    def handle(self) -> None:
        command = self.request.recv(10)
        assert command == b"zINSTREAM\0"
        while True:
            size = int.from_bytes(_recv_exactly(self.request, 4), "big")
            if size == 0:
                break
            _recv_exactly(self.request, size)
        self.request.sendall(self.answer)


def _recv_exactly(sock, n: int) -> bytes:
    data = b""
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            break
        data += part
    return data


@pytest.fixture
def clamd(settings) -> Iterator[type[FakeClamd]]:
    server = socketserver.TCPServer(("127.0.0.1", 0), FakeClamd)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    settings.SYMPRAXI_CLAMAV_ADDRESS = f"127.0.0.1:{server.server_address[1]}"
    yield FakeClamd
    server.shutdown()
    server.server_close()
    FakeClamd.answer = b"stream: OK\0"


def test_a_clean_scan_makes_the_file_ready(
    client, world, clamd, django_capture_on_commit_callbacks
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        attached = attach(world.appointment, world.student, by=world.student)
    attached.file.refresh_from_db()
    assert attached.file.scan_status == ScanStatus.CLEAN
    client.force_login(world.student)
    assert client.get(download_url(world, attached)).status_code == 200


def test_a_flagged_file_is_deleted_and_cant_be_opened(
    client, world, clamd, django_capture_on_commit_callbacks
) -> None:
    clamd.answer = b"stream: Eicar-Test-Signature FOUND\0"
    with django_capture_on_commit_callbacks(execute=True):
        attached = attach(world.appointment, world.student, by=world.student)
    attached.file.refresh_from_db()
    assert attached.file.scan_status == ScanStatus.INFECTED
    assert attached.file.scan_detail == "Eicar-Test-Signature"
    assert attached.file.storage_name == ""
    assert AuditEvent.objects.filter(action=AuditAction.FILE_INFECTED).exists()
    client.force_login(world.student)
    response = client.get(download_url(world, attached))
    assert response.status_code == 409
    assert "virus scanner flagged" in response.content.decode()


def test_a_pending_file_cant_be_opened(client, world, settings) -> None:
    settings.SYMPRAXI_CLAMAV_ADDRESS = "127.0.0.1:9"
    attached = attach(world.appointment, world.student, by=world.student)  # not scanned yet
    assert attached.file.scan_status == ScanStatus.PENDING
    client.force_login(world.student)
    assert client.get(download_url(world, attached)).status_code == 409


def test_an_unreachable_scanner_is_tried_again_later(world, settings) -> None:
    settings.SYMPRAXI_CLAMAV_ADDRESS = "127.0.0.1:9"  # nothing listens on the discard port
    attached = attach(world.appointment, world.student, by=world.student)
    assert services.scan(attached.file_id) == ScanStatus.ERROR
    StoredFile.objects.filter(pk=attached.file_id).update(
        uploaded_at=timezone.now() - timedelta(minutes=10)
    )
    assert services.retry_scans() == 1
    StoredFile.objects.filter(pk=attached.file_id).update(scan_attempts=services.MAX_ATTEMPTS)
    assert services.retry_scans() == 0


# --- Removing -----------------------------------------------------------------------


def remove_url(world, attached) -> str:
    return reverse("files:remove", kwargs={"slug": world.center.slug, "file_id": attached.pk})


def test_the_person_who_attached_it_removes_it(client, world) -> None:
    attached = attach(world.appointment, world.student, by=world.student)
    name = attached.file.storage_name
    client.force_login(world.student)
    client.post(remove_url(world, attached))
    attached.file.refresh_from_db()
    assert attached.file.removed_at is not None and attached.file.storage_name == ""
    assert not services.storage().exists(name)
    assert AppointmentFile.objects.filter(pk=attached.pk).exists()  # the row stays
    assert client.get(download_url(world, attached)).status_code == 409


def test_a_tutor_cant_remove_a_students_file_but_a_director_can(client, world) -> None:
    attached = attach(world.appointment, world.student, by=world.student)
    client.force_login(world.tutor)
    assert (
        "Only the person who attached it"
        in client.get(remove_url(world, attached)).content.decode()
    )
    client.post(remove_url(world, attached))
    attached.file.refresh_from_db()
    assert attached.file.removed_at is None
    client.force_login(make_member(world.center, Role.DIRECTOR))
    client.post(remove_url(world, attached))
    attached.file.refresh_from_db()
    assert attached.file.removed_at is not None


# --- Exports ------------------------------------------------------------------------


def test_the_student_export_lists_their_files(world) -> None:
    attach(world.appointment, world.student, by=world.student, name="essay.pdf")
    membership = Membership.objects.get(center=world.center, user=world.student, role=Role.STUDENT)
    data = export.build(membership, exported_by=make_member(world.center, Role.DIRECTOR))
    [entry] = data["files"]
    assert entry["name"] == "essay.pdf" and entry["size"] == len(PDF)
    assert "storage_name" not in entry
