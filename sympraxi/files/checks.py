"""What an uploaded file is, from its content, and whether to accept it.

Only writing a center works with is accepted: PDF, Word (.docx and the older
.doc), OpenDocument text, rich text and plain text. The name's extension must
match what the content turns out to be, so a program renamed to "essay.pdf" is
refused. Word and OpenDocument files are ZIP archives; they're opened to check
they hold a document, that a .docx carries no macros, and that unpacking them
wouldn't fill the disk (a "zip bomb"). The older .doc can carry macros and isn't
opened here, which is what the virus scanner is for.
"""

import re
import unicodedata
import zipfile
from typing import IO

from django.utils.translation import gettext as _

from sympraxi.files.models import FileKind

EXTENSIONS = {
    ".pdf": FileKind.PDF,
    ".docx": FileKind.DOCX,
    ".doc": FileKind.DOC,
    ".odt": FileKind.ODT,
    ".rtf": FileKind.RTF,
    ".txt": FileKind.TXT,
}

CONTENT_TYPES = {
    FileKind.PDF: "application/pdf",
    FileKind.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    FileKind.DOC: "application/msword",
    FileKind.ODT: "application/vnd.oasis.opendocument.text",
    FileKind.RTF: "application/rtf",
    FileKind.TXT: "text/plain; charset=utf-8",
}

OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
ODT_MIMETYPE = b"application/vnd.oasis.opendocument.text"
# Limits on what a ZIP-based document may unpack to.
MAX_UNPACKED = 250 * 1024 * 1024
MAX_ENTRIES = 3000
MAX_RATIO = 200


class Refused(ValueError):
    """The file can't be accepted; the message says why, for the person."""


def clean_name(name: str) -> str:
    """A display name: no folders, no control characters, at most 200 characters."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "file"
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: 199 - len(ext)] + dot + ext) if dot and len(ext) <= 10 else name[:200]
    return name


def extension_kind(name: str) -> FileKind:
    lowered = name.lower()
    for ext, kind in EXTENSIONS.items():
        if lowered.endswith(ext):
            return kind
    raise Refused(_("Sympraxi accepts PDF, Word, OpenDocument, rich text and plain text files."))


def _check_zip(handle: IO[bytes], kind: FileKind) -> None:
    try:
        archive = zipfile.ZipFile(handle)
    except zipfile.BadZipFile as error:
        raise Refused(
            _("The file is damaged or isn't really a %(kind)s.") % {"kind": kind.label}
        ) from error
    with archive:
        entries = archive.infolist()
        if len(entries) > MAX_ENTRIES:
            raise Refused(_("The file holds too many parts to be a document."))
        unpacked = sum(e.file_size for e in entries)
        if unpacked > MAX_UNPACKED or any(
            e.file_size > MAX_RATIO * max(e.compress_size, 1) and e.file_size > 1024 * 1024
            for e in entries
        ):
            raise Refused(_("The file unpacks to far more than its size, so it was refused."))
        names = {e.filename for e in entries}
        if kind == FileKind.DOCX:
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise Refused(_("The file isn't really a Word document."))
            types = archive.read("[Content_Types].xml")
            if b"macroEnabled" in types or any(n.lower().endswith("vbaproject.bin") for n in names):
                raise Refused(
                    _("The Word file has macros. Save it as a regular .docx and try again.")
                )
        elif kind == FileKind.ODT:
            if "mimetype" not in names or archive.read("mimetype").strip() != ODT_MIMETYPE:
                raise Refused(_("The file isn't really an OpenDocument text file."))
            if any(n.lower().startswith("basic/") for n in names):
                raise Refused(_("The file has macros. Save it without them and try again."))


def identify(name: str, handle: IO[bytes]) -> FileKind:
    """The file's kind, checked against its content. Raises Refused."""
    kind = extension_kind(name)
    handle.seek(0)
    head = handle.read(64 * 1024)
    handle.seek(0)
    if not head:
        raise Refused(_("The file is empty."))
    if kind == FileKind.PDF:
        ok = head.startswith(b"%PDF-")
    elif kind == FileKind.DOC:
        ok = head.startswith(OLE_MAGIC)
    elif kind == FileKind.RTF:
        ok = head.startswith(b"{\\rtf")
    elif kind == FileKind.TXT:
        ok = b"\x00" not in head
        if ok:
            try:
                head.decode("utf-8")
            except UnicodeDecodeError as error:
                # A cut-off character at the end of the sample is fine.
                ok = error.start >= len(head) - 4
    else:
        ok = head.startswith(b"PK\x03\x04")
        if ok:
            _check_zip(handle, kind)
            handle.seek(0)
    if not ok:
        raise Refused(
            _("The file's content isn't a %(kind)s, though its name says it is.")
            % {"kind": kind.label}
        )
    return kind
