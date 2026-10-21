"""Opting in, checking the number, sending reminders, and handling STOP and HELP."""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from sympraxi.accounts.encryption import cipher
from sympraxi.accounts.models import User
from sympraxi.privacy.audit import AuditAction, client_ip, record
from sympraxi.scheduling.models import Appointment
from sympraxi.sms import providers
from sympraxi.sms.models import MessageStatus, SmsConsent, SmsMessage

logger = logging.getLogger("sympraxi.sms")

CONSENT_VERSION = "2026-10"
CODE_MINUTES = 15
MAX_ATTEMPTS = 5
RESEND_SECONDS = 60
# No reminder texts before 8 am or after 9 pm in the center's time zone.
QUIET_BEFORE, QUIET_AFTER = time(8), time(21)

STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_WORDS = {"start", "unstop", "yes"}
HELP_WORDS = {"help", "info"}


class SmsError(Exception):
    """A rule refused the request; the message is written for the person who asked."""


def consent_text() -> str:
    return _(
        "I agree to receive text messages from %(site)s with reminders about my appointments, "
        "and a code to check this number. Message frequency varies. Message and data rates "
        "may apply. Reply STOP to cancel at any time, or HELP for help. I am not required to "
        "agree to use the center."
    ) % {"site": settings.SYMPRAXI_SITE_NAME}


def available() -> bool:
    return providers.configured() is not None


# --- Numbers ----------


def normalize(raw: str) -> str | None:
    """A phone number as E.164, or None. Ten digits are taken as North American."""
    text = raw.strip()
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if plus:
        pass
    elif len(digits) == 10:
        digits = "1" + digits
    elif len(digits) == 11 and digits.startswith("1"):
        pass
    else:
        return None
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return None
    return "+" + digits


def phone_hash(e164: str) -> str:
    key = hashlib.sha256(b"sympraxi.sms.phone:" + settings.SECRET_KEY.encode()).digest()
    return hmac.new(key, e164.encode(), hashlib.sha256).hexdigest()


def seal(value: str) -> str:
    return cipher().encrypt(value.encode()).decode()


def phone_of(consent: SmsConsent) -> str:
    return cipher().decrypt(consent.phone_encrypted.encode()).decode()


def masked(consent: SmsConsent) -> str:
    return "••• ••• " + phone_of(consent)[-4:]


# --- Opting in ----------


def _code_hash(code: str, consent: SmsConsent) -> str:
    return hashlib.sha256(f"{consent.pk}:{code}".encode()).hexdigest()


def start_opt_in(
    user: User, raw_phone: str, *, agreed: bool, request: HttpRequest | None = None
) -> SmsConsent:
    """Record the agreement and text a code. A box that isn't ticked is not an agreement."""
    if not available():
        raise SmsError(_("Text messages aren't set up on this site."))
    if not agreed:
        raise SmsError(_("Tick the box to agree to receive texts."))
    phone = normalize(raw_phone)
    if phone is None:
        raise SmsError(_("Enter a mobile number with its area code, like 555 010 1234."))
    code = f"{secrets.randbelow(10**6):06d}"
    with transaction.atomic():
        SmsConsent.objects.filter(user=user, revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_via="web"
        )
        consent = SmsConsent.objects.create(
            user=user,
            phone_encrypted=seal(phone),
            phone_hash=phone_hash(phone),
            consent_text=consent_text(),
            consent_version=CONSENT_VERSION,
            consent_ip=client_ip(request) if request is not None else None,
        )
        consent.code_hash = _code_hash(code, consent)
        consent.code_expires_at = timezone.now() + timedelta(minutes=CODE_MINUTES)
        consent.code_sent_at = timezone.now()
        consent.save(update_fields=["code_hash", "code_expires_at", "code_sent_at"])
        record(
            AuditAction.SMS_CONSENT_GIVEN,
            request=request,
            actor=user,
            target=consent,
            version=CONSENT_VERSION,
        )
    _send_now(
        consent,
        "code",
        _("%(site)s code: %(code)s. Reply STOP to opt out.")
        % {"site": settings.SYMPRAXI_SITE_NAME, "code": code},
    )
    return consent


def resend_code(consent: SmsConsent) -> None:
    if consent.verified_at is not None or consent.revoked_at is not None:
        raise SmsError(_("There's nothing to confirm."))
    if consent.code_sent_at and timezone.now() - consent.code_sent_at < timedelta(
        seconds=RESEND_SECONDS
    ):
        raise SmsError(_("Wait a minute before asking for another code."))
    code = f"{secrets.randbelow(10**6):06d}"
    consent.code_hash = _code_hash(code, consent)
    consent.code_expires_at = timezone.now() + timedelta(minutes=CODE_MINUTES)
    consent.code_sent_at = timezone.now()
    consent.code_attempts = 0
    consent.save(update_fields=["code_hash", "code_expires_at", "code_sent_at", "code_attempts"])
    _send_now(
        consent,
        "code",
        _("%(site)s code: %(code)s. Reply STOP to opt out.")
        % {"site": settings.SYMPRAXI_SITE_NAME, "code": code},
    )


