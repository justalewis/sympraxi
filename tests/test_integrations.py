"""API tokens, the JSON API, and webhooks."""

import json
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from sympraxi.accounts.encryption import cipher
from sympraxi.centers.models import Membership, Role
from sympraxi.integrations import api, tokens, webhooks
from sympraxi.integrations.models import ApiToken, DeliveryStatus, WebhookDelivery, WebhookEndpoint
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records.linkcheck import Unreachable
from sympraxi.scheduling import services
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Modality
from sympraxi.workshops.models import Workshop
from tests.scheduling_world import build_world, make_member
from tests.workshops_world import make_workshop

pytestmark = pytest.mark.django_db

ALL = [
    "schedules:read",
    "appointments:read",
    "appointments:write",
    "students:read",
    "workshops:read",
]


@pytest.fixture(autouse=True)
def fresh_cache():
    cache.clear()


@pytest.fixture
def world(center):
    return build_world(center)


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


def make_token(center, director, scopes=None):
    return tokens.create(center, director, name="Reporting", scopes=scopes or ALL, days=30)


def call(client, center, name, token, *, method="get", query=None, body=None, **kwargs):
    url = reverse(f"api:{name}", kwargs={"slug": center.slug, **kwargs})
    if query:
        url += "?" + urlencode(query)
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    if method == "post":
        return client.post(url, json.dumps(body or {}), content_type="application/json", **headers)
    return getattr(client, method)(url, **headers)


# --- Tokens ----------


def test_a_token_is_shown_once_and_only_its_hash_is_kept(center, director) -> None:
    row, text = make_token(center, director)
    assert text.startswith("sym_") and row.prefix == text.split("_")[0] + "_" + text.split("_")[1]
    assert text not in str(row.__dict__.values()) and row.token_hash != text
    assert tokens.find(text) == row
    assert (
        tokens.find(text + "x") is None and tokens.find("") is None and tokens.find("sym_") is None
    )
    assert AuditEvent.objects.filter(action=AuditAction.API_TOKEN_CREATED).count() == 1


def test_a_token_needs_a_real_scope(center, director) -> None:
    with pytest.raises(ValueError):
        tokens.create(center, director, name="x", scopes=["everything"], days=None)


def test_expired_and_revoked_tokens_stop_working(center, director) -> None:
    row, text = make_token(center, director)
    row.expires_at = timezone.now() - timedelta(seconds=1)
    row.save()
    assert tokens.find(text) is None
    row.expires_at = None
    row.revoked_at = None
    row.save()
    assert tokens.find(text)
    assert tokens.revoke(row, by=director) and not tokens.revoke(row, by=director)
    assert tokens.find(text) is None


def test_the_page_shows_a_new_token_once(client, center, director) -> None:
    client.force_login(director)
    url = reverse("integrations:home", kwargs={"slug": center.slug})
    page = client.post(
        url, {"action": "token", "name": "Dashboard", "scopes": ["schedules:read"], "days": "90"}
    ).content.decode()
    row = ApiToken.objects.get()
    assert "Copy this token now" in page and row.prefix in page
    again = client.get(url).content.decode()
    assert "Copy this token now" not in again and row.prefix in again
    client.post(url, {"action": "revoke", "token": str(row.pk)})
    row.refresh_from_db()
    assert row.revoked_at is not None


def test_only_directors_open_the_integrations_page(client, center) -> None:
    client.force_login(make_member(center, Role.TUTOR))
    assert client.get(reverse("integrations:home", kwargs={"slug": center.slug})).status_code == 403


# --- Who may call ----------


def test_the_api_refuses_a_missing_wrong_or_other_centers_token(
    client, center, other_center, director
) -> None:
    _, text = make_token(center, director)
    assert call(client, center, "schedules", None).status_code == 401
    assert call(client, center, "schedules", "sym_wrong_token").status_code == 401
    other = call(client, other_center, "schedules", text)
    assert other.status_code == 401 and other.json()["error"]["code"] == "invalid_token"
    assert call(client, center, "schedules", text).status_code == 200


def test_a_token_needs_the_scope_for_what_it_asks(client, center, director) -> None:
    _, text = make_token(center, director, ["schedules:read"])
    response = call(client, center, "students", text)
    assert response.status_code == 403 and response.json()["error"]["code"] == "missing_scope"


def test_a_token_stops_working_when_its_owner_loses_access(client, center, director) -> None:
    _, text = make_token(center, director)
    Membership.objects.filter(user=director, center=center).update(is_active=False)
    response = call(client, center, "schedules", text)
    assert response.status_code == 403 and response.json()["error"]["code"] == "owner_lost_access"


