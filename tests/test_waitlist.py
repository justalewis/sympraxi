"""The waiting list: joining, offers that hold a canceled time, accepting, and the sweep."""

import time as clock
from datetime import date, time, timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import services, waitlist
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Modality,
    WaitingEntry,
    WaitingStatus,
)
from tests.scheduling_world import assign, at, make_member, make_schedule

pytestmark = pytest.mark.django_db


class Setup:
    """A schedule with a waiting list, one tutor, a booked 10:00 half hour, and a desk."""

    def __init__(self, center, capture) -> None:
        self.center = center
        self.capture = capture
        self.tomorrow: date = timezone.localdate() + timedelta(days=2)
        self.schedule = make_schedule(
            center,
            self.tomorrow - timedelta(days=3),
            self.tomorrow + timedelta(days=10),
            waitlist_enabled=True,
            waitlist_hold_minutes=15,
        )
        self.tutor = make_member(center, Role.TUTOR, full_name="Toni Tutor")
        self.assignment = assign(
            self.schedule, self.tutor, offers_online=True, meeting_url="https://example.org/room"
        )
        self.desk = make_member(center, Role.FRONT_DESK)
        self.holder = make_member(center, Role.STUDENT, full_name="Holder Student")
        self.appointment = services.book(
            actor=self.holder,
            assignment=self.assignment,
            starts_at=at(self.schedule, self.tomorrow, 10),
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[self.holder],
            as_staff=False,
        )

    def student(self, name: str):
        return make_member(self.center, Role.STUDENT, full_name=name)

    def wait(self, student, *, earliest=time(9), latest=time(12), minutes=30, **fields):
        values = {
            "day": self.tomorrow,
            "earliest": earliest,
            "latest": latest,
            "minutes": minutes,
            "modality": Modality.IN_PERSON,
            "tutor": None,
        }
        values.update(fields)
        clock.sleep(0.02)  # joined_at orders the list; the Windows clock ticks about every 15 ms
        return waitlist.join(student, self.schedule, **values)

    def cancel(self) -> None:
        with self.capture(execute=True):
            services.cancel(
                actor=self.desk, appointment=self.appointment, as_staff=True, notify=False
            )


@pytest.fixture
def lst(center, django_capture_on_commit_callbacks):
    return Setup(center, django_capture_on_commit_callbacks)


def offered(entry: WaitingEntry) -> bool:
    entry.refresh_from_db()
    return entry.status == WaitingStatus.OFFERED


# --- Joining ----------


def test_a_student_joins_and_can_only_have_one_wish_a_day(lst) -> None:
    pat = lst.student("Pat")
    entry = lst.wait(pat)
    assert entry.status == WaitingStatus.WAITING
    assert AuditEvent.objects.filter(action=AuditAction.WAITLIST_JOINED).count() == 1
    with pytest.raises(waitlist.WaitlistError, match="already on the waiting list"):
        lst.wait(pat)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"day": timezone.localdate() - timedelta(days=1)}, "hasn't passed"),
        ({"day": timezone.localdate() + timedelta(days=400)}, "further ahead"),
        ({"earliest": time(12), "latest": time(11)}, "latest time has to be after"),
        ({"earliest": time(10), "latest": time(10, 15)}, "shorter than"),
        ({"minutes": 17, "latest": time(13)}, "length"),
    ],
)
def test_joining_checks_the_wish(lst, fields, message) -> None:
    with pytest.raises(waitlist.WaitlistError, match=message):
        lst.wait(lst.student("Pat"), **fields)


def test_a_schedule_without_a_waiting_list_refuses(lst) -> None:
    lst.schedule.waitlist_enabled = False
    lst.schedule.save()
    with pytest.raises(waitlist.WaitlistError, match="doesn't have a waiting list"):
        lst.wait(lst.student("Pat"))


