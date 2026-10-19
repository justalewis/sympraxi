"""Turning a document into a PDF with LibreOffice, in the task worker.

The comment viewer draws PDFs, so Word, OpenDocument, rich text and plain text
files get a PDF copy made by `soffice --headless`. The original stays as it
was, and downloads still give the original.

The file is untrusted, so the conversion is kept small. It runs in a fresh
temporary folder holding the file under a fixed name (never the person's),
with its own LibreOffice profile and a minimal environment, and is killed after
`TIMEOUT` seconds. The result is used only if it starts with the PDF marker and is
not larger than the upload limit. The worker's network is not cut off, so a
document that links to something outside could make LibreOffice fetch it; see
decisions.md, "Phase 2: comments in the browser".
"""

import logging
import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

logger = logging.getLogger("sympraxi.files")

TIMEOUT = 120


class ConversionFailed(Exception):
    """LibreOffice couldn't produce a PDF. The message is for the log, not for people."""


@contextmanager
def workspace() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="sympraxi-convert-") as name:
        yield Path(name)


def to_pdf(source: Path, directory: Path, *, max_bytes: int) -> bytes:
    """Convert one file and return the PDF's bytes."""
    out = directory / "out"
    out.mkdir()
    command = [
        settings.SYMPRAXI_OFFICE_COMMAND,
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        f"-env:UserInstallation={(directory / 'profile').as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(out),
        str(source),
    ]
    environment = {"PATH": os.environ.get("PATH", ""), "HOME": str(directory), "LANG": "C.UTF-8"}
    try:
        result = subprocess.run(  # noqa: S603 (a fixed command; the file's name is ours)
            command,
            capture_output=True,
            timeout=TIMEOUT,
            cwd=directory,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConversionFailed(
            f"couldn't run {settings.SYMPRAXI_OFFICE_COMMAND}: {error}"
        ) from error
    produced = out / (source.stem + ".pdf")
    if result.returncode != 0 or not produced.is_file():
        tail = result.stderr.decode("utf-8", "replace")[-300:]
        raise ConversionFailed(f"exit {result.returncode}: {tail}")
    if produced.stat().st_size > max_bytes:
        raise ConversionFailed("the PDF is larger than the upload limit")
    data = produced.read_bytes()
    if not data.startswith(b"%PDF"):
        raise ConversionFailed("the output isn't a PDF")
    return data