def test_the_api_is_rate_limited_per_token(client, center, director, monkeypatch) -> None:
    monkeypatch.setattr(api, "RATE_LIMIT", 3)
    _, text = make_token(center, director)
    codes = [call(client, center, "schedules", text).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_wrong_methods_are_refused(client, center, director) -> None:
    _, text = make_token(center, director)
    response = call(client, center, "schedules", text, method="post")
    assert response.status_code == 405 and response["Allow"] == "GET"


# --- Reading ----------


def test_schedules_and_appointments_are_listed(client, center, director, world) -> None:
    _, text = make_token(center, director)
    schedules = call(client, center, "schedules", text).json()["items"]
    assert schedules[0]["id"] == str(world.schedule.pk) and schedules[0]["tutors"]
    items = call(client, center, "appointments", text).json()["items"]
    mine = next(i for i in items if i["id"] == str(world.appointment.pk))
    assert mine["students"][0]["email"] == world.student.email
    assert mine["tutor"]["name"] == world.tutor.display_name


def test_a_token_without_the_scope_sees_no_students_in_appointments(
    client, center, director, world
) -> None:
    _, text = make_token(center, director, ["schedules:read"])
    assert call(client, center, "appointments", text).status_code == 403


def test_appointments_page_without_repeating_or_skipping(client, center, director, world) -> None:
    for hours in (1, 2, 3):
        other = make_member(center, Role.STUDENT)
        services.book(
            actor=director,
            assignment=world.assignment,
            starts_at=world.free_start + timedelta(hours=hours),
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[other],
            as_staff=True,
            override_limits=True,
            notify=False,
        )
    _, text = make_token(center, director)
    everything = call(client, center, "appointments", text).json()["items"]
    seen, after = [], None
    for _ in range(10):
        query = {"limit": 1, **({"after": after} if after else {})}
        page = call(client, center, "appointments", text, query=query).json()
        seen += [i["id"] for i in page["items"]]
        after = page["next"]
        if after is None:
            break
    assert seen == [i["id"] for i in everything]


def test_appointments_filter_by_time_and_status(client, center, director, world) -> None:
    _, text = make_token(center, director)
    start = world.appointment.starts_at
    inside = call(
        client,
        center,
        "appointments",
        text,
        query={"from": start.isoformat(), "to": (start + timedelta(hours=1)).isoformat()},
    ).json()["items"]
    assert [i["id"] for i in inside] == [str(world.appointment.pk)]
    before = call(
        client,
        center,
        "appointments",
        text,
        query={"to": (start - timedelta(days=1)).isoformat()},
    ).json()["items"]
    assert before == []
    canceled = call(client, center, "appointments", text, query={"status": "canceled"}).json()
    assert canceled["items"] == []
    bad = call(client, center, "appointments", text, query={"from": "tomorrow"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_time"
    assert call(client, center, "appointments", text, query={"limit": "x"}).status_code == 400
    assert (
        call(client, center, "appointments", text, query={"after": "nonsense"}).status_code == 400
    )


def test_students_and_workshops_are_listed(client, center, director, world) -> None:
    make_workshop(center, None, title="Citations")
    _, text = make_token(center, director)
    people = call(client, center, "students", text).json()["items"]
    assert any(p["email"] == world.student.email for p in people)
    shops = call(client, center, "workshops", text).json()["items"]
    assert shops[0]["title"] == "Citations" and shops[0]["registered"] == 0
    assert Workshop.objects.count() == 1


# --- Writing ----------


def free_body(world, student=None) -> dict:
    return {
        "assignment": str(world.assignment.pk),
        "starts_at": world.free_start.isoformat(),
        "minutes": 30,
        "modality": "in_person",
        "student_email": (student or world.student).email,
    }


def test_booking_through_the_api_uses_the_booking_rules(client, center, director, world) -> None:
    other = make_member(center, Role.STUDENT, full_name="Other Student")
    _, text = make_token(center, director)
    response = call(
        client, center, "appointments", text, method="post", body=free_body(world, other)
    )
    assert response.status_code == 201
    made = Appointment.objects.get(pk=response.json()["id"])
    assert made.participants.get().student == other and made.created_by == director
    event = AuditEvent.objects.get(action=AuditAction.API_WRITE)
    assert (
        event.actor_label.startswith("API token sym_")
        and event.details["what"] == "appointment booked"
    )
    taken = call(
        client, center, "appointments", text, method="post", body=free_body(world, world.student)
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "slot_taken"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"starts_at": "2026-10-21T10:00:00"}, "bad_time"),
        ({"minutes": "abc"}, "bad_minutes"),
        ({"modality": "hovercraft"}, "bad_modality"),
        ({"student_email": "nobody@example.edu"}, "student_not_found"),
        ({"assignment": "not-a-uuid"}, "bad_id"),
    ],
)
def test_bad_bookings_say_what_is_wrong(client, center, director, world, change, code) -> None:
    _, text = make_token(center, director)
    body = {**free_body(world), **change}
    response = call(client, center, "appointments", text, method="post", body=body)
    assert response.json()["error"]["code"] == code and response.status_code in (400, 404)


