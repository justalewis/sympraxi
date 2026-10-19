"""A client for the ClamAV daemon (clamd), speaking its INSTREAM command over TCP.

clamd's protocol is short: send `zINSTREAM\\0`, then the file in chunks, each
prefixed with its length as a 4-byte big-endian number, then four zero bytes;
it answers `stream: OK`, `stream: <signature> FOUND`, or an error, ending in a
NUL. No package needed. Set SYMPRAXI_CLAMAV_ADDRESS to "host:port" to use it.
"""

import socket
import struct
from dataclasses import dataclass
from typing import IO

CHUNK = 64 * 1024
TIMEOUT = 60


class ScannerUnavailable(Exception):
    """clamd couldn't be reached, or answered with an error."""


@dataclass
class Verdict:
    clean: bool
    signature: str = ""


def parse_address(address: str) -> tuple[str, int]:
    host, _colon, port = address.rpartition(":")
    if not host or not port.isdigit():
        raise ScannerUnavailable(f"SYMPRAXI_CLAMAV_ADDRESS should be host:port, not {address!r}")
    return host.strip("[]"), int(port)


def scan(handle: IO[bytes], address: str) -> Verdict:
    host, port = parse_address(address)
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as conn:
            conn.sendall(b"zINSTREAM\0")
            handle.seek(0)
            while chunk := handle.read(CHUNK):
                conn.sendall(struct.pack("!L", len(chunk)) + chunk)
            conn.sendall(struct.pack("!L", 0))
            reply = b""
            while not reply.endswith(b"\0"):
                part = conn.recv(4096)
                if not part:
                    break
                reply += part
    except OSError as error:
        raise ScannerUnavailable(str(error)) from error
    text = reply.rstrip(b"\0").decode("utf-8", errors="replace").strip()
    if text.endswith(" OK"):
        return Verdict(clean=True)
    if text.endswith(" FOUND"):
        signature = text.removeprefix("stream:").removesuffix("FOUND").strip()
        return Verdict(clean=False, signature=signature[:200])
    raise ScannerUnavailable(text or "no answer from clamd")