def test_the_tutor_must_be_on_the_schedule_and_the_list_has_a_ceiling(lst) -> None:
    outsider = make_member(lst.center, Role.TUTOR)
    with pytest.raises(waitlist.WaitlistError, match="isn't on this schedule"):
        lst.wait(lst.student("Pat"), tutor=outsider)
    pat = lst.student("Quinn")
    for offset in range(waitlist.MAX_ACTIVE):
        lst.wait(pat, day=lst.tomorrow + timedelta(days=offset))
    with pytest.raises(waitlist.WaitlistError, match="at a time"):
        lst.wait(pat, day=lst.tomorrow + timedelta(days=waitlist.MAX_ACTIVE))


# --- Offering a canceled time ----------


def test_a_canceled_time_is_held_for_the_first_student_it_suits(lst) -> None:
    first = lst.wait(lst.student("First"))
    second = lst.wait(lst.student("Second"))
    lst.cancel()
    assert offered(first) and not offered(second)
    hold = first.hold
    assert hold.is_placeholder and hold.status == AppointmentStatus.SCHEDULED
    assert hold.starts_at == lst.appointment.starts_at
    assert first.offer_expires_at == first.offered_at + timedelta(minutes=15)
    [message] = mail.outbox
    assert message.to == [first.student.email] and "A time opened up" in message.subject
    assert "Take this time" in message.body and "waiting-list" in message.body
    assert AuditEvent.objects.filter(action=AuditAction.WAITLIST_OFFERED).count() == 1


def test_nobody_else_can_book_a_time_while_it_is_held(lst) -> None:
    lst.wait(lst.student("First"))
    lst.cancel()
    stranger = lst.student("Stranger")
    with pytest.raises(services.BookingError):
        services.book(
            actor=stranger,
            assignment=lst.assignment,
            starts_at=lst.appointment.starts_at,
            minutes=30,
            modality=Modality.IN_PERSON,
            students=[stranger],
            as_staff=False,
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"earliest": time(10, 30), "latest": time(12)},  # starts too late for the window
        {"earliest": time(8), "latest": time(10, 20)},  # would finish after the window ends
        {"minutes": 60, "latest": time(12)},  # more than the canceled half hour
        {
            "modality": Modality.ONLINE
        },  # wants to meet another way than the held slot's tutor? offered
        {"day": date.today() + timedelta(days=5)},  # a different day
    ],
)
def test_wishes_that_do_not_fit_are_left_alone(lst, fields) -> None:
    if fields.get("modality") == Modality.ONLINE:
        lst.assignment.offers_online = False
        lst.assignment.offers_in_person = True
        lst.assignment.save()
    entry = lst.wait(lst.student("Picky"), **fields)
    lst.cancel()
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.WAITING and mail.outbox == []


def test_a_wish_for_another_tutor_is_skipped_and_for_this_tutor_is_offered(lst) -> None:
    other = make_member(lst.center, Role.TUTOR, full_name="Other Tutor")
    assign(lst.schedule, other)
    wants_other = lst.wait(lst.student("Wants Other"), tutor=other)
    wants_toni = lst.wait(lst.student("Wants Toni"), tutor=lst.tutor)
    lst.cancel()
    assert not offered(wants_other) and offered(wants_toni)


def test_a_student_the_rules_would_refuse_is_skipped(lst) -> None:
    lst.schedule.max_per_day = 1
    lst.schedule.save()
    busy = lst.student("Busy")
    services.book(
        actor=busy,
        assignment=lst.assignment,
        starts_at=at(lst.schedule, lst.tomorrow, 15),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[busy],
        as_staff=False,
    )
    skipped = lst.wait(busy)
    next_in_line = lst.wait(lst.student("Next"))
    lst.cancel()
    assert not offered(skipped) and offered(next_in_line)
    assert Appointment.objects.filter(is_placeholder=True).count() == 1


def test_no_waiting_list_means_no_offer(lst) -> None:
    entry = lst.wait(lst.student("Pat"))
    lst.schedule.waitlist_enabled = False
    lst.schedule.save()
    lst.cancel()
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.WAITING


