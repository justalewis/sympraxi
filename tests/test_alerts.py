"""Early alerts, and visit confirmations a student shares with an instructor."""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from sympraxi.alerts import services
from sympraxi.alerts.models import Alert, AlertStatus, VisitShare
from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling.models import Appointment, Attendance, Participant
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db

REASON = "She told me she has stopped opening the course site and is thinking of dropping."


@pytest.fixture
def world(center):
    return build_world(center)


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


def membership(world):
    return Membership.objects.get(user=world.student, center=world.center, role=Role.STUDENT)


def raise_url(world) -> str:
    return reverse(
        "alerts:raise", kwargs={"slug": world.center.slug, "membership_id": membership(world).pk}
    )


def detail_url(alert) -> str:
    return reverse("alerts:detail", kwargs={"slug": alert.center.slug, "alert_id": alert.pk})


def post_alert(client, world, **fields):
    data = {"kind": "engagement", "reason": REASON, "student_informed": "on"}
    data.update(fields)
    return client.post(raise_url(world), data)


# --- Raising ----------


def test_a_tutor_raises_an_alert_with_a_reason_and_directors_are_told(
    client, center, world, director, django_capture_on_commit_callbacks
) -> None:
    client.force_login(world.tutor)
    with django_capture_on_commit_callbacks(execute=True):
        response = post_alert(client, world)
    alert = Alert.objects.get()
    assert response["Location"] == detail_url(alert)
    assert (alert.student, alert.raised_by, alert.status) == (
        world.student,
        world.tutor,
        AlertStatus.OPEN,
    )
    assert alert.student_informed and alert.reason == REASON
    [message] = mail.outbox
    assert message.to == [director.email]
    assert "Seems disengaged" in message.body and REASON not in message.body
    assert world.student.display_name in message.body
    event = AuditEvent.objects.get(action=AuditAction.ALERT_RAISED)
    assert event.details["kind"] == "engagement" and REASON not in str(event.details)


def test_the_notice_goes_to_the_centers_alert_address_when_it_has_one(
    client, center, world, director, django_capture_on_commit_callbacks
) -> None:
    center.alert_email = "support@example.edu"
    center.save()
    client.force_login(world.tutor)
    with django_capture_on_commit_callbacks(execute=True):
        post_alert(client, world)
    assert mail.outbox[0].to == ["support@example.edu"]


def test_an_alert_needs_a_real_reason_and_a_kind(client, center, world) -> None:
    client.force_login(world.tutor)
    short = post_alert(client, world, reason="She seems off.")
    assert "at least 15 characters" in short.content.decode()
    blank = post_alert(client, world, reason="")
    assert blank.status_code == 200
    bad = post_alert(client, world, kind="gossip")
    assert bad.status_code == 200
    assert Alert.objects.count() == 0
    with pytest.raises(services.AlertError):
        services.raise_alert(
            center, world.student, world.tutor, kind="gossip", reason=REASON, student_informed=False
        )


def test_the_database_will_not_hold_an_alert_without_a_reason(center, world) -> None:
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        Alert.objects.create(
            center=center, student=world.student, raised_by=world.tutor, kind="other", reason=""
        )


def test_a_tutor_raises_alerts_only_about_students_they_have_tutored(client, center, world) -> None:
    stranger = make_member(center, Role.STUDENT, full_name="Never Tutored")
    other = Membership.objects.get(user=stranger, center=center)
    client.force_login(world.tutor)
    assert client.get(raise_url(world)).status_code == 200
    url = reverse("alerts:raise", kwargs={"slug": center.slug, "membership_id": other.pk})
    assert client.get(url).status_code == 404
    desk = make_member(center, Role.FRONT_DESK)
    client.force_login(desk)
    assert client.get(url).status_code == 200


def test_students_cannot_raise_alerts(client, world) -> None:
    client.force_login(world.student)
    assert client.get(raise_url(world)).status_code == 403


def test_alerts_are_only_about_students_of_the_center(center, world) -> None:
    tutor = make_member(center, Role.TUTOR)
    with pytest.raises(services.AlertError, match="isn't a student"):
        services.raise_alert(
            center, tutor, world.tutor, kind="other", reason=REASON, student_informed=False
        )


# --- Reading and handling ----------


def make_alert(world, **fields) -> Alert:
    values = {
        "center": world.center,
        "student": world.student,
        "raised_by": world.tutor,
        "kind": "attendance",
        "reason": REASON,
    }
    values.update(fields)
    return Alert.objects.create(**values)


