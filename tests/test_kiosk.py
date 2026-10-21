"""The check-in kiosk, the waiting room, and arrivals that keep a student from being flagged."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import attendance
from sympraxi.scheduling.models import Participant
from sympraxi.staff import kiosk
from sympraxi.staff.models import Arrival, ArrivalStatus
from tests.factories import UserFactory
from tests.scheduling_world import assign, make_member, make_schedule

pytestmark = pytest.mark.django_db


@pytest.fixture
def desk(center):
    return make_member(center, Role.FRONT_DESK, full_name="Fran Desk")


@pytest.fixture
def tutor(center):
    return make_member(center, Role.TUTOR, full_name="Toni Tutor")


@pytest.fixture
def student(center):
    person = make_member(center, Role.STUDENT, full_name="Pat Student", student_id="S1234567")
    return person


def kiosk_url(center) -> str:
    return reverse("staff:kiosk", kwargs={"slug": center.slug})


def room_url(center) -> str:
    return reverse("staff:waiting_room", kwargs={"slug": center.slug})


def book(center, tutor, student, *, starts_in: timedelta, minutes: int = 30):
    """An appointment for the student starting `starts_in` from now."""
    from sympraxi.scheduling.models import Appointment, Modality

    today = timezone.localdate()
    schedule = make_schedule(center, today - timedelta(days=2), today + timedelta(days=2))
    assignment = assign(schedule, tutor)
    starts = timezone.now() + starts_in
    appointment = Appointment.objects.create(
        schedule=schedule,
        assignment=assignment,
        tutor=tutor,
        starts_at=starts,
        ends_at=starts + timedelta(minutes=minutes),
        blocked_until=starts + timedelta(minutes=minutes),
        modality=Modality.IN_PERSON,
    )
    Participant.objects.create(appointment=appointment, student=student)
    return appointment


# --- Reading a card --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("S1234567", "S1234567"),
        ("  s123 4567 \n", "s1234567"),
        (";123456789?", "123456789"),
        ("%123456789?", "123456789"),
        (";?", ""),
        ("", ""),
    ],
)
def test_the_card_reader_and_the_keyboard_give_the_same_id(typed, expected) -> None:
    assert kiosk.parse_card(typed) == expected


# --- The kiosk -------------------------------------------------------------------------


def test_a_student_with_an_appointment_checks_in_for_it(
    client, center, desk, tutor, student
) -> None:
    appointment = book(center, tutor, student, starts_in=timedelta(minutes=10))
    client.force_login(desk)
    page = client.post(kiosk_url(center), {"student_id": "s1234567"}).content.decode()
    assert "Hi, Pat." in page and "appointment with Toni Tutor" in page
    arrival = Arrival.objects.get()
    assert (arrival.student, arrival.appointment, arrival.via) == (student, appointment, "kiosk")
    assert 'http-equiv="refresh"' in page
    assert "Pat Student" not in page  # first name only
    assert AuditEvent.objects.filter(action=AuditAction.ARRIVAL_RECORDED).count() == 1


def test_a_card_swipe_checks_in(client, center, desk, student) -> None:
    client.force_login(desk)
    client.post(kiosk_url(center), {"student_id": ";S1234567?"})
    assert Arrival.objects.get().student == student


def test_a_student_without_an_appointment_is_a_walk_in(client, center, desk, student) -> None:
    client.force_login(desk)
    page = client.post(
        kiosk_url(center), {"student_id": "S1234567", "reason": "Thesis statement"}
    ).content.decode()
    assert "someone will be with you soon" in page
    arrival = Arrival.objects.get()
    assert arrival.appointment is None and arrival.reason == "Thesis statement"


def test_an_appointment_far_off_or_over_does_not_count(
    client, center, desk, tutor, student
) -> None:
    book(center, tutor, student, starts_in=timedelta(hours=3))
    book(center, make_member(center, Role.TUTOR), student, starts_in=timedelta(hours=-3))
    client.force_login(desk)
    client.post(kiosk_url(center), {"student_id": "S1234567"})
    assert Arrival.objects.get().appointment is None


def test_checking_in_twice_changes_nothing(client, center, desk, student) -> None:
    client.force_login(desk)
    client.post(kiosk_url(center), {"student_id": "S1234567"})
    page = client.post(kiosk_url(center), {"student_id": "S1234567"}).content.decode()
    assert "already checked in" in page and Arrival.objects.count() == 1


def test_an_unknown_id_says_nothing_about_who_exists(client, center, desk, student) -> None:
    other = make_member(center, Role.TUTOR, student_id="T999")
    client.force_login(desk)
    unknown = client.post(kiosk_url(center), {"student_id": "nope"}).content.decode()
    staff_id = client.post(kiosk_url(center), {"student_id": other.student_id}).content.decode()
    assert "couldn&#x27;t find that ID" in unknown and "couldn&#x27;t find that ID" in staff_id
    assert Arrival.objects.count() == 0


def test_a_student_from_another_center_is_not_found(client, center, other_center, desk) -> None:
    make_member(other_center, Role.STUDENT, student_id="X1")
    client.force_login(desk)
    assert (
        "couldn&#x27;t find"
        in client.post(kiosk_url(center), {"student_id": "X1"}).content.decode()
    )


def test_a_deactivated_student_is_not_found(client, center, desk, student) -> None:
    student.is_active = False
    student.save()
    client.force_login(desk)
    client.post(kiosk_url(center), {"student_id": "S1234567"})
    assert Arrival.objects.count() == 0


def test_the_kiosk_works_only_from_the_centers_network_when_one_is_set(
    client, center, desk, student
) -> None:
    center.clock_networks = "203.0.113.0/24"
    center.save()
    client.force_login(desk)
    outside = client.post(kiosk_url(center), {"student_id": "S1234567"}, REMOTE_ADDR="198.51.100.7")
    assert "only works from the center&#x27;s network" in outside.content.decode()
    assert Arrival.objects.count() == 0
    client.post(kiosk_url(center), {"student_id": "S1234567"}, REMOTE_ADDR="203.0.113.5")
    assert Arrival.objects.count() == 1


def test_students_and_tutors_cannot_open_the_kiosk(client, center, tutor, student) -> None:
    for person in (tutor, student):
        client.force_login(person)
        assert client.get(kiosk_url(center)).status_code == 403


# --- The waiting room ------------------------------------------------------------------


def test_the_waiting_room_lists_the_longest_wait_first(
    client, center, desk, tutor, student
) -> None:
    other = make_member(center, Role.STUDENT, full_name="Quinn Second", student_id="S2")
    first = Arrival.objects.create(
        center=center, student=student, arrived_at=timezone.now() - timedelta(minutes=9)
    )
    Arrival.objects.create(center=center, student=other, reason="Resume")
    client.force_login(tutor)
    page = client.get(room_url(center)).content.decode()
    assert page.index("Pat Student") < page.index("Quinn Second")
    assert "Resume" in page and "Check a student in" not in page  # tutors only look
    assert str(first.pk) in page


def test_staff_mark_a_student_seen_or_gone(client, center, desk, student) -> None:
    arrival = Arrival.objects.create(center=center, student=student)
    client.force_login(desk)
    client.post(room_url(center), {"action": "seen", "arrival": str(arrival.pk)})
    arrival.refresh_from_db()
    assert arrival.status == ArrivalStatus.SEEN and arrival.handled_by == desk
    event = AuditEvent.objects.get(action=AuditAction.ARRIVAL_HANDLED)
    assert event.details["status"] == "seen"
    page = client.get(room_url(center)).content.decode()
    assert "Nobody is waiting." in page and "Pat Student" in page  # in "seen today"
    client.post(room_url(center), {"action": "left", "arrival": str(arrival.pk)})
    arrival.refresh_from_db()
    assert arrival.status == ArrivalStatus.SEEN  # the second click changes nothing


def test_front_desk_checks_a_student_in_from_the_waiting_room(
    client, center, desk, student
) -> None:
    client.force_login(desk)
    client.post(room_url(center), {"action": "check_in", "student_id": "S1234567"})
    arrival = Arrival.objects.get()
    assert arrival.via == "staff"
    bad = client.post(room_url(center), {"action": "check_in", "student_id": "zzz"})
    assert "No student in this center has that ID" in bad.content.decode()


def test_tutors_cannot_check_students_in(client, center, tutor, student) -> None:
    client.force_login(tutor)
    client.post(room_url(center), {"action": "check_in", "student_id": "S1234567"})
    assert Arrival.objects.count() == 0


def test_another_centers_arrival_cannot_be_marked(client, center, other_center, desk) -> None:
    outsider = make_member(other_center, Role.STUDENT, student_id="O1")
    arrival = Arrival.objects.create(center=other_center, student=outsider)
    client.force_login(desk)
    client.post(room_url(center), {"action": "seen", "arrival": str(arrival.pk)})
    arrival.refresh_from_db()
    assert arrival.status == ArrivalStatus.WAITING


def test_one_waiting_arrival_per_student(center, student) -> None:
    Arrival.objects.create(center=center, student=student)
    with pytest.raises(IntegrityError), transaction.atomic():
        Arrival.objects.create(center=center, student=student)
    seen = Arrival.objects.get()
    seen.status = ArrivalStatus.SEEN
    seen.save()
    Arrival.objects.create(center=center, student=student)  # a later visit is fine


# --- Arriving keeps a student from being flagged ----------------------------------------------


def test_a_student_who_checked_in_is_not_flagged_as_a_possible_no_show(
    center, desk, tutor, student
) -> None:
    came = book(center, tutor, student, starts_in=timedelta(minutes=-40))
    missed_student = UserFactory()
    from sympraxi.centers.models import Membership

    Membership.objects.create(user=missed_student, center=center, role=Role.STUDENT)
    missed = book(
        center, make_member(center, Role.TUTOR), missed_student, starts_in=timedelta(minutes=-40)
    )
    Arrival.objects.create(center=center, student=student, appointment=came)
    assert attendance.flag_possible_no_shows() == 1
    assert Participant.objects.get(appointment=came).flagged_at is None
    assert Participant.objects.get(appointment=missed).flagged_at is not None
