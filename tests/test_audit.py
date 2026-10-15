import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.privacy.models import AuditEvent
from tests.factories import PASSWORD, UserFactory


def actions() -> list[str]:
    return list(AuditEvent.objects.order_by("id").values_list("action", flat=True))


@pytest.mark.django_db
def test_sign_in_and_out_are_recorded(client) -> None:
    user = UserFactory(email="logger@example.edu")
    client.post(reverse("account_login"), {"login": "logger@example.edu", "password": PASSWORD})
    client.post(reverse("account_logout"))
    assert actions() == [AuditAction.LOGIN, AuditAction.LOGOUT]
    login = AuditEvent.objects.get(action=AuditAction.LOGIN)
    assert login.actor_id == user.pk
    assert login.actor_label == "logger@example.edu"
    assert login.ip_address == "127.0.0.1"


@pytest.mark.django_db
def test_failed_sign_in_is_recorded_without_the_password(client) -> None:
    UserFactory(email="target@example.edu")
    client.post(
        reverse("account_login"), {"login": "target@example.edu", "password": "wrong-guess"}
    )
    event = AuditEvent.objects.get(action=AuditAction.LOGIN_FAILED)
    assert event.actor_label == "target@example.edu"
    assert "wrong-guess" not in str(event.details)


@pytest.mark.django_db
def test_forwarded_address_is_used_only_when_trusted(rf, settings) -> None:
    request = rf.get("/", HTTP_X_FORWARDED_FOR="203.0.113.9", REMOTE_ADDR="10.0.0.2")
    settings.SYMPRAXI_TRUST_X_FORWARDED_FOR = False
    assert record(AuditAction.LOGIN, request=request).ip_address == "10.0.0.2"
    settings.SYMPRAXI_TRUST_X_FORWARDED_FOR = True
    assert record(AuditAction.LOGIN, request=request).ip_address == "203.0.113.9"


@pytest.mark.django_db
def test_entries_cannot_be_changed_through_the_model() -> None:
    event = record(AuditAction.LOGIN, actor_label="x@example.edu")
    event.action = "something.else"
    with pytest.raises(ValueError, match="append-only"):
        event.save()
    with pytest.raises(ValueError, match="append-only"):
        event.delete()


@pytest.mark.django_db
def test_database_rejects_update_and_delete() -> None:
    event = record(AuditAction.LOGIN, actor_label="x@example.edu")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk).update(action="tampered")
    with pytest.raises(DatabaseError, match="append-only"), transaction.atomic():
        AuditEvent.objects.filter(pk=event.pk)._raw_delete(connection.alias)
    assert AuditEvent.objects.get(pk=event.pk).action == AuditAction.LOGIN


@pytest.mark.django_db
def test_retention_purge_can_delete_when_flagged() -> None:
    event = record(AuditAction.LOGIN, actor_label="x@example.edu")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL sympraxi.audit_purge = 'on'")
        AuditEvent.objects.filter(pk=event.pk)._raw_delete(connection.alias)
    assert not AuditEvent.objects.filter(pk=event.pk).exists()


@pytest.mark.django_db
def test_deleting_a_user_keeps_their_history() -> None:
    user = UserFactory(email="leaving@example.edu")
    record(AuditAction.LOGIN, actor=user)
    user.delete()
    event = AuditEvent.objects.get()
    assert event.actor_label == "leaving@example.edu"


def test_viewing_the_audit_log_is_itself_recorded(client, person_with_role, center) -> None:
    director = person_with_role("director", center)
    client.force_login(director)
    client.get(reverse("privacy:audit_log", kwargs={"slug": center.slug}))
    event = AuditEvent.objects.get(action=AuditAction.AUDIT_LOG_VIEWED)
    assert (event.actor_id, event.center_id) == (director.pk, center.pk)


def test_membership_changes_in_the_admin_are_recorded(client, person_with_role, center) -> None:
    admin = person_with_role("site_admin", center)
    newcomer = UserFactory()
    client.force_login(admin)
    response = client.post(
        reverse("admin:centers_membership_add"),
        {"user": newcomer.pk, "center": center.pk, "role": Role.TUTOR, "is_active": "on"},
    )
    assert response.status_code == 302
    event = AuditEvent.objects.get(action=AuditAction.MEMBERSHIP_ADDED)
    assert event.center_id == center.pk
    assert event.details == {"member": str(newcomer.pk), "role": "tutor", "active": True}


def test_every_audit_action_has_a_readable_label() -> None:
    from sympraxi.privacy.models import ACTION_LABELS

    assert set(ACTION_LABELS) == {a.value for a in AuditAction}