def test_moving_an_appointment_away_offers_the_time_it_left(
    lst, django_capture_on_commit_callbacks
) -> None:
    entry = lst.wait(lst.student("Pat"))
    with django_capture_on_commit_callbacks(execute=True):
        services.reschedule(
            actor=lst.desk,
            appointment=lst.appointment,
            starts_at=at(lst.schedule, lst.tomorrow, 14),
            minutes=30,
            as_staff=True,
            notify=False,
        )
    assert offered(entry)
    assert entry.hold.starts_at == at(lst.schedule, lst.tomorrow, 10)


# --- Taking, declining, leaving, running out ----------


def test_accepting_books_the_student_even_inside_the_minimum_notice(lst) -> None:
    lst.schedule.min_notice_minutes = 60 * 24 * 7  # a week: no one could book this online
    lst.schedule.save()
    pat = lst.student("Pat")
    entry = lst.wait(pat)
    lst.cancel()
    appointment = waitlist.accept(entry)
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.BOOKED and entry.appointment == appointment
    assert appointment.starts_at == lst.appointment.starts_at
    assert [p.student for p in appointment.participants.all()] == [pat]
    assert entry.hold.status == AppointmentStatus.CANCELED
    assert AuditEvent.objects.filter(action=AuditAction.WAITLIST_ACCEPTED).count() == 1


def test_a_hold_that_ran_out_cannot_be_taken(lst) -> None:
    entry = lst.wait(lst.student("Pat"))
    lst.cancel()
    entry.refresh_from_db()
    with pytest.raises(waitlist.WaitlistError, match="run out"):
        waitlist.accept(entry, now=entry.offer_expires_at + timedelta(seconds=1))


def test_accepting_fails_cleanly_if_a_rule_now_stops_the_student(lst) -> None:
    lst.schedule.max_per_day = 1
    lst.schedule.save()
    pat = lst.student("Pat")
    entry = lst.wait(pat)
    lst.cancel()
    # After the offer, Pat books another time the same day through the front desk's open list.
    services.book(
        actor=pat,
        assignment=lst.assignment,
        starts_at=at(lst.schedule, lst.tomorrow, 15),
        minutes=30,
        modality=Modality.IN_PERSON,
        students=[pat],
        as_staff=False,
    )
    entry.refresh_from_db()
    with pytest.raises(waitlist.WaitlistError):
        waitlist.accept(entry)
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.OFFERED
    assert entry.hold.status == AppointmentStatus.SCHEDULED  # the hold is still there


def test_declining_offers_the_time_to_the_next_student(
    lst, django_capture_on_commit_callbacks
) -> None:
    first = lst.wait(lst.student("First"))
    second = lst.wait(lst.student("Second"))
    lst.cancel()
    with django_capture_on_commit_callbacks(execute=True):
        assert waitlist.decline(first)
    first.refresh_from_db()
    assert (
        first.status == WaitingStatus.DECLINED and first.hold.status == AppointmentStatus.CANCELED
    )
    assert offered(second)
    assert len(mail.outbox) == 2


def test_leaving_while_offered_passes_the_time_on(lst, django_capture_on_commit_callbacks) -> None:
    first = lst.wait(lst.student("First"))
    second = lst.wait(lst.student("Second"))
    lst.cancel()
    with django_capture_on_commit_callbacks(execute=True):
        assert waitlist.leave(first, actor=first.student)
    assert offered(second)
    assert not waitlist.leave(first, actor=first.student)  # already closed


def test_the_sweep_expires_an_unanswered_offer_and_offers_the_next(
    lst, django_capture_on_commit_callbacks
) -> None:
    first = lst.wait(lst.student("First"))
    second = lst.wait(lst.student("Second"))
    lst.cancel()
    first.refresh_from_db()
    later = first.offer_expires_at + timedelta(minutes=1)
    with django_capture_on_commit_callbacks(execute=True):
        assert waitlist.sweep(later) == 1
    first.refresh_from_db()
    assert first.status == WaitingStatus.EXPIRED
    assert first.hold.status == AppointmentStatus.CANCELED
    # The new offer needs the time to still be in the future, which it is.
    assert offered(second)
    assert AuditEvent.objects.filter(action=AuditAction.WAITLIST_EXPIRED).count() == 1


