"""Webhooks: signed notices to a web address when something happens in the center.

Events come from the audit log, so there is one place that decides what counts as
something happening. A notice carries the kind of event, when, the center, and the
id of the thing that changed. It never carries a name, an email or a note: the
receiver asks the API, with its own token, for whatever it is allowed to see.

Each notice is POSTed as JSON with `Sympraxi-Signature: t=<unix time>,v1=<hex>`, where
the signature is an HMAC-SHA256 of `<t>.<body>` with the endpoint's secret. A receiver
should refuse a notice whose time is far from now. A notice that fails is tried again
after 1 minute, 5 minutes, 30 minutes, 2 hours and 12 hours, and then given up on.
The address must be public: one that resolves to a private or internal address is
refused, so a webhook can't be used to reach into the host's own network.
"""

import hashlib
import hmac
import http.client
import json
import secrets
import socket
import ssl
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from sympraxi.accounts.encryption import cipher
from sympraxi.integrations.models import DeliveryStatus, WebhookDelivery, WebhookEndpoint
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records.linkcheck import Unreachable, public_address

# The event names a webhook can subscribe to, and the audit actions behind each.
EVENTS: dict[str, tuple[str, set[str]]] = {
    "appointment.booked": (
        "appointment",
        {AuditAction.APPOINTMENT_BOOKED, AuditAction.APPOINTMENT_JOINED},
    ),
    "appointment.changed": (
        "appointment",
        {AuditAction.APPOINTMENT_CHANGED, AuditAction.APPOINTMENT_CONFIRMED},
    ),
    "appointment.canceled": (
        "appointment",
        {AuditAction.APPOINTMENT_CANCELED, AuditAction.APPOINTMENT_LEFT},
    ),
    "workshop.registered": ("workshop", {AuditAction.WORKSHOP_REGISTERED}),
    "workshop.canceled": ("workshop", {AuditAction.WORKSHOP_CANCELED}),
    "arrival.recorded": ("arrival", {AuditAction.ARRIVAL_RECORDED}),
}
ACTION_TO_EVENT = {str(a): name for name, (_, actions) in EVENTS.items() for a in actions}
BACKOFF = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
]
TIMEOUT = 10


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


def seal(secret: str) -> str:
    return cipher().encrypt(secret.encode()).decode()


def unseal(sealed: str) -> str:
    return cipher().decrypt(sealed.encode()).decode()