def confirm(consent: SmsConsent, code: str, *, request: HttpRequest | None = None) -> None:
    """Check the code. A few wrong tries lock it; ask for a new code."""
    if consent.revoked_at is not None or consent.verified_at is not None:
        raise SmsError(_("There's nothing to confirm."))
    if consent.code_attempts >= MAX_ATTEMPTS or (
        consent.code_expires_at and consent.code_expires_at < timezone.now()
    ):
        raise SmsError(_("That code has expired. Ask for a new one."))
    consent.code_attempts += 1
    consent.save(update_fields=["code_attempts"])
    entered = re.sub(r"\D", "", code)
    if not hmac.compare_digest(_code_hash(entered, consent), consent.code_hash):
        raise SmsError(_("That isn't the code we sent."))
    consent.verified_at = timezone.now()
    consent.code_hash = ""
    consent.save(update_fields=["verified_at", "code_hash"])
    record(AuditAction.SMS_CONSENT_VERIFIED, request=request, actor=consent.user, target=consent)
    _send_now(
        consent,
        "welcome",
        _(
            "%(site)s: you're set up for appointment reminders. "
            "Reply STOP to opt out, HELP for help."
        )
        % {"site": settings.SYMPRAXI_SITE_NAME},
    )


def current_consent(user: User) -> SmsConsent | None:
    return SmsConsent.objects.filter(user=user, revoked_at__isnull=True).first()


def revoke(consent: SmsConsent, *, via: str, request: HttpRequest | None = None) -> bool:
    if consent.revoked_at is not None:
        return False
    consent.revoked_at = timezone.now()
    consent.revoked_via = via
    consent.save(update_fields=["revoked_at", "revoked_via"])
    record(
        AuditAction.SMS_CONSENT_REVOKED,
        request=request,
        actor=consent.user,
        target=consent,
        via=via,
    )
    return True


# --- Sending ----------


def _send_now(consent: SmsConsent, purpose: str, body: str) -> None:
    """Send at once, for a code the person is waiting on."""
    message = SmsMessage.objects.create(consent=consent, purpose=purpose, body=body)
    deliver(message.pk)


def in_quiet_hours(when: datetime, zone: str) -> bool:
    local = when.astimezone(ZoneInfo(zone)).time()
    return local < QUIET_BEFORE or local >= QUIET_AFTER


def queue_reminder(user: User, appointment: Appointment, *, now: datetime | None = None) -> bool:
    """Text a reminder if this person agreed, the center allows it, and it isn't night."""
    now = now or timezone.now()
    center = appointment.schedule.center
    if not center.sms_enabled or not available():
        return False
    consent = current_consent(user)
    if consent is None or not consent.is_active:
        return False
    with timezone.override(center.time_zone):
        when = date_format(timezone.localtime(appointment.starts_at), "D M j, g:i A")
    body = _(
        "%(site)s: reminder, your appointment at %(center)s is %(when)s. Reply STOP to opt out."
    ) % {
        "site": settings.SYMPRAXI_SITE_NAME,
        "center": center.name,
        "when": when,
    }
    if in_quiet_hours(now, center.time_zone):
        SmsMessage.objects.create(
            consent=consent,
            purpose="reminder",
            status=MessageStatus.SKIPPED,
            detail="Not texted overnight.",
        )
        return False
    message = SmsMessage.objects.create(consent=consent, purpose="reminder", body=body)
    from sympraxi.sms.tasks import send_text

    transaction.on_commit(lambda: send_text.enqueue(str(message.pk)))
    return True


def deliver(message_id: Any) -> bool:
    """Send one waiting text. Safe to run twice: only a pending message is sent."""
    with transaction.atomic():
        message = (
            SmsMessage.objects.select_for_update(of=("self",))
            .select_related("consent")
            .filter(pk=message_id, status=MessageStatus.PENDING)
            .first()
        )
        if message is None:
            return False
        consent = message.consent
        provider = providers.configured()
        # A code goes to a number not yet confirmed; everything else needs a live consent.
        if (
            provider is None
            or consent.revoked_at is not None
            or (message.purpose not in ("code",) and consent.verified_at is None)
        ):
            message.status = MessageStatus.SKIPPED
            message.detail = "No provider, or the person has opted out."
            message.body = ""
            message.save(update_fields=["status", "detail", "body"])
            return False
        try:
            sent = provider.send(phone_of(consent), message.body)
        except providers.SendError as error:
            message.status = MessageStatus.FAILED
            message.detail = str(error)[:200]
            message.body = ""
            message.save(update_fields=["status", "detail", "body"])
            if error.blocked:
                revoke(consent, via="carrier")
            return False
        message.status = MessageStatus.SENT
        message.provider_id = sent.id
        message.sent_at = timezone.now()
        message.body = ""
        message.save(update_fields=["status", "provider_id", "sent_at", "body"])
        return True


# --- Replies ----------


def handle_reply(from_number: str, text: str) -> str:
    """Act on an incoming text. Returns what to answer, or an empty string for nothing."""
    phone = normalize(from_number)
    if phone is None:
        return ""
    word = re.sub(r"[^a-z]", "", text.strip().lower().split(" ")[0] if text.strip() else "")
    matches = SmsConsent.objects.filter(phone_hash=phone_hash(phone), revoked_at__isnull=True)
    if word in STOP_WORDS:
        for consent in matches:
            revoke(consent, via="stop")
        return _(
            "%(site)s: you're unsubscribed and won't get more texts. Reply START to start again."
        ) % {"site": settings.SYMPRAXI_SITE_NAME}
    if word in HELP_WORDS:
        return _(
            "%(site)s: appointment reminders from your writing center. Message and data rates "
            "may apply. Reply STOP to cancel. Questions: %(url)s"
        ) % {"site": settings.SYMPRAXI_SITE_NAME, "url": settings.SYMPRAXI_BASE_URL or ""}
    if word in START_WORDS:
        # A person who texted STOP has to start again on the site, where they see the
        # wording they are agreeing to. Texting START does not count as that.
        return _(
            "%(site)s: to start texts again, sign in and turn them on in your account settings."
        ) % {"site": settings.SYMPRAXI_SITE_NAME}
    return ""
