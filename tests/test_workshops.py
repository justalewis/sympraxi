"""Workshops: setup, registering, rosters, attendance, canceling, and the exports."""

import csv
import io
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.workshops import services
from sympraxi.workshops.models import Attendance, Registration, WorkshopStatus
from tests.scheduling_world import make_member
from tests.workshops_world import make_workshop

pytestmark = pytest.mark.django_db


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


@pytest.fixture
def leader(center):
    return make_member(center, Role.TUTOR, full_name="Toni Leader")


@pytest.fixture
def student(center):
    return make_member(center, Role.STUDENT, full_name="Pat Student", student_id="S100")


def url(name: str, center, workshop=None) -> str:
    kwargs = {"slug": center.slug}
    if workshop is not None:
        kwargs["workshop_id"] = workshop.pk
    return reverse(f"workshops:{name}", kwargs=kwargs)


# --- Seeing workshops ----------


def test_students_see_published_workshops_only(client, center, student, leader) -> None:
    make_workshop(center, leader, title="Published one")
    make_workshop(center, leader, title="A draft", status=WorkshopStatus.DRAFT)
    make_workshop(center, leader, title="Called off", status=WorkshopStatus.CANCELED)
    client.force_login(student)
    page = client.get(url("list", center)).content.decode()
    assert "Published one" in page and "A draft" not in page and "Called off" not in page


def test_a_student_cannot_open_a_draft(client, center, student) -> None:
    draft = make_workshop(center, None, status=WorkshopStatus.DRAFT)
    client.force_login(student)
    assert client.get(url("detail", center, draft)).status_code == 404


def test_directors_see_drafts_and_a_tutor_sees_one_they_lead(
    client, center, director, leader
) -> None:
    mine = make_workshop(center, leader, title="Mine as leader", status=WorkshopStatus.DRAFT)
    make_workshop(center, None, title="Someone elses draft", status=WorkshopStatus.DRAFT)
    client.force_login(director)
    assert "Someone elses draft" in client.get(url("list", center)).content.decode()
    client.force_login(leader)
    page = client.get(url("list", center)).content.decode()
    assert "Mine as leader" in page and "Someone elses draft" not in page
    assert client.get(url("detail", center, mine)).status_code == 200


# --- Registering ----------


def test_a_student_registers_and_is_emailed(
    client, center, student, django_capture_on_commit_callbacks
) -> None:
    workshop = make_workshop(center, None, location="Library 214")
    client.force_login(student)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(url("register", center, workshop), follow=True)
    assert "You&#x27;re registered" in response.content.decode()
    registration = Registration.objects.get()
    assert registration.student == student and registration.registered_by == student
    [message] = mail.outbox
    assert message.to == [student.email] and "Thesis statements" in message.subject
    assert "Library 214" in message.body
    assert AuditEvent.objects.filter(action=AuditAction.WORKSHOP_REGISTERED).count() == 1


def test_a_workshop_cannot_be_registered_twice_or_past_its_seats(client, center, student) -> None:
    workshop = make_workshop(center, None, capacity=1)
    other = make_member(center, Role.STUDENT)
    services.register(workshop, other, by=other)
    client.force_login(student)
    full = client.post(url("register", center, workshop), follow=True)
    assert "This workshop is full." in full.content.decode()
    assert Registration.objects.count() == 1
    with pytest.raises(services.WorkshopError, match="already registered"):
        services.register(workshop, other, by=other)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"status": WorkshopStatus.DRAFT}, "isn't open"),
        ({"status": WorkshopStatus.CANCELED}, "isn't open"),
        ({"starts_in": timedelta(minutes=-5)}, "already started"),
    ],
)
def test_registration_checks_the_workshop(center, student, fields, message) -> None:
    workshop = make_workshop(center, None, **fields)
    with pytest.raises(services.WorkshopError, match=message):
        services.register(workshop, student, by=student)


def test_only_students_of_the_center_can_register(center, other_center, leader) -> None:
    workshop = make_workshop(center, None)
    outsider = make_member(other_center, Role.STUDENT)
    with pytest.raises(services.WorkshopError, match="Only students"):
        services.register(workshop, outsider, by=outsider)
    with pytest.raises(services.WorkshopError, match="Only students"):
        services.register(workshop, leader, by=leader)