def sign(secret: str, body: bytes, when: int) -> str:
    mac = hmac.new(secret.encode(), f"{when}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={when},v1={mac}"


def payload_for(event_name: str, event: AuditEvent) -> dict[str, Any]:
    kind = EVENTS[event_name][0]
    center = event.center
    return {
        "id": str(event.pk),
        "type": event_name,
        "created": event.occurred_at.isoformat(),
        "center": center.slug if center else "",
        "data": {"object": kind, "id": event.target_id},
    }


def hook(event: AuditEvent) -> None:
    """Called when an audit event is written. Queues notices after the transaction commits."""
    if event.center_id is not None and event.action in ACTION_TO_EVENT:
        transaction.on_commit(lambda: queue(event.pk))


def queue(event_id: int) -> int:
    """Make a delivery for each endpoint that wants this event, and send them."""
    event = AuditEvent.objects.select_related("center").filter(pk=event_id).first()
    if event is None or event.action not in ACTION_TO_EVENT:
        return 0
    name = ACTION_TO_EVENT[event.action]
    made = 0
    for endpoint in WebhookEndpoint.objects.filter(center=event.center, is_active=True):
        if endpoint.events and name not in endpoint.events:
            continue
        delivery = WebhookDelivery.objects.create(
            endpoint=endpoint, event=name, payload=payload_for(name, event)
        )
        send_later(delivery.pk)
        made += 1
    return made


def ping(endpoint: WebhookEndpoint) -> WebhookDelivery:
    """A test notice, so a director can check the address and the signature."""
    delivery = WebhookDelivery.objects.create(
        endpoint=endpoint,
        event="ping",
        payload={
            "id": "ping",
            "type": "ping",
            "created": timezone.now().isoformat(),
            "center": endpoint.center.slug,
            "data": {},
        },
    )
    send_later(delivery.pk)
    return delivery


def send_later(delivery_id: Any) -> None:
    from sympraxi.integrations.tasks import deliver_webhook

    transaction.on_commit(lambda: deliver_webhook.enqueue(str(delivery_id)))


# --- Sending ---------------------------------------------------------------------------------


def post_json(
    url: str, body: bytes, headers: dict[str, str], *, allow_private: bool = False
) -> tuple[int, str]:
    """POST to a vetted public address. Returns the status and the start of the answer."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Unreachable("not a web address")
    if parts.scheme == "http" and not allow_private:
        raise Unreachable("webhook addresses must use https")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = public_address(parts.hostname, port, allow_private=allow_private)
    try:
        sock = socket.create_connection((address, port), timeout=TIMEOUT)
        if parts.scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
    except (OSError, ssl.SSLError) as error:
        raise Unreachable(f"couldn't connect: {error}") from error
    connection: http.client.HTTPConnection
    if parts.scheme == "https":
        connection = http.client.HTTPSConnection(parts.hostname, port, timeout=TIMEOUT)
    else:
        connection = http.client.HTTPConnection(parts.hostname, port, timeout=TIMEOUT)
    connection.sock = sock
    path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    try:
        connection.request("POST", path, body=body, headers=headers)
        answer = connection.getresponse()
        text = answer.read(300).decode("utf-8", errors="replace")
        return answer.status, text
    except (OSError, http.client.HTTPException) as error:
        raise Unreachable(f"no answer: {error}") from error
    finally:
        connection.close()


Poster = Any


def deliver(delivery_id: Any, *, post: Poster = None, now: datetime | None = None) -> bool:
    """Try one delivery. True if it was delivered. Safe to run twice at once."""
    now = now or timezone.now()
    post = post or post_json
    with transaction.atomic():
        delivery = (
            WebhookDelivery.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("endpoint")
            .filter(pk=delivery_id, status=DeliveryStatus.PENDING)
            .first()
        )
        if delivery is None or delivery.next_attempt_at > now:
            return False
        endpoint = delivery.endpoint
        if not endpoint.is_active and delivery.event != "ping":
            delivery.status = DeliveryStatus.FAILED
            delivery.last_error = "The endpoint was turned off."
            delivery.save(update_fields=["status", "last_error"])
            return False
        body = json.dumps(delivery.payload, separators=(",", ":"), sort_keys=True).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"{settings.SYMPRAXI_SITE_NAME}-webhooks",
            "Sympraxi-Event": delivery.event,
            "Sympraxi-Delivery": str(delivery.pk),
            "Sympraxi-Signature": sign(
                unseal(endpoint.secret_encrypted), body, int(now.timestamp())
            ),
        }
        delivery.attempts += 1
        try:
            status, snippet = post(
                endpoint.url,
                body,
                headers,
                allow_private=getattr(settings, "SYMPRAXI_WEBHOOKS_ALLOW_PRIVATE", False),
            )
        except Unreachable as error:
            status, snippet = None, str(error)
        delivery.last_status_code = status
        if status is not None and 200 <= status < 300:
            delivery.status = DeliveryStatus.DELIVERED
            delivery.delivered_at = now
            delivery.last_error = ""
            delivery.save()
            return True
        delivery.last_error = (snippet or f"HTTP {status}")[:300]
        if delivery.attempts > len(BACKOFF):
            delivery.status = DeliveryStatus.FAILED
        else:
            delivery.next_attempt_at = now + BACKOFF[delivery.attempts - 1]
        delivery.save()
        return False


def retry_due(now: datetime | None = None) -> int:
    """Queue the deliveries whose next try is due. Run by the scheduler every minute."""
    now = now or timezone.now()
    due = list(
        WebhookDelivery.objects.filter(
            status=DeliveryStatus.PENDING, next_attempt_at__lte=now, attempts__gt=0
        ).values_list("pk", flat=True)[:200]
    )
    for pk in due:
        send_later(pk)
    return len(due)
