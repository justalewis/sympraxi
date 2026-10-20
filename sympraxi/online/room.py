"""The shared document of a session: who may open it, how it is kept, and how it is shown.

A center turns it on, and the installation runs the editor service (`collab/` in the
repository, Hocuspocus on Yjs) with a secret that Sympraxi and the service share
(`SYMPRAXI_COLLAB_SECRET`). The tutor and the students in an appointment open the
appointment's room page, where Sympraxi hands each person a token (an HS256 JSON Web
Token) for one document, named from the appointment. The editor service accepts only
that token. It holds the live copy, and loads and saves it through Sympraxi's internal
address (`internal_load` and `internal_store`, which need the shared secret).

What is saved is Yjs's own state, which only the editor service understands, and the same
content as a ProseMirror document, which Sympraxi stores, shows, versions and exports.
Sympraxi never trusts that content: it is checked for shape and size when it is saved and
drawn through `render()`, which writes only the few elements the editor makes and
escapes every piece of text, so what a student types can't become markup.
"""

import base64
import binascii
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString, mark_safe

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.core.policy import Action, can
from sympraxi.online import jwt
from sympraxi.online.models import RoomDocument, RoomVersion, VideoSettings
from sympraxi.scheduling.models import Appointment

# The room opens half an hour before the start and keeps taking edits for three hours
# after the end, for notes written up afterwards. Outside that it is read-only.
OPENS_BEFORE = timedelta(minutes=30)
CLOSES_AFTER = timedelta(hours=3)
# An automatic version is saved when the document changed and the last one is this old.
AUTO_VERSION_AFTER = timedelta(minutes=5)

MAX_STATE_BYTES = 20 * 1024 * 1024
MAX_CONTENT_BYTES = 2 * 1024 * 1024
MAX_CHAT = 500
MAX_DEPTH = 30

EARLY = "early"
OPEN = "open"
OVER = "over"

TOKEN_AUDIENCE = "sympraxi-collab"  # noqa: S105 (a name, not a secret)


def configured() -> bool:
    """Whether the installation runs the editor service."""
    return bool(settings.SYMPRAXI_COLLAB_SECRET)


def room_on(center: Center) -> bool:
    return configured() and VideoSettings.objects.filter(center=center, room_is_on=True).exists()


def document_name(appointment: Appointment) -> str:
    return f"appointment:{appointment.pk}"


def has_room(appointment: Appointment) -> bool:
    return (
        appointment.is_active
        and not appointment.is_placeholder
        and room_on(appointment.schedule.center)
    )


def window(appointment: Appointment) -> tuple[datetime, datetime]:
    return appointment.starts_at - OPENS_BEFORE, appointment.ends_at + CLOSES_AFTER


def state(appointment: Appointment, now: datetime | None = None) -> str:
    now = now or timezone.now()
    opens, closes = window(appointment)
    if now < opens:
        return EARLY
    return OPEN if now <= closes else OVER


def token(appointment: Appointment, user: User, *, now: datetime | None = None) -> str:
    """A token that lets this person into this appointment's document, and no other."""
    now = now or timezone.now()
    opens, closes = window(appointment)
    claims: dict[str, Any] = {
        "aud": TOKEN_AUDIENCE,
        "doc": document_name(appointment),
        "sub": str(user.pk),
        "name": user.display_name,
        "role": "tutor" if appointment.tutor_id == user.pk else "student",
        "nbf": int(min(opens, now).timestamp()),
        "exp": int(closes.timestamp()),
    }
    return jwt.encode(claims, settings.SYMPRAXI_COLLAB_SECRET)


def collab_url(host: str, *, secure: bool) -> str:
    """Where the browser connects: the configured address, or /collab/ on this host."""
    if settings.SYMPRAXI_COLLAB_URL:
        return str(settings.SYMPRAXI_COLLAB_URL)
    return f"{'wss' if secure else 'ws'}://{host}/collab/"


# --- The document ---------------------------------------------------------------------


def document_for(appointment: Appointment) -> RoomDocument | None:
    return RoomDocument.objects.filter(appointment=appointment).first()


def appointment_named(name: str) -> Appointment | None:
    prefix = "appointment:"
    if not name.startswith(prefix):
        return None
    try:
        pk = UUID(name[len(prefix) :])
    except ValueError:
        return None
    return Appointment.objects.select_related("schedule__center").filter(pk=pk).first()


class Refused(Exception):
    """What the editor service sent isn't something to keep."""


def _check_node(node: Any, depth: int, count: list[int]) -> None:
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > 50000 or not isinstance(node, dict):
        raise Refused("content too deep or too large")
    if not isinstance(node.get("type"), str):
        raise Refused("a node has no type")
    children = node.get("content", [])
    if not isinstance(children, list):
        raise Refused("content isn't a list")
    for child in children:
        _check_node(child, depth + 1, count)


def clean_content(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict) or content.get("type") != "doc":
        raise Refused("the content isn't a document")
    _check_node(content, 0, [0])
    if len(repr(content)) > MAX_CONTENT_BYTES:
        raise Refused("the content is too large")
    return content


def clean_chat(chat: Any) -> list[dict[str, Any]]:
    if not isinstance(chat, list):
        raise Refused("the chat isn't a list")
    kept = []
    for message in chat[-MAX_CHAT:]:
        if not isinstance(message, dict):
            continue
        kept.append(
            {
                "id": str(message.get("id", ""))[:60],
                "name": str(message.get("name", ""))[:100],
                "text": str(message.get("text", ""))[:1000],
                "at": str(message.get("at", ""))[:40],
            }
        )
    return kept