def test_a_tutor_cannot_register_through_the_page(client, center, leader) -> None:
    workshop = make_workshop(center, None)
    client.force_login(leader)
    assert client.post(url("register", center, workshop)).status_code == 403


def test_canceling_a_registration_frees_the_seat_and_it_can_be_restored(
    client, center, student
) -> None:
    workshop = make_workshop(center, None, capacity=1)
    client.force_login(student)
    client.post(url("register", center, workshop))
    client.post(url("register", center, workshop), {"action": "cancel"})
    registration = Registration.objects.get()
    assert not registration.is_active and workshop.seats_left() == 1
    client.post(url("register", center, workshop))
    assert Registration.objects.get().is_active and Registration.objects.count() == 1


def test_a_started_workshop_cannot_be_left(center, student) -> None:
    workshop = make_workshop(center, None)
    registration = services.register(workshop, student, by=student)
    workshop.starts_at = timezone.now() - timedelta(minutes=10)
    workshop.ends_at = timezone.now() + timedelta(minutes=50)
    workshop.save()
    with pytest.raises(services.WorkshopError, match="already started"):
        services.unregister(registration, by=student)


# --- Setup ----------


def post_data(**fields) -> dict[str, str]:
    day = (timezone.localdate() + timedelta(days=5)).isoformat()
    data = {
        "title": "Citing sources",
        "description": "MLA and APA basics",
        "day": day,
        "start": "14:00",
        "end": "15:00",
        "location": "Room 5",
        "meeting_url": "",
        "leader": "",
        "capacity": "10",
        "status": "published",
    }
    data.update(fields)
    return data


def test_a_director_creates_a_workshop_in_the_centers_time_zone(client, center, director) -> None:
    center.time_zone = "America/Chicago"
    center.save()
    client.force_login(director)
    response = client.post(url("create", center), post_data())
    assert response.status_code == 302
    workshop = center.workshops.get()
    assert workshop.title == "Citing sources" and workshop.is_published
    assert workshop.starts_at.astimezone(ZoneInfo("America/Chicago")).hour == 14
    assert workshop.starts_at.hour != 14  # stored in UTC
    assert AuditEvent.objects.filter(action=AuditAction.WORKSHOP_SAVED).count() == 1


def test_the_form_checks_times_and_seats(client, center, director) -> None:
    client.force_login(director)
    bad = client.post(url("create", center), post_data(start="15:00", end="14:00"))
    assert "end must be after the start" in bad.content.decode()
    assert center.workshops.count() == 0
    workshop = make_workshop(center, None, capacity=5)
    for _ in range(3):
        student = make_member(center, Role.STUDENT)
        services.register(workshop, student, by=student)
    too_few = client.post(url("edit", center, workshop), post_data(capacity="2"))
    assert "already registered" in too_few.content.decode()


def test_only_directors_set_up_workshops(client, center, leader, student) -> None:
    for person in (leader, student):
        client.force_login(person)
        assert client.get(url("create", center)).status_code == 403


def test_canceling_a_workshop_tells_the_registered_students(
    client, center, director, django_capture_on_commit_callbacks
) -> None:
    workshop = make_workshop(center, None)
    students = [make_member(center, Role.STUDENT) for _ in range(2)]
    for s in students:
        services.register(workshop, s, by=s)
    mail.outbox.clear()
    client.force_login(director)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            url("edit", center, workshop),
            {"cancel_workshop": "1", "cancel-reason": "The leader is ill"},
        )
    workshop.refresh_from_db()
    assert workshop.is_canceled and workshop.cancel_reason == "The leader is ill"
    assert sorted(m.to[0] for m in mail.outbox) == sorted(s.email for s in students)
    assert "canceled" in mail.outbox[0].body
    with pytest.raises(services.WorkshopError):
        services.register(workshop, make_member(center, Role.STUDENT), by=director)
    again = client.get(url("edit", center, workshop)).content.decode()
    assert "can't be changed" in again


def test_a_draft_that_is_canceled_tells_no_one(center, director, student) -> None:
    workshop = make_workshop(center, None, status=WorkshopStatus.DRAFT)
    Registration.objects.create(workshop=workshop, student=student)
    mail.outbox.clear()
    services.cancel_workshop(workshop, reason="never mind", by=director)
    assert mail.outbox == []


# --- Rosters and attendance ----------


def started(center, leader=None, **fields):
    workshop = make_workshop(center, leader, **fields)
    workshop.starts_at = timezone.now() - timedelta(minutes=20)
    workshop.ends_at = timezone.now() + timedelta(minutes=40)
    workshop.save()
    return workshop


