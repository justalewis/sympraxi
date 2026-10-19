"""Builders for file tests: small real files of each kind, and a file on the world's
appointment."""

import io
import zipfile

from django.test import RequestFactory

from sympraxi.accounts.models import User
from sympraxi.files import services
from sympraxi.files.models import AppointmentFile
from sympraxi.scheduling.models import Appointment
from tests.scheduling_world import World

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
TXT = "Draft one.\nSecond paragraph, with an é.\n".encode()
RTF = b"{\\rtf1\\ansi Draft one.}"
DOC = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 504


def docx(*, macros: bool = False) -> bytes:
    out = io.BytesIO()
    main = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
    types = f'<Types><Override ContentType="{main}"/></Types>'
    if macros:
        types = types.replace("document.main", "document.macroEnabled.main")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", types)
        archive.writestr("word/document.xml", "<w:document><w:body>Draft</w:body></w:document>")
        if macros:
            archive.writestr("word/vbaProject.bin", b"\x00" * 64)
    return out.getvalue()


def odt() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", "<office:document-content/>")
    return out.getvalue()


def zip_bomb() -> bytes:
    """A .docx whose one part unpacks to far more than it takes."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"\x00" * (3 * 1024 * 1024))
    return out.getvalue()


def attach(
    appointment: Appointment, student: User, *, by: User, name: str = "draft.pdf", data: bytes = PDF
) -> AppointmentFile:
    request = RequestFactory().post("/")
    request.user = by
    stored = services.store(
        io.BytesIO(data), name, len(data), center=appointment.schedule.center, uploaded_by=by
    )
    return services.attach(appointment, student, stored, note="", request=request)


def add_files(world: World) -> World:
    """Give the world `appointment_file`: the student's draft on their appointment."""
    world.appointment_file = attach(world.appointment, world.student, by=world.student)
    return world