def load_state(name: str) -> bytes | None:
    appointment = appointment_named(name)
    if appointment is None:
        return None
    found = document_for(appointment)
    return bytes(found.state) if found and found.state else None


def save(
    name: str, state_b64: str, content: Any, chat: Any, *, now: datetime | None = None
) -> bool:
    """Keep what the editor service sends. Returns whether anything was saved."""
    now = now or timezone.now()
    appointment = appointment_named(name)
    if appointment is None or not has_room(appointment):
        return False
    try:
        state_bytes = base64.b64decode(state_b64, validate=True)
    except (binascii.Error, ValueError, TypeError) as error:
        raise Refused("the state isn't base64") from error
    if len(state_bytes) > MAX_STATE_BYTES:
        raise Refused("the state is too large")
    document_content = clean_content(content)
    messages = clean_chat(chat)
    with transaction.atomic():
        document, _created = RoomDocument.objects.select_for_update().get_or_create(
            appointment=appointment
        )
        document.state = state_bytes
        document.content = document_content
        document.chat = messages
        document.save()
        _automatic_version(document, now)
    return True


def _automatic_version(document: RoomDocument, now: datetime) -> None:
    last = document.versions.order_by("-created_at").first()
    if last is not None and now - last.created_at < AUTO_VERSION_AFTER:
        return
    if last is not None:
        # Versions made in the same instant are all "the latest": none may match.
        latest = document.versions.filter(created_at=last.created_at)
        if any(v.content == document.content and v.chat == document.chat for v in latest):
            return
    RoomVersion.objects.create(
        document=document, label="", content=document.content, chat=document.chat
    )


def save_version(appointment: Appointment, by: User) -> RoomVersion | None:
    """Keep the document as it is now, labelled with who asked. None if there is nothing yet."""
    with transaction.atomic():
        document = RoomDocument.objects.select_for_update().filter(appointment=appointment).first()
        if document is None or not text_of(document.content).strip():
            return None
        return RoomVersion.objects.create(
            document=document, label=by.display_name, content=document.content, chat=document.chat
        )


def secret_matches(given: str) -> bool:
    return bool(settings.SYMPRAXI_COLLAB_SECRET) and hmac.compare_digest(
        given.encode(), settings.SYMPRAXI_COLLAB_SECRET.encode()
    )


def may_use(user: User, appointment: Appointment) -> bool:
    return has_room(appointment) and can(user, Action.USE_ROOM, appointment)


# --- Showing the document ---------------------------------------------------------------

BLOCKS = {
    "paragraph": "p",
    "blockquote": "blockquote",
    "bulletList": "ul",
    "orderedList": "ol",
    "listItem": "li",
}
MARKS = {"bold": "strong", "italic": "em", "strike": "s", "underline": "u", "code": "code"}


def _text(node: dict[str, Any]) -> SafeString:
    out: SafeString = format_html("{}", str(node.get("text", "")))
    marks = node.get("marks", [])
    for mark in marks if isinstance(marks, list) else []:
        tag = MARKS.get(str(mark.get("type"))) if isinstance(mark, dict) else None
        if tag:
            out = format_html("<{0}>{1}</{0}>", mark_safe(tag), out)  # noqa: S308 (fixed tag names)
    return out


def _children(node: dict[str, Any], depth: int) -> SafeString:
    children = node.get("content", [])
    return format_html_join(
        "", "{}", ((_node(c, depth + 1),) for c in children if isinstance(c, dict))
    )


def _node(node: dict[str, Any], depth: int = 0) -> SafeString:
    if depth > MAX_DEPTH:
        return mark_safe("")
    kind = node.get("type")
    if kind == "text":
        return _text(node)
    if kind == "hardBreak":
        return mark_safe("<br>")
    if kind == "horizontalRule":
        return mark_safe("<hr>")
    if kind == "heading":
        attrs = node.get("attrs", {})
        level = attrs.get("level", 1) if isinstance(attrs, dict) else 1
        level = level if isinstance(level, int) and 1 <= level <= 4 else 1
        tag = f"h{level + 2}"
        return format_html("<{0}>{1}</{0}>", mark_safe(tag), _children(node, depth))  # noqa: S308
    if kind == "codeBlock":
        return format_html("<pre><code>{}</code></pre>", _children(node, depth))
    tag_name = BLOCKS.get(str(kind))
    if tag_name:
        return format_html("<{0}>{1}</{0}>", mark_safe(tag_name), _children(node, depth))  # noqa: S308
    return _children(node, depth)  # an element we don't know: keep its text, drop the markup


def render(content: Any) -> SafeString:
    """The document as HTML: only elements the editor makes, with every text escaped."""
    if not isinstance(content, dict):
        return mark_safe("")
    return _children(content, 0)


def text_of(content: Any) -> str:
    """The document's words, one block per line."""
    lines: list[str] = []

    def walk(node: Any, depth: int = 0) -> str:
        if not isinstance(node, dict) or depth > MAX_DEPTH:
            return ""
        if node.get("type") == "text":
            return str(node.get("text", ""))
        children = node.get("content", [])
        pieces = [walk(c, depth + 1) for c in children if isinstance(c, dict)]
        if node.get("type") in ("paragraph", "heading", "codeBlock"):
            lines.append("".join(pieces))
            return ""
        return "".join(pieces)

    walk(content)
    return "\n".join(lines)


@dataclass
class Chat:
    name: str
    text: str
    at: str


def chat_of(messages: Any) -> list[Chat]:
    return [
        Chat(str(m.get("name", "")), str(m.get("text", "")), str(m.get("at", "")))
        for m in messages
        if isinstance(m, dict)
    ]