def test_the_raiser_and_directors_read_it_and_no_one_else(client, center, world, director) -> None:
    alert = make_alert(world)
    client.force_login(world.tutor)
    assert REASON in client.get(detail_url(alert)).content.decode()
    client.force_login(director)
    assert REASON in client.get(detail_url(alert)).content.decode()
    other_tutor = make_member(center, Role.TUTOR)
    client.force_login(other_tutor)
    assert client.get(detail_url(alert)).status_code == 404
    desk = make_member(center, Role.FRONT_DESK)
    client.force_login(desk)
    assert client.get(detail_url(alert)).status_code == 404
    client.force_login(world.student)
    assert client.get(detail_url(alert)).status_code == 403


def test_a_director_acknowledges_then_closes_with_an_outcome(
    client, center, world, director
) -> None:
    alert = make_alert(world)
    client.force_login(director)
    client.post(detail_url(alert), {"action": "acknowledge"})
    alert.refresh_from_db()
    assert alert.status == AlertStatus.ACKNOWLEDGED and alert.acknowledged_by == director
    refused = client.post(detail_url(alert), {"action": "close", "outcome": ""})
    assert refused.status_code == 200
    alert.refresh_from_db()
    assert alert.status == AlertStatus.ACKNOWLEDGED
    client.post(
        detail_url(alert),
        {"action": "close", "outcome": "Spoke with the student; advising has reached out."},
    )
    alert.refresh_from_db()
    assert alert.is_closed and alert.closed_by == director and "advising" in alert.outcome
    assert AuditEvent.objects.filter(action=AuditAction.ALERT_HANDLED).count() == 2
    page = client.get(detail_url(alert)).content.decode()
    assert "Close the alert" not in page


def test_the_raiser_cannot_handle_the_alert(client, world) -> None:
    alert = make_alert(world)
    client.force_login(world.tutor)
    client.post(detail_url(alert), {"action": "acknowledge"})
    alert.refresh_from_db()
    assert alert.status == AlertStatus.OPEN


def test_a_closed_alert_needs_an_outcome_at_the_database_level(world) -> None:
    from django.db import IntegrityError, transaction

    alert = make_alert(world)
    alert.status = AlertStatus.CLOSED
    with pytest.raises(IntegrityError), transaction.atomic():
        alert.save()


def test_the_list_shows_open_and_closed_and_mine_shows_my_own(
    client, center, world, director
) -> None:
    open_one = make_alert(world)
    closed = make_alert(world, status=AlertStatus.CLOSED, outcome="Done", closed_at=timezone.now())
    client.force_login(director)
    page = client.get(reverse("alerts:list", kwargs={"slug": center.slug})).content.decode()
    assert page.count(detail_url(open_one)) == 1 and detail_url(closed) in page
    other = make_member(center, Role.TUTOR)
    make_alert(world, raised_by=other)
    client.force_login(world.tutor)
    mine = client.get(reverse("alerts:mine", kwargs={"slug": center.slug})).content.decode()
    assert (
        detail_url(open_one) in mine and mine.count("/alerts/") == 2
    )  # one alert plus the list link


def test_directors_only_for_the_list(client, center, world) -> None:
    client.force_login(world.tutor)
    assert client.get(reverse("alerts:list", kwargs={"slug": center.slug})).status_code == 403


# --- Shared visits ----------


@pytest.fixture
def visit(center, world):
    """The student's appointment, made to have happened."""
    center.visit_sharing_enabled = True
    center.save()
    appointment = world.appointment
    appointment.starts_at -= timedelta(days=3)
    appointment.ends_at -= timedelta(days=3)
    appointment.blocked_until -= timedelta(days=3)
    appointment.save()
    return Participant.objects.get(appointment=appointment, student=world.student)


def share_url(center, appointment) -> str:
    return reverse("alerts:share", kwargs={"slug": center.slug, "appointment_id": appointment.pk})


def test_a_student_shares_a_visit_and_the_instructor_opens_it_without_signing_in(
    client, center, world, visit, django_capture_on_commit_callbacks
) -> None:
    visit.attendance = Attendance.ATTENDED
    visit.save()
    client.force_login(world.student)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            share_url(center, world.appointment),
            {"instructor_email": "Prof@Example.edu", "course": "ENG 101"},
        )
    assert response.status_code == 302
    share = VisitShare.objects.get()
    assert share.instructor_email == "prof@example.edu" and share.is_usable
    [message] = mail.outbox
    assert message.to == ["prof@example.edu"] and message.reply_to == [world.student.email]
    link = next(line for line in message.body.splitlines() if "/visit/" in line).strip()
    token = link.rsplit("/visit/", 1)[1].rstrip("/")
    assert share.token_hash != token and token not in share.token_hash
    client.logout()
    page = client.get(f"/visit/{token}/")
    body = page.content.decode()
    assert page.status_code == 200 and world.student.display_name in body
    assert "Staff confirmed that they came" in body and "ENG 101" in body
    assert page["Cache-Control"] == "no-store" and page["X-Robots-Tag"] == "noindex"
    share.refresh_from_db()
    assert share.opened_count == 1


