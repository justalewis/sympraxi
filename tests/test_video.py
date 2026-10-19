"""Video rooms: tokens Jitsi accepts, who is let in and when, and the center's switch."""

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.online import jwt, services
from sympraxi.online.models import VideoSettings
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import online_switch
from sympraxi.scheduling.models import AppointmentStatus, Modality
from tests.records_world import appointment_for
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

SECRET = "a-long-shared-secret-for-the-test"


@pytest.fixture
def jitsi(settings):
    settings.SYMPRAXI_JITSI_DOMAIN = "meet.example.edu"
    settings.SYMPRAXI_JITSI_APP_ID = "sympraxi"
    settings.SYMPRAXI_JITSI_APP_SECRET = SECRET


@pytest.fixture
def world(center, jitsi):
    VideoSettings.objects.create(center=center, is_on=True)
    return build_world(center)


def starting_soon(world, minutes: int = 5, **fields):
    """An online appointment that starts in a few minutes."""
    return appointment_for(
        world,
        world.student,
        days=minutes / 1440,
        modality=Modality.ONLINE,
        meeting_url="",
        room=None,
        **fields,
    )


def join_url(world, appointment) -> str:
    return reverse(
        "online:join", kwargs={"slug": world.center.slug, "appointment_id": appointment.pk}
    )


def claims_of(location: str) -> dict:
    token = parse_qs(urlsplit(location).query)["jwt"][0]
    header, payload, signature = token.split(".")
    expected = hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "==") == expected
    return json.loads(base64.urlsafe_b64decode(payload + "=="))


# --- Tokens -------------------------------------------------------------------------