def test_the_sweep_lapses_wishes_for_days_that_have_passed(lst) -> None:
    entry = lst.wait(lst.student("Pat"))
    assert waitlist.sweep(timezone.now() + timedelta(days=4)) == 1
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.EXPIRED


def test_what_the_booking_leaves_of_a_longer_hold_goes_to_the_next_student(
    lst, django_capture_on_commit_callbacks
) -> None:
    # Cancel an hour; the first student wants 30 minutes, the second wants the other half.
    services.cancel(actor=lst.desk, appointment=lst.appointment, as_staff=True, notify=False)
    long_one = services.book(
        actor=lst.holder,
        assignment=lst.assignment,
        starts_at=at(lst.schedule, lst.tomorrow, 11),
        minutes=60,
        modality=Modality.IN_PERSON,
        students=[lst.holder],
        as_staff=False,
    )
    lst.appointment = long_one
    first = lst.wait(lst.student("First"), earliest=time(11), latest=time(12))
    second = lst.wait(lst.student("Second"), earliest=time(11, 30), latest=time(12))
    lst.cancel()
    assert offered(first) and not offered(second)
    with django_capture_on_commit_callbacks(execute=True):
        waitlist.accept(first)
    assert offered(second)
    assert second.hold.starts_at == at(lst.schedule, lst.tomorrow, 11, 30)


# --- The pages ----------


def test_a_student_asks_for_a_time_on_the_page(client, lst) -> None:
    pat = lst.student("Pat")
    client.force_login(pat)
    url = reverse("scheduling:waiting_list", kwargs={"slug": lst.center.slug})
    assert lst.schedule.name in client.get(url).content.decode()
    response = client.post(
        url,
        {
            "action": "join",
            "schedule": str(lst.schedule.pk),
            "day": lst.tomorrow.isoformat(),
            "earliest": "09:00",
            "latest": "12:00",
            "minutes": "30",
            "modality": Modality.IN_PERSON,
            "tutor": "",
        },
        follow=True,
    )
    assert "You&#x27;re on the waiting list" in response.content.decode()
    assert WaitingEntry.objects.get().student == pat


def test_the_page_offers_the_held_time_and_accepting_books_it(client, lst) -> None:
    pat = lst.student("Pat")
    entry = lst.wait(pat)
    lst.cancel()
    client.force_login(pat)
    url = reverse("scheduling:waiting_list", kwargs={"slug": lst.center.slug})
    page = client.get(url).content.decode()
    assert "Take this time" in page and "held for you until" in page
    response = client.post(url, {"action": "accept", "entry": str(entry.pk)})
    assert response.status_code == 302
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.BOOKED


def test_a_student_cannot_touch_someone_elses_request(client, lst) -> None:
    entry = lst.wait(lst.student("Pat"))
    client.force_login(lst.student("Intruder"))
    url = reverse("scheduling:waiting_list", kwargs={"slug": lst.center.slug})
    client.post(url, {"action": "leave", "entry": str(entry.pk)})
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.WAITING


def test_the_front_desk_sees_and_clears_the_list(client, lst) -> None:
    entry = lst.wait(lst.student("Pat Waiting"))
    client.force_login(lst.desk)
    url = reverse("scheduling:waiting_list_manage", kwargs={"slug": lst.center.slug})
    assert "Pat Waiting" in client.get(url).content.decode()
    client.post(url, {"entry": str(entry.pk)})
    entry.refresh_from_db()
    assert entry.status == WaitingStatus.REMOVED


def test_students_cannot_open_the_front_desks_list(client, lst) -> None:
    client.force_login(lst.student("Pat"))
    url = reverse("scheduling:waiting_list_manage", kwargs={"slug": lst.center.slug})
    assert client.get(url).status_code == 403