def test_the_page_says_a_visit_not_marked_attended_is_not_confirmed(
    client, center, world, visit
) -> None:
    _share, token = services.share_visit(visit, instructor_email="p@example.edu", course="")
    body = client.get(f"/visit/{token}/").content.decode()
    assert "not marked this visit as attended" in body and "Staff confirmed" not in body


def test_the_page_reveals_nothing_about_the_work(client, center, world, visit) -> None:
    _share, token = services.share_visit(visit, instructor_email="p@example.edu", course="")
    body = client.get(f"/visit/{token}/").content.decode()
    assert world.tutor.display_name not in body and "topic" not in body.lower()


def test_sharing_needs_a_center_that_allows_it_and_a_visit_that_happened(
    client, center, world, visit
) -> None:
    center.visit_sharing_enabled = False
    center.save()
    with pytest.raises(services.AlertError, match="doesn't offer"):
        services.share_visit(visit, instructor_email="p@example.edu", course="")
    center.visit_sharing_enabled = True
    center.save()
    future = Appointment.objects.get(pk=world.appointment.pk)
    future.starts_at = timezone.now() + timedelta(days=1)
    future.ends_at = future.starts_at + timedelta(minutes=30)
    future.blocked_until = future.ends_at
    future.save()
    visit = Participant.objects.get(pk=visit.pk)
    with pytest.raises(services.AlertError, match="once the visit has happened"):
        services.share_visit(visit, instructor_email="p@example.edu", course="")


def test_a_student_turns_a_link_off_and_it_stops_working(client, center, world, visit) -> None:
    share, token = services.share_visit(visit, instructor_email="p@example.edu", course="")
    client.force_login(world.student)
    client.post(reverse("alerts:shares", kwargs={"slug": center.slug}), {"share": str(share.pk)})
    share.refresh_from_db()
    assert share.revoked_at is not None
    client.logout()
    assert client.get(f"/visit/{token}/").status_code == 404
    assert AuditEvent.objects.filter(action=AuditAction.VISIT_SHARE_REVOKED).count() == 1


def test_one_student_cannot_turn_off_anothers_link(client, center, world, visit) -> None:
    share, _ = services.share_visit(visit, instructor_email="p@example.edu", course="")
    client.force_login(make_member(center, Role.STUDENT))
    client.post(reverse("alerts:shares", kwargs={"slug": center.slug}), {"share": str(share.pk)})
    share.refresh_from_db()
    assert share.revoked_at is None


@pytest.mark.parametrize("token", ["", "x", "a" * 300, "not-a-real-token"])
def test_unknown_links_all_look_the_same(client, token) -> None:
    response = client.get(f"/visit/{token or 'x'}/")
    assert response.status_code == 404 and "doesn't work" in response.content.decode()


def test_a_link_expires(client, center, world, visit) -> None:
    share, token = services.share_visit(visit, instructor_email="p@example.edu", course="")
    share.expires_at = timezone.now() - timedelta(seconds=1)
    share.created_at = share.expires_at - timedelta(days=1)
    VisitShare.objects.filter(pk=share.pk).update(
        expires_at=share.expires_at, created_at=share.created_at
    )
    assert client.get(f"/visit/{token}/").status_code == 404


def test_the_share_page_is_for_students_in_the_appointment(client, center, world, visit) -> None:
    client.force_login(world.student)
    assert client.get(share_url(center, world.appointment)).status_code == 200
    client.force_login(make_member(center, Role.STUDENT))
    assert client.get(share_url(center, world.appointment)).status_code == 404
    client.force_login(world.tutor)
    assert client.get(share_url(center, world.appointment)).status_code == 403


def test_the_appointment_page_offers_sharing_only_after_the_visit(
    client, center, world, visit
) -> None:
    client.force_login(world.student)
    url = reverse(
        "scheduling:appointment",
        kwargs={"slug": center.slug, "appointment_id": world.appointment.pk},
    )
    assert "Send an instructor a confirmation" in client.get(url).content.decode()
    center.visit_sharing_enabled = False
    center.save()
    assert "Send an instructor a confirmation" not in client.get(url).content.decode()


# --- Records ----------


def test_a_students_export_lists_alerts_without_the_staff_members_words(world, visit) -> None:
    from sympraxi.records import export

    make_alert(world)
    services.share_visit(visit, instructor_email="p@example.edu", course="ENG 101")
    data = export.build(membership(world), exported_by=world.student)
    assert data["version"] == 10
    [alert] = data["early_alerts"]
    assert alert["kind"] == "Stopped coming or missed sessions" and "reason" not in alert
    assert REASON not in str(data)
    [share] = data["shared_visits"]
    assert share["instructor_email"] == "p@example.edu" and share["course"] == "ENG 101"
