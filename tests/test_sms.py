"""Text reminders: consent, the code, sending, STOP and HELP, and quiet hours."""

import base64
import hashlib
import hmac
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import reminders
from sympraxi.sms import providers, service
from sympraxi.sms.models import MessageStatus, SmsConsent, SmsMessage
from tests.factories import UserFactory
from tests.scheduling_world import build_world

pytestmark = pytest.mark.django_db


class Recorder:
    """A provider that records what it is asked to send."""

    name = "recorder"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail: providers.SendError | None = None
        self.signature_ok = True

    def send(self, to: str, body: str) -> providers.Sent:
        if self.fail:
            raise self.fail
        self.sent.append((to, body))
        return providers.Sent(id=f"SM{len(self.sent)}")

    def verify_inbound(self, request) -> bool:
        return self.signature_ok

    def code(self) -> str:
        return next(b for _, b in reversed(self.sent) if "code:" in b).split("code: ")[1][:6]


@pytest.fixture
def provider(monkeypatch):
    fake = Recorder()
    monkeypatch.setattr(providers, "configured", lambda: fake)
    return fake


@pytest.fixture
def user(db):
    return UserFactory(full_name="Pat Student")


def opt_in(user, phone="(555) 010-1234", agreed=True):
    return service.start_opt_in(user, phone, agreed=agreed)


# --- Numbers ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("555 010 1234", "+15550101234"),
        ("(555) 010-1234", "+15550101234"),
        ("1-555-010-1234", "+15550101234"),
        ("+44 20 7946 0958", "+442079460958"),
        ("12345", None),
        ("", None),
        ("+0123456789", None),
        ("abc", None),
        ("+1234567890123456", None),
    ],
)
def test_phone_numbers_are_made_into_e164(raw, expected) -> None:
    assert service.normalize(raw) == expected


# --- Opting in ----------


def test_an_unticked_box_is_not_consent(user, provider) -> None:
    with pytest.raises(service.SmsError, match="Tick the box"):
        opt_in(user, agreed=False)
    assert SmsConsent.objects.count() == 0 and provider.sent == []


def test_texts_off_without_a_provider(user, monkeypatch) -> None:
    monkeypatch.setattr(providers, "configured", lambda: None)
    with pytest.raises(service.SmsError, match="aren't set up"):
        opt_in(user)


def test_a_bad_number_is_refused(user, provider) -> None:
    with pytest.raises(service.SmsError, match="area code"):
        opt_in(user, phone="123")


def test_consent_keeps_the_wording_and_the_number_only_encrypted(user, provider) -> None:
    consent = opt_in(user)
    assert consent.consent_text == service.consent_text()
    assert "STOP" in consent.consent_text and "rates" in consent.consent_text
    assert consent.consent_version == service.CONSENT_VERSION
    assert "5550101234" not in consent.phone_encrypted
    assert service.phone_of(consent) == "+15550101234"
    assert consent.phone_hash == service.phone_hash("+15550101234")
    assert provider.sent[0][0] == "+15550101234" and "code:" in provider.sent[0][1]
    assert not consent.is_active
    assert AuditEvent.objects.filter(action=AuditAction.SMS_CONSENT_GIVEN).count() == 1


def test_the_code_confirms_the_number_and_nothing_is_sent_before_it(user, provider) -> None:
    consent = opt_in(user)
    # A reminder queued now is not sent: the number isn't confirmed.
    message = SmsMessage.objects.create(consent=consent, purpose="reminder", body="x")
    assert not service.deliver(message.pk)
    assert len(provider.sent) == 1
    with pytest.raises(service.SmsError, match="isn't the code"):
        service.confirm(consent, "000000" if provider.code() != "000000" else "111111")
    service.confirm(consent, provider.code())
    consent.refresh_from_db()
    assert consent.is_active and consent.verified_at
    assert "set up for appointment reminders" in provider.sent[-1][1]
    assert AuditEvent.objects.filter(action=AuditAction.SMS_CONSENT_VERIFIED).count() == 1
    with pytest.raises(service.SmsError, match="nothing to confirm"):
        service.confirm(consent, provider.code())