def test_the_leader_runs_the_roster_and_other_tutors_cannot(
    client, center, leader, student
) -> None:
    workshop = make_workshop(center, leader)
    services.register(workshop, student, by=student)
    other_tutor = make_member(center, Role.TUTOR)
    client.force_login(other_tutor)
    assert client.get(url("roster", center, workshop)).status_code == 404
    client.force_login(leader)
    page = client.get(url("roster", center, workshop)).content.decode()
    assert "Pat Student" in page and "Attendance can be marked once" in page


def test_attendance_is_marked_once_the_workshop_starts(client, center, leader, student) -> None:
    workshop = started(center, leader)
    registration = Registration.objects.create(workshop=workshop, student=student)
    client.force_login(leader)
    client.post(
        url("roster", center, workshop),
        {"action": "mark", "registration": str(registration.pk), "attendance": "attended"},
    )
    registration.refresh_from_db()
    assert registration.attendance == Attendance.ATTENDED and registration.marked_by == leader
    event = AuditEvent.objects.get(action=AuditAction.WORKSHOP_ATTENDANCE_MARKED)
    assert event.details["attendance"] == "attended"
    client.post(
        url("roster", center, workshop),
        {"action": "mark", "registration": str(registration.pk), "attendance": ""},
    )
    registration.refresh_from_db()
    assert registration.attendance == "" and registration.marked_at is None


def test_attendance_cannot_be_marked_early_or_wrongly(center, leader, student) -> None:
    early = make_workshop(center, leader)
    registration = Registration.objects.create(workshop=early, student=student)
    with pytest.raises(services.WorkshopError, match="once the workshop has started"):
        services.mark(registration, Attendance.ATTENDED, by=leader)
    running = started(center, leader)
    registration = Registration.objects.create(workshop=running, student=student)
    with pytest.raises(services.WorkshopError, match="Choose came"):
        services.mark(registration, "maybe", by=leader)


def test_staff_add_and_remove_students_at_the_door(client, center, director, student) -> None:
    workshop = make_workshop(center, None)
    client.force_login(director)
    by_id = client.post(url("roster", center, workshop), {"action": "add", "student": "S100"})
    assert by_id.status_code == 302 and Registration.objects.get().student == student
    assert Registration.objects.get().registered_by == director
    nobody = client.post(url("roster", center, workshop), {"action": "add", "student": "zzz"})
    assert "No student in this center" in nobody.content.decode()
    client.post(
        url("roster", center, workshop),
        {"action": "remove", "registration": str(Registration.objects.get().pk)},
    )
    assert not Registration.objects.get().is_active


def test_a_registration_from_another_workshop_cannot_be_marked(
    client, center, director, student
) -> None:
    one = started(center, None)
    two = started(center, None)
    foreign = Registration.objects.create(workshop=two, student=student)
    client.force_login(director)
    client.post(
        url("roster", center, one),
        {"action": "mark", "registration": str(foreign.pk), "attendance": "attended"},
    )
    foreign.refresh_from_db()
    assert foreign.attendance == ""


def test_the_roster_downloads_as_csv_and_is_logged(client, center, director, student) -> None:
    workshop = started(center, None)
    Registration.objects.create(workshop=workshop, student=student, attendance="attended")
    client.force_login(director)
    response = client.get(url("roster", center, workshop), {"format": "csv"})
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[1] == ["Name", "Email", "Student ID", "Registered", "Attendance"]
    assert rows[2][0] == "Pat Student" and rows[2][2] == "S100" and rows[2][4] == "Came"
    assert AuditEvent.objects.filter(action=AuditAction.WORKSHOP_ROSTER_DOWNLOADED).count() == 1


# --- Exports ----------


def test_a_students_export_lists_their_workshops(center, student) -> None:
    from sympraxi.centers.models import Membership
    from sympraxi.records import export

    workshop = make_workshop(center, None, title="Thesis statements")
    services.register(workshop, student, by=student)
    membership = Membership.objects.get(user=student, center=center, role=Role.STUDENT)
    data = export.build(membership, exported_by=student)
    assert data["version"] == 11
    assert [w["workshop"] for w in data["workshops"]] == ["Thesis statements"]
    assert data["waiting_list"] == [] and data["check_ins"] == []
