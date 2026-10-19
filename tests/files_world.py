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


def text_pdf(*lines: str) -> bytes:
    """A real one-page PDF with the lines in Helvetica, small enough to write by hand."""
    stream = "BT /F1 18 Tf 72 700 Td " + " 0 -28 Td ".join(f"({line}) Tj" for line in lines) + " ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    size = len(objects) + 1
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


PDF = text_pdf("The thesis of this draft is clear.", "The second sentence needs a source.")
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