def test_too_many_wrong_codes_or_an_old_code_lock_it(user, provider) -> None:
    consent = opt_in(user)
    wrong = "000000" if provider.code() != "000000" else "111111"
    for _ in range(service.MAX_ATTEMPTS):
        with pytest.raises(service.SmsError):
            service.confirm(consent, wrong)
    with pytest.raises(service.SmsError, match="expired"):
        service.confirm(consent, provider.code())
    fresh = opt_in(user)
    fresh.code_expires_at = timezone.now() - timedelta(seconds=1)
    fresh.save()
    with pytest.raises(service.SmsError, match="expired"):
        service.confirm(fresh, provider.code())


def test_a_new_code_can_be_asked_for_once_a_minute(user, provider) -> None:
    consent = opt_in(user)
    with pytest.raises(service.SmsError, match="Wait a minute"):
        service.resend_code(consent)
    consent.code_sent_at = timezone.now() - timedelta(minutes=2)
    consent.save()
    old = provider.code()
    service.resend_code(consent)
    assert len(provider.sent) == 2
    consent.refresh_from_db()
    assert consent.code_attempts == 0
    service.confirm(consent, provider.code())
    assert old is not None


def test_starting_again_replaces_the_pending_consent(user, provider) -> None:
    first = opt_in(user)
    second = opt_in(user, phone="555 010 9999")
    first.refresh_from_db()
    assert first.revoked_at is not None and second.revoked_at is None
    assert SmsConsent.objects.filter(user=user, revoked_at__isnull=True).count() == 1


# --- Turning it off, STOP and HELP ----------


def confirmed(user, provider, phone="555 010 1234"):
    consent = opt_in(user, phone=phone)
    service.confirm(consent, provider.code())
    consent.refresh_from_db()
    return consent


def test_a_person_turns_texts_off_on_the_site(user, provider) -> None:
    consent = confirmed(user, provider)
    assert service.revoke(consent, via="web") and not service.revoke(consent, via="web")
    consent.refresh_from_db()
    assert consent.revoked_via == "web" and not consent.is_active
    assert AuditEvent.objects.filter(action=AuditAction.SMS_CONSENT_REVOKED).count() == 1


@pytest.mark.parametrize(
    "word", ["STOP", "stop", " Stop ", "unsubscribe", "CANCEL", "end", "quit", "STOPALL"]
)
def test_stop_turns_off_every_consent_on_that_number(user, provider, word) -> None:
    other = UserFactory()
    mine = confirmed(user, provider)
    theirs = confirmed(other, provider)
    unrelated = confirmed(UserFactory(), provider, phone="555 010 7777")
    answer = service.handle_reply("+1 555 010 1234", word)
    assert "unsubscribed" in answer
    for consent in (mine, theirs):
        consent.refresh_from_db()
        assert consent.revoked_via == "stop"
    unrelated.refresh_from_db()
    assert unrelated.is_active


def test_help_explains_and_start_sends_you_to_the_site(user, provider) -> None:
    consent = confirmed(user, provider)
    assert "Reply STOP" in service.handle_reply("5550101234", "HELP")
    answer = service.handle_reply("5550101234", "START")
    assert "sign in" in answer
    consent.refresh_from_db()
    assert consent.is_active  # nothing changed
    stopped = service.handle_reply("5550101234", "stop")
    assert stopped
    assert "sign in" in service.handle_reply("5550101234", "yes")
    consent.refresh_from_db()
    assert not consent.is_active  # YES doesn't turn it back on


def test_other_replies_and_unknown_numbers_get_no_answer(user, provider) -> None:
    confirmed(user, provider)
    assert service.handle_reply("5550101234", "thanks!") == ""
    assert service.handle_reply("5550101234", "") == ""
    assert service.handle_reply("not a number", "stop") == ""
    assert service.handle_reply("5559999999", "stop") != ""  # a stranger texting STOP is fine


def test_the_inbound_page_needs_the_providers_signature(client, user, provider) -> None:
    confirmed(user, provider)
    url = reverse("sms:inbound")
    provider.signature_ok = False
    assert client.post(url, {"From": "+15550101234", "Body": "STOP"}).status_code == 403
    assert current(user) is not None
    provider.signature_ok = True
    response = client.post(url, {"From": "+15550101234", "Body": "STOP"})
    assert response.status_code == 200 and "<Message>" in response.content.decode()
    assert response["Content-Type"].startswith("text/xml")
    assert current(user) is None
    assert client.get(url).status_code == 405


def current(user):
    return service.current_consent(user)