def test_a_read_only_token_cannot_write(client, center, director, world) -> None:
    _, text = make_token(center, director, ["appointments:read"])
    response = call(client, center, "appointments", text, method="post", body=free_body(world))
    assert response.status_code == 403 and Appointment.objects.count() == 1


def test_a_bad_json_body_is_a_400(client, center, director) -> None:
    _, text = make_token(center, director)
    url = reverse("api:appointments", kwargs={"slug": center.slug})
    response = client.post(
        url, "{not json", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {text}"
    )
    assert response.status_code == 400 and response.json()["error"]["code"] == "bad_json"


def test_canceling_through_the_api(client, center, director, world) -> None:
    _, text = make_token(center, director)
    response = call(
        client, center, "cancel", text, method="post", appointment_id=world.appointment.pk
    )
    assert response.status_code == 200 and response.json()["status"] == "canceled"
    world.appointment.refresh_from_db()
    assert world.appointment.status == AppointmentStatus.CANCELED
    missing = call(
        client,
        center,
        "cancel",
        text,
        method="post",
        appointment_id=world.appointment.pk.__class__(int=1),
    )
    assert missing.status_code == 404
    again = call(client, center, "cancel", text, method="post", appointment_id=world.appointment.pk)
    assert again.status_code == 422


# --- Webhooks ----------


class Receiver:
    def __init__(self, statuses=(200,)) -> None:
        self.statuses = list(statuses)
        self.calls: list[tuple[str, bytes, dict]] = []

    def __call__(self, url, body, headers, *, allow_private=False):
        self.calls.append((url, body, headers))
        status = self.statuses.pop(0) if self.statuses else 200
        return status, "ok" if status < 300 else "nope"


def make_endpoint(center, director, events=None):
    secret = webhooks.new_secret()
    endpoint = WebhookEndpoint.objects.create(
        center=center,
        url="https://hooks.example.org/in",
        events=events or [],
        secret_encrypted=webhooks.seal(secret),
        created_by=director,
    )
    return endpoint, secret


def test_a_booking_queues_a_notice_with_ids_and_no_names(
    center, director, world, django_capture_on_commit_callbacks, monkeypatch
) -> None:
    monkeypatch.setattr(webhooks, "post_json", Receiver())
    endpoint, _ = make_endpoint(center, director, ["appointment.booked"])
    make_endpoint(center, director, ["appointment.canceled"])  # not interested in bookings
    student = make_member(center, Role.STUDENT, full_name="Secret Name")
    with django_capture_on_commit_callbacks(execute=True):
        appointment = services.book(
            actor=director,
            assignment=world.assignment,
            starts_at=world.free_start,
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[student],
            as_staff=True,
            notify=False,
        )
    [delivery] = WebhookDelivery.objects.filter(endpoint=endpoint)
    assert delivery.event == "appointment.booked"
    assert delivery.payload["data"] == {"object": "appointment", "id": str(appointment.pk)}
    assert delivery.payload["center"] == center.slug
    flat = json.dumps(delivery.payload)
    assert "Secret Name" not in flat and student.email not in flat
    assert WebhookDelivery.objects.count() == 1


def test_the_signature_can_be_checked_with_the_secret(center, director) -> None:
    endpoint, secret = make_endpoint(center, director)
    delivery = webhooks.ping(endpoint)
    receiver = Receiver()
    assert webhooks.deliver(delivery.pk, post=receiver)
    [(_, body, headers)] = receiver.calls
    stamp, mac = headers["Sympraxi-Signature"].split(",")
    when = int(stamp.removeprefix("t="))
    assert mac == "v1=" + webhooks.sign(secret, body, when).split("v1=")[1]
    assert headers["Sympraxi-Event"] == "ping" and json.loads(body)["type"] == "ping"
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED and delivery.attempts == 1


def test_the_secret_is_encrypted_in_the_database(center, director) -> None:
    endpoint, secret = make_endpoint(center, director)
    assert secret not in endpoint.secret_encrypted
    assert cipher().decrypt(endpoint.secret_encrypted.encode()).decode() == secret


