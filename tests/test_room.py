"""The shared document of a session: tokens for the editor service, what it may save,
history, what each person sees, and the exports."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Membership, Role
from sympraxi.online import room
from sympraxi.online.models import RoomDocument, RoomVersion, VideoSettings
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export
from tests.records_world import appointment_for
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

SECRET = "a-collab-secret-for-the-tests"


@pytest.fixture
def world(center, settings):
    settings.SYMPRAXI_COLLAB_SECRET = SECRET
    VideoSettings.objects.create(center=center, room_is_on=True)
    return build_world(center)


def session(world, minutes: float = 5, **fields):
    """An appointment that starts in a few minutes, so the room is open."""
    return appointment_for(world, world.student, days=minutes / 1440, **fields)


def doc(*paragraphs: str) -> dict:
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p}]} for p in paragraphs
        ],
    }


STATE = base64.b64encode(b"yjs state").decode()


def call(client, name: str, body: dict, secret: str | None = SECRET):
    headers = {"HTTP_X_COLLAB_SECRET": secret} if secret is not None else {}
    return client.post(
        reverse(f"collab:{name}"), json.dumps(body), content_type="application/json", **headers
    )


def store(client, appointment, content=None, chat=None, state=STATE):
    return call(
        client,
        "store",
        {
            "doc": room.document_name(appointment),
            "state": state,
            "content": content or doc("Hello"),
            "chat": chat or [],
        },
    )


def room_url(world, appointment) -> str:
    return reverse(
        "online:room", kwargs={"slug": world.center.slug, "appointment_id": appointment.pk}
    )


def versions_url(world, appointment) -> str:
    return reverse(
        "online:room_versions", kwargs={"slug": world.center.slug, "appointment_id": appointment.pk}
    )


# --- Tokens -----------------------------------------------------------------------------


def test_a_token_is_for_one_person_and_one_document(world) -> None:
    appointment = session(world)
    token = room.token(appointment, world.student)
    header, payload, signature = token.split(".")
    expected = hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "==") == expected
    claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert claims["aud"] == "sympraxi-collab"
    assert claims["doc"] == f"appointment:{appointment.pk}"
    assert (claims["sub"], claims["role"]) == (str(world.student.pk), "student")
    assert claims["name"] == world.student.display_name
    assert claims["exp"] == int((appointment.ends_at + room.CLOSES_AFTER).timestamp())
    tutor = json.loads(
        base64.urlsafe_b64decode(room.token(appointment, world.tutor).split(".")[1] + "==")
    )
    assert tutor["role"] == "tutor"


def test_the_document_opens_half_an_hour_before_and_stays_three_hours(world) -> None:
    assert room.state(session(world, minutes=90)) == room.EARLY
    assert room.state(session(world, minutes=5)) == room.OPEN
    over = appointment_for(world, world.student, days=-1)
    assert room.state(over) == room.OVER


def test_nothing_exists_without_the_service_or_the_centers_choice(world, settings) -> None:
    appointment = session(world)
    assert room.has_room(appointment)
    VideoSettings.objects.update(room_is_on=False)
    assert not room.has_room(appointment)
    VideoSettings.objects.update(room_is_on=True)
    settings.SYMPRAXI_COLLAB_SECRET = ""
    assert not room.has_room(appointment)


# --- The room page ----------------------------------------------------------------------


def test_a_student_opens_the_room_with_a_token_and_the_service_address(client, world) -> None:
    appointment = session(world)
    client.force_login(world.student)
    response = client.get(room_url(world, appointment))
    page = response.content.decode()
    assert response.status_code == 200 and 'id="room-config"' in page
    assert f"appointment:{appointment.pk}" in page and 'id="room-editor"' in page
    assert "ws://testserver" in response["Content-Security-Policy"]
    assert AuditEvent.objects.filter(action=AuditAction.ROOM_OPENED).exists()


def test_only_the_people_in_the_session_open_it(client, world) -> None:
    appointment = session(world)
    client.force_login(world.tutor)
    assert client.get(room_url(world, appointment)).status_code == 200
    client.force_login(make_member(world.center, Role.STUDENT))
    assert client.get(room_url(world, appointment)).status_code == 404
    client.force_login(make_member(world.center, Role.TUTOR))
    assert client.get(room_url(world, appointment)).status_code == 404
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(room_url(world, appointment)).status_code == 403
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(room_url(world, appointment)).status_code == 403


def test_before_and_after_the_session_the_page_shows_what_was_saved(client, world) -> None:
    early = session(world, minutes=120)
    over = appointment_for(world, world.student, days=-1)
    RoomDocument.objects.create(appointment=over, content=doc("Revise the intro."))
    client.force_login(world.student)
    page = client.get(room_url(world, early)).content.decode()
    assert "opens half an hour before" in page and "room-config" not in page
    page = client.get(room_url(world, over)).content.decode()
    assert "The session is over" in page and "Revise the intro." in page
    assert "room-config" not in page


def test_an_appointment_without_a_room_says_so(client, world) -> None:
    VideoSettings.objects.update(room_is_on=False)
    client.force_login(world.student)
    page = client.get(room_url(world, session(world))).content.decode()
    assert "has no shared document" in page


# --- The editor service's address --------------------------------------------------------


def test_the_internal_address_needs_the_shared_secret(client, world) -> None:
    appointment = session(world)
    body = {"doc": room.document_name(appointment)}
    assert call(client, "load", body, secret=None).status_code == 403
    assert call(client, "load", body, secret="wrong").status_code == 403
    assert client.get(reverse("collab:load")).status_code == 405
    assert call(client, "load", body).status_code == 200


def test_the_secret_is_never_accepted_when_none_is_set(client, world, settings) -> None:
    settings.SYMPRAXI_COLLAB_SECRET = ""
    assert call(client, "load", {"doc": "x"}, secret="").status_code == 403


def test_a_document_is_saved_and_loaded_back(client, world) -> None:
    appointment = session(world)
    assert call(client, "load", {"doc": room.document_name(appointment)}).json() == {"state": None}
    response = store(client, appointment, doc("Thesis first."), [{"name": "Sam", "text": "Hi"}])
    assert response.json() == {"saved": True}
    loaded = call(client, "load", {"doc": room.document_name(appointment)}).json()
    assert base64.b64decode(loaded["state"]) == b"yjs state"
    saved = RoomDocument.objects.get()
    assert room.text_of(saved.content) == "Thesis first."
    assert saved.chat[0]["text"] == "Hi"


@pytest.mark.parametrize(
    "fields",
    [
        {"content": {"type": "nope"}},
        {"content": ["a"]},
        {"content": {"type": "doc", "content": [{"no": "type"}]}},
        {"state": "***not base64***"},
        {"chat": "text"},
    ],
)
def test_bad_content_is_refused_and_nothing_is_kept(client, world, fields) -> None:
    appointment = session(world)
    body = {
        "doc": room.document_name(appointment),
        "state": STATE,
        "content": doc("ok"),
        "chat": [],
    }
    body.update(fields)
    assert call(client, "store", body).status_code == 400
    assert not RoomDocument.objects.exists()


def test_content_nested_too_deep_is_refused(client, world) -> None:
    node: dict = {"type": "paragraph"}
    for _ in range(room.MAX_DEPTH + 5):
        node = {"type": "blockquote", "content": [node]}
    appointment = session(world)
    assert store(client, appointment, {"type": "doc", "content": [node]}).status_code == 400


def test_nothing_is_saved_for_an_appointment_with_no_room(client, world) -> None:
    VideoSettings.objects.update(room_is_on=False)
    assert store(client, session(world)).json() == {"saved": False}
    assert call(client, "store", {"doc": "appointment:nonsense", "state": STATE}).json() == {
        "saved": False
    }


def test_chat_is_trimmed_to_what_a_message_can_hold(client, world) -> None:
    appointment = session(world)
    long = [{"name": "N" * 500, "text": "x" * 5000, "at": "now", "extra": 1}, "junk"]
    store(client, appointment, chat=long)
    [message] = RoomDocument.objects.get().chat
    assert (len(message["name"]), len(message["text"])) == (100, 1000)
    assert "extra" not in message


# --- History -----------------------------------------------------------------------------


def test_a_version_is_saved_automatically_when_the_document_changed(world) -> None:
    name = room.document_name(session(world))
    now = timezone.now()
    room.save(name, STATE, doc("One"), [], now=now)
    assert RoomVersion.objects.count() == 1  # the first change is kept at once
    room.save(name, STATE, doc("Two"), [], now=now + timedelta(minutes=1))
    assert RoomVersion.objects.count() == 1  # too soon after the last
    room.save(name, STATE, doc("Two"), [], now=now + timedelta(minutes=6))
    assert RoomVersion.objects.count() == 2  # "Two" earns one once the time has passed
    room.save(name, STATE, doc("Two"), [], now=now + timedelta(minutes=12))
    assert RoomVersion.objects.count() == 2  # nothing changed since


def test_a_person_saves_a_version_and_reads_it_later(client, world) -> None:
    appointment = session(world)
    store(client, appointment, doc("Our plan."))
    client.force_login(world.student)
    response = client.post(versions_url(world, appointment))
    assert response.status_code == 302
    version = RoomVersion.objects.filter(label=world.student.display_name).get()
    assert AuditEvent.objects.filter(action=AuditAction.ROOM_VERSION_SAVED).exists()
    page = client.get(versions_url(world, appointment)).content.decode()
    assert world.student.display_name in page
    shown = client.get(f"{versions_url(world, appointment)}?version={version.pk}").content.decode()
    assert "Our plan." in shown


def test_saving_a_version_of_an_empty_document_is_declined(client, world) -> None:
    appointment = session(world)
    client.force_login(world.student)
    response = client.post(versions_url(world, appointment), HTTP_ACCEPT="application/json")
    assert response.status_code == 409 and not RoomVersion.objects.exists()


def test_the_history_is_for_the_people_in_the_session(client, world) -> None:
    appointment = session(world)
    client.force_login(make_member(world.center, Role.STUDENT))
    assert client.get(versions_url(world, appointment)).status_code == 404
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(versions_url(world, appointment)).status_code == 403


# --- Showing what people typed ------------------------------------------------------------


def test_what_people_type_is_escaped_not_run() -> None:
    hostile = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    {"type": "text", "text": "<script>alert(1)</script>"},
                    {
                        "type": "text",
                        "text": "link",
                        "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}],
                    },
                    {"type": "text", "text": "bold", "marks": [{"type": "bold"}]},
                ],
            },
            {"type": "iframe", "content": [{"type": "text", "text": "kept as text"}]},
            {"type": "heading", "attrs": {"level": 99}, "content": [{"type": "text", "text": "H"}]},
        ],
    }
    html = str(room.render(hostile))
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "javascript:" not in html and "<a " not in html and "<iframe" not in html
    assert "<strong>bold</strong>" in html and "kept as text" in html and "<h3>H</h3>" in html


def test_the_text_of_a_document_is_one_block_per_line() -> None:
    content = {
        "type": "doc",
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 1},
                "content": [{"type": "text", "text": "Plan"}],
            },
            {
                "type": "bulletList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [
                            {"type": "paragraph", "content": [{"type": "text", "text": "One"}]}
                        ],
                    }
                ],
            },
        ],
    }
    assert room.text_of(content) == "Plan\nOne"


# --- The settings and the exports ---------------------------------------------------------


def test_a_director_turns_the_shared_document_on_and_off(client, world, settings) -> None:
    VideoSettings.objects.update(room_is_on=False)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    url = reverse("online:settings", kwargs={"slug": world.center.slug})
    client.post(url, {"room_is_on": "on"})
    assert room.room_on(world.center)
    client.post(url, {})
    assert not room.room_on(world.center)


def test_the_student_export_holds_the_shared_documents(client, world) -> None:
    appointment = session(world)
    store(client, appointment, doc("Our plan."), [{"name": "Sam", "text": "Hi", "at": "t"}])
    room.save_version(appointment, world.tutor)
    membership = Membership.objects.get(center=world.center, user=world.student, role=Role.STUDENT)
    data = export.build(membership, exported_by=make_member(world.center, Role.DIRECTOR))
    [shared] = data["shared_documents"]
    assert shared["text"] == "Our plan." and shared["chat"][0]["text"] == "Hi"
    assert world.tutor.display_name in [v["saved_by"] for v in shared["versions"]]


def test_the_session_video_sits_beside_the_document(client, world, settings) -> None:
    settings.SYMPRAXI_JITSI_DOMAIN = "meet.example.edu"
    settings.SYMPRAXI_JITSI_APP_ID = "sympraxi"
    settings.SYMPRAXI_JITSI_APP_SECRET = "a-jitsi-secret-for-the-test"
    VideoSettings.objects.update(is_on=True)
    online = session(world, modality="online", meeting_url="", room=None)
    in_person = session(world, minutes=50)
    client.force_login(world.student)
    response = client.get(room_url(world, online))
    page = response.content.decode()
    assert '<iframe class="sympraxi-room__video" src="https://meet.example.edu/sympraxi-' in page
    assert "frame-src https://meet.example.edu" in response["Content-Security-Policy"]
    assert AuditEvent.objects.filter(action=AuditAction.VIDEO_JOINED).exists()
    response = client.get(room_url(world, in_person))
    assert "<iframe" not in response.content.decode()
    assert "frame-src" not in response["Content-Security-Policy"]