def test_twilios_signature_is_checked_the_way_twilio_signs(client, settings) -> None:
    settings.SYMPRAXI_BASE_URL = "https://writing.example.edu"
    settings.SYMPRAXI_SMS_PROVIDER = "twilio"
    settings.SYMPRAXI_TWILIO_ACCOUNT_SID = "ACtest"
    settings.SYMPRAXI_TWILIO_AUTH_TOKEN = "secret-token"
    settings.SYMPRAXI_TWILIO_FROM = "+15550000000"
    url = reverse("sms:inbound")
    fields = {"From": "+15550101234", "Body": "HELP"}
    signed = "https://writing.example.edu" + url + "".join(k + fields[k] for k in sorted(fields))
    mac = hmac.new(b"secret-token", signed.encode(), hashlib.sha1).digest()
    good = base64.b64encode(mac).decode()
    assert client.post(url, fields, HTTP_X_TWILIO_SIGNATURE=good).status_code == 200
    assert client.post(url, fields, HTTP_X_TWILIO_SIGNATURE="forged").status_code == 403
    assert client.post(url, fields).status_code == 403
    tampered = {**fields, "Body": "STOP"}
    assert client.post(url, tampered, HTTP_X_TWILIO_SIGNATURE=good).status_code == 403


def test_no_provider_means_the_inbound_page_refuses_everyone(client, monkeypatch) -> None:
    monkeypatch.setattr(providers, "configured", lambda: None)
    assert (
        client.post(reverse("sms:inbound"), {"From": "+15550101234", "Body": "STOP"}).status_code
        == 403
    )


# --- Sending reminders ----------


@pytest.fixture
def world(center):
    center.sms_enabled = True
    center.save()
    return build_world(center)


def noon(world):
    """An instant at midday in the center's zone, on the appointment's day."""
    from zoneinfo import ZoneInfo

    start = world.appointment.starts_at.astimezone(ZoneInfo(world.center.time_zone))
    return start.replace(hour=12, minute=0, second=0, microsecond=0)


def test_a_reminder_text_goes_only_to_people_who_confirmed_and_names_nobody(
    world, provider, django_capture_on_commit_callbacks
) -> None:
    consent = confirmed(world.student, provider)
    provider.sent.clear()
    with django_capture_on_commit_callbacks(execute=True):
        assert service.queue_reminder(world.student, world.appointment, now=noon(world))
    [(to, body)] = provider.sent
    assert to == "+15550101234" and world.center.name in body and "STOP" in body
    assert world.student.full_name not in body and world.tutor.full_name not in body
    message = consent.messages.get(purpose="reminder")
    assert message.status == MessageStatus.SENT and message.body == "" and message.provider_id


def test_no_text_without_consent_a_center_switch_or_a_provider(
    world, provider, monkeypatch
) -> None:
    assert not service.queue_reminder(world.student, world.appointment, now=noon(world))
    confirmed(world.student, provider)
    world.center.sms_enabled = False
    world.center.save()
    appointment = (
        type(world.appointment)
        .objects.select_related("schedule__center")
        .get(pk=world.appointment.pk)
    )
    assert not service.queue_reminder(world.student, appointment, now=noon(world))
    world.center.sms_enabled = True
    world.center.save()
    monkeypatch.setattr(providers, "configured", lambda: None)
    appointment = (
        type(world.appointment)
        .objects.select_related("schedule__center")
        .get(pk=world.appointment.pk)
    )
    assert not service.queue_reminder(world.student, appointment, now=noon(world))


def test_nothing_is_texted_overnight(world, provider) -> None:
    consent = confirmed(world.student, provider)
    provider.sent.clear()
    night = noon(world).replace(hour=23)
    early = noon(world).replace(hour=6)
    for when in (night, early):
        assert not service.queue_reminder(world.student, world.appointment, now=when)
    assert provider.sent == []
    skipped = consent.messages.filter(status=MessageStatus.SKIPPED)
    assert skipped.count() == 2 and "overnight" in skipped.first().detail