def test_a_failed_notice_is_retried_with_backoff_then_given_up_on(center, director) -> None:
    endpoint, _ = make_endpoint(center, director)
    delivery = webhooks.ping(endpoint)
    receiver = Receiver([500] * 10)
    now = timezone.now()
    waits = []
    for _ in range(len(webhooks.BACKOFF)):
        assert not webhooks.deliver(delivery.pk, post=receiver, now=now)
        delivery.refresh_from_db()
        assert delivery.status == DeliveryStatus.PENDING
        waits.append(delivery.next_attempt_at - now)
        now = delivery.next_attempt_at
    assert waits == webhooks.BACKOFF
    assert not webhooks.deliver(delivery.pk, post=receiver, now=now)
    delivery.refresh_from_db()
    assert (
        delivery.status == DeliveryStatus.FAILED and delivery.attempts == len(webhooks.BACKOFF) + 1
    )
    assert delivery.last_error == "nope"


def test_a_notice_is_not_sent_before_its_time_or_twice(center, director) -> None:
    endpoint, _ = make_endpoint(center, director)
    delivery = webhooks.ping(endpoint)
    receiver = Receiver([500, 200])
    now = timezone.now()
    webhooks.deliver(delivery.pk, post=receiver, now=now)
    assert not webhooks.deliver(delivery.pk, post=receiver, now=now)  # not due yet
    later = now + timedelta(minutes=2)
    assert webhooks.deliver(delivery.pk, post=receiver, now=later)
    assert not webhooks.deliver(delivery.pk, post=receiver, now=later)  # already delivered
    assert len(receiver.calls) == 2


def test_the_sweep_queues_due_retries(center, director, django_capture_on_commit_callbacks) -> None:
    endpoint, _ = make_endpoint(center, director)
    delivery = webhooks.ping(endpoint)
    webhooks.deliver(delivery.pk, post=Receiver([500]))
    with django_capture_on_commit_callbacks(execute=False):
        assert webhooks.retry_due(timezone.now() - timedelta(minutes=10)) == 0
        assert webhooks.retry_due(timezone.now() + timedelta(minutes=2)) == 1


def test_a_turned_off_endpoint_gets_nothing(center, director) -> None:
    endpoint, _ = make_endpoint(center, director)
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint, event="appointment.booked", payload={"type": "appointment.booked"}
    )
    endpoint.is_active = False
    endpoint.save()
    receiver = Receiver()
    assert not webhooks.deliver(delivery.pk, post=receiver)
    assert receiver.calls == []
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED


def test_webhooks_will_not_reach_private_addresses(monkeypatch) -> None:
    with pytest.raises(Unreachable, match="https"):
        webhooks.post_json("http://example.org/x", b"{}", {})
    with pytest.raises(Unreachable, match="not a public address"):
        webhooks.post_json("https://127.0.0.1/x", b"{}", {})
    with pytest.raises(Unreachable, match="not a public address"):
        webhooks.post_json("https://169.254.169.254/latest", b"{}", {})
    with pytest.raises(Unreachable, match="not a web address"):
        webhooks.post_json("ftp://example.org/x", b"{}", {})


def test_the_page_adds_a_webhook_and_shows_the_secret_once(client, center, director) -> None:
    client.force_login(director)
    url = reverse("integrations:home", kwargs={"slug": center.slug})
    refused = client.post(url, {"action": "webhook", "url": "http://hooks.example.org/in"})
    assert "must start with https" in refused.content.decode()
    page = client.post(
        url,
        {
            "action": "webhook",
            "url": "https://hooks.example.org/in",
            "events": ["appointment.booked"],
        },
    ).content.decode()
    endpoint = WebhookEndpoint.objects.get()
    assert "whsec_" in page and endpoint.events == ["appointment.booked"]
    assert "whsec_" not in client.get(url).content.decode()
    client.post(url, {"action": "toggle", "endpoint": str(endpoint.pk)})
    endpoint.refresh_from_db()
    assert not endpoint.is_active
    client.post(url, {"action": "remove", "endpoint": str(endpoint.pk)})
    assert WebhookEndpoint.objects.count() == 0


def test_another_centers_webhook_cannot_be_touched(client, center, other_center, director) -> None:
    foreign, _ = make_endpoint(other_center, make_member(other_center, Role.DIRECTOR))
    client.force_login(director)
    url = reverse("integrations:home", kwargs={"slug": center.slug})
    client.post(url, {"action": "remove", "endpoint": str(foreign.pk)})
    assert WebhookEndpoint.objects.filter(pk=foreign.pk).exists()