def test_a_token_is_a_signed_hs256_jwt() -> None:
    token = jwt.encode({"room": "r"}, SECRET)
    header, payload, signature = token.split(".")
    assert json.loads(base64.urlsafe_b64decode(header + "==")) == {"alg": "HS256", "typ": "JWT"}
    assert json.loads(base64.urlsafe_b64decode(payload + "==")) == {"room": "r"}
    expected = hmac.new(SECRET.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    assert base64.urlsafe_b64decode(signature + "==") == expected


def test_rooms_are_named_from_a_keyed_hash(world) -> None:
    first, second = starting_soon(world), starting_soon(world, minutes=40)
    assert services.room_name(first) == services.room_name(first)
    assert services.room_name(first) != services.room_name(second)
    assert str(first.pk).replace("-", "") not in services.room_name(first)


# --- Joining ------------------------------------------------------------------------


def test_a_student_enters_their_room_with_a_token_for_it_alone(client, world) -> None:
    appointment = starting_soon(world)
    client.force_login(world.student)
    response = client.get(join_url(world, appointment))
    assert response.status_code == 302
    location = response["Location"]
    assert location.startswith(f"https://meet.example.edu/{services.room_name(appointment)}?jwt=")
    claims = claims_of(location)
    assert (claims["aud"], claims["iss"], claims["sub"]) == (
        "jitsi",
        "sympraxi",
        "meet.example.edu",
    )
    assert claims["room"] == services.room_name(appointment)
    assert claims["moderator"] is False and claims["context"]["user"]["moderator"] is False
    assert claims["context"]["user"]["name"] == world.student.display_name
    assert claims["exp"] == int((appointment.ends_at + services.STAY_LATE).timestamp())
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"] == "no-referrer"
    assert AuditEvent.objects.filter(action=AuditAction.VIDEO_JOINED).exists()


def test_the_tutor_moderates(client, world) -> None:
    appointment = starting_soon(world)
    client.force_login(world.tutor)
    claims = claims_of(client.get(join_url(world, appointment))["Location"])
    assert claims["moderator"] is True and claims["context"]["user"]["moderator"] is True


def test_the_room_opens_ten_minutes_before_and_closes_half_an_hour_after(client, world) -> None:
    client.force_login(world.student)
    early = starting_soon(world, minutes=30)
    response = client.get(join_url(world, early))
    assert response.status_code == 200 and "opens" in response.content.decode()
    over = appointment_for(
        world, world.student, days=-1, modality=Modality.ONLINE, meeting_url="", room=None
    )
    response = client.get(join_url(world, over))
    assert response.status_code == 200 and "has closed" in response.content.decode()
    assert not AuditEvent.objects.filter(action=AuditAction.VIDEO_JOINED).exists()


def test_there_is_no_room_for_an_in_person_or_canceled_appointment(client, world) -> None:
    client.force_login(world.student)
    in_person = appointment_for(world, world.student, days=5 / 1440)
    response = client.get(join_url(world, in_person))
    assert response.status_code == 200 and "no video room" in response.content.decode()
    canceled = starting_soon(world, status=AppointmentStatus.CANCELED)
    assert client.get(join_url(world, canceled)).status_code == 200


def test_nothing_opens_while_the_center_has_video_off(client, world) -> None:
    VideoSettings.objects.update(is_on=False)
    client.force_login(world.student)
    response = client.get(join_url(world, starting_soon(world)))
    assert response.status_code == 200 and "no video room" in response.content.decode()


def test_nothing_opens_without_a_jitsi_server(client, world, settings) -> None:
    settings.SYMPRAXI_JITSI_APP_SECRET = ""
    client.force_login(world.student)
    assert client.get(join_url(world, starting_soon(world))).status_code == 200


def test_only_the_people_in_it_may_join(client, world) -> None:
    appointment = starting_soon(world)
    client.force_login(make_member(world.center, Role.STUDENT))
    assert client.get(join_url(world, appointment)).status_code == 404
    client.force_login(make_member(world.center, Role.TUTOR))
    assert client.get(join_url(world, appointment)).status_code == 404
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(join_url(world, appointment)).status_code == 403
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(join_url(world, appointment)).status_code == 403


def test_the_appointment_page_offers_the_room_when_it_is_open(client, world) -> None:
    appointment = starting_soon(world)
    page = reverse(
        "scheduling:appointment",
        kwargs={"slug": world.center.slug, "appointment_id": appointment.pk},
    )
    client.force_login(world.student)
    assert "Join the video session" in client.get(page).content.decode()
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert "Join the video session" not in client.get(page).content.decode()


# --- The rest of scheduling -----------------------------------------------------------


def test_online_tutors_need_a_link_only_without_video(world) -> None:
    world.assignment.meeting_url = ""
    world.assignment.offers_online = True
    world.assignment.clean()  # video is on: no link needed
    VideoSettings.objects.update(is_on=False)
    with pytest.raises(ValidationError, match="meeting link"):
        world.assignment.clean()


def test_a_tutor_without_a_link_can_move_online_when_there_is_video(world) -> None:
    from tests.scheduling_world import at

    tomorrow = timezone.localtime(world.appointment.starts_at, world.schedule.tz).date()
    world.assignment.meeting_url = ""
    world.assignment.save()
    assert at  # the world's one booking is on `tomorrow`
    plan = online_switch.plan(world.center, tomorrow)
    assert plan.movable == [world.appointment] and plan.stuck == []
    VideoSettings.objects.update(is_on=False)
    plan = online_switch.plan(world.center, tomorrow)
    assert plan.stuck == [world.appointment]


# --- The center's switch --------------------------------------------------------------


def test_a_director_turns_video_on_and_off(client, world) -> None:
    VideoSettings.objects.update(is_on=False)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    settings_url = reverse("online:settings", kwargs={"slug": world.center.slug})
    client.post(settings_url, {"is_on": "on"})
    assert services.video_on(world.center)
    client.post(settings_url, {})
    assert not services.video_on(world.center)
    assert AuditEvent.objects.filter(action=AuditAction.VIDEO_SETTINGS_CHANGED).count() == 2


def test_the_settings_page_explains_when_there_is_no_server(client, world, settings) -> None:
    settings.SYMPRAXI_JITSI_DOMAIN = ""
    client.force_login(make_member(world.center, Role.DIRECTOR))
    page = client.get(reverse("online:settings", kwargs={"slug": world.center.slug}))
    assert "isn't connected to a Jitsi" in page.content.decode()
    assert "Save" not in page.content.decode().split("<main")[1]


def test_calendar_files_say_where_to_join(world) -> None:
    from sympraxi.scheduling import ics

    appointment = starting_soon(world)
    assert ics.location(appointment) == "Online: join from the appointment page"
    appointment.meeting_url = "https://meet.example.invalid/room"
    assert ics.location(appointment) == "https://meet.example.invalid/room"
    assert timedelta(minutes=10) == services.JOIN_EARLY