def test_the_reminder_run_texts_only_for_the_last_reminder(
    world, provider, django_capture_on_commit_callbacks
) -> None:
    confirmed(world.student, provider)
    provider.sent.clear()
    schedule = world.schedule
    schedule.reminder_1_minutes, schedule.reminder_2_minutes, schedule.reminder_3_minutes = (
        1440,
        60,
        None,
    )
    schedule.save()
    appointment = world.appointment
    afternoon = noon(world) + timedelta(hours=2)
    type(appointment).objects.filter(pk=appointment.pk).update(
        starts_at=afternoon,
        ends_at=afternoon + timedelta(minutes=30),
        blocked_until=afternoon + timedelta(minutes=30),
        updated_at=afternoon - timedelta(days=5),
        is_tentative=False,
    )
    appointment.refresh_from_db()
    # Booked well before either reminder is due, whatever the time of day the test runs.
    appointment.participants.update(added_at=afternoon - timedelta(days=5))

    def run(minutes: int) -> None:
        due = appointment.starts_at - timedelta(minutes=minutes)
        with django_capture_on_commit_callbacks(execute=True):
            sent = reminders.remind(
                appointment.pk,
                minutes,
                base_url="https://x.example",
                now=due + timedelta(minutes=1),
            )
            assert sent == 1

    run(1440)
    assert provider.sent == []  # the day-before reminder is email only
    SmsMessage.objects.all().delete()
    from sympraxi.scheduling.models import Reminder

    Reminder.objects.all().delete()
    run(60)
    assert len(provider.sent) == 1


def test_a_failed_send_is_recorded_and_a_blocked_number_ends_consent(user, provider) -> None:
    consent = confirmed(user, provider)
    message = SmsMessage.objects.create(consent=consent, purpose="reminder", body="hi")
    provider.fail = providers.SendError("carrier says no")
    assert not service.deliver(message.pk)
    message.refresh_from_db()
    assert message.status == MessageStatus.FAILED and message.body == ""
    assert consent.is_active
    blocked = SmsMessage.objects.create(consent=consent, purpose="reminder", body="hi")
    provider.fail = providers.SendError("21610", blocked=True)
    service.deliver(blocked.pk)
    consent.refresh_from_db()
    assert consent.revoked_via == "carrier"


def test_a_message_is_sent_once(user, provider) -> None:
    consent = confirmed(user, provider)
    message = SmsMessage.objects.create(consent=consent, purpose="reminder", body="hi")
    provider.sent.clear()
    assert service.deliver(message.pk) and not service.deliver(message.pk)
    assert len(provider.sent) == 1


# --- Records ----------------------------------------------------------------------------------


def test_a_students_export_shows_consent_without_the_full_number(user, provider) -> None:
    from sympraxi.centers.models import Center, Membership, Role
    from sympraxi.records import export

    center = Center.objects.create(name="C", slug="c")
    Membership.objects.create(user=user, center=center, role=Role.STUDENT)
    confirmed(user, provider)
    data = export.build(Membership.objects.get(user=user), exported_by=user)
    assert data["version"] == 11
    [consent] = data["text_message_consents"]
    assert consent["number_ends_in"] == "1234" and consent["confirmed_at"]
    assert "5550101234" not in str(data)


# --- The page ----------


def test_the_settings_page_never_pre_ticks_the_box(client, user, provider) -> None:
    client.force_login(user)
    page = client.get(reverse("sms:settings")).content.decode()
    assert 'name="agree"' in page and "checked" not in page.split('name="agree"')[1].split(">")[0]
    assert "Message and data rates may apply" in page and "Reply STOP" in page


def test_the_page_walks_through_opt_in_confirm_and_stop(client, user, provider) -> None:
    client.force_login(user)
    url = reverse("sms:settings")
    unticked = client.post(url, {"action": "start", "phone": "5550101234"})
    assert "Tick the box" in unticked.content.decode() and SmsConsent.objects.count() == 0
    client.post(url, {"action": "start", "phone": "5550101234", "agree": "on"})
    page = client.get(url).content.decode()
    assert "••• ••• 1234" in page and "5550101234" not in page
    client.post(url, {"action": "confirm", "code": provider.code()})
    assert "Text reminders are on" in client.get(url).content.decode()
    client.post(url, {"action": "stop"})
    assert "Get a text before your appointments" in client.get(url).content.decode()


def test_the_page_says_when_texts_are_not_set_up(client, user, monkeypatch) -> None:
    monkeypatch.setattr(providers, "configured", lambda: None)
    client.force_login(user)
    assert "aren't set up on this site" in client.get(reverse("sms:settings")).content.decode()
