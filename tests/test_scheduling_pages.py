"""The booking and setup pages, driven the way a browser would."""

import html
from datetime import time, timedelta
from urllib.parse import urlencode

import pytest
from django.contrib.messages import get_messages
from django.contrib.staticfiles import finders
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import services, views
from sympraxi.scheduling.forms import format_start
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    AvailabilityException,
    Exemption,
    Schedule,
    Term,
    TutorAssignment,
)
from sympraxi.scheduling.timeutils import local_date
from tests.scheduling_world import World, assign, at, build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def page(name: str, world: World, **kwargs: object) -> str:
    return reverse(name, kwargs={"slug": world.center.slug, **kwargs})


def tomorrow(world: World):
    return local_date(world.appointment.starts_at, world.schedule.tz)


def test_the_booking_page_lists_open_times_without_names(client, world) -> None:
    other = make_member(world.center, Role.STUDENT)
    client.force_login(other)
    url = page("scheduling:open_times", world, schedule_id=world.schedule.pk)
    response = client.get(url, {"date": tomorrow(world).isoformat(), "minutes": 30})
    text = " ".join(response.content.decode().split())
    assert response.status_code == 200
    assert "open times on" in text
    assert world.tutor.display_name in text
    assert world.student.display_name not in text
    assert page("scheduling:book", world, schedule_id=world.schedule.pk) in text
    # 10:00 is taken; the times either side are open.
    assert "9:30 a.m. to 10 a.m." in text
    assert "10 a.m. to 10:30 a.m." not in text
    assert "10:30 a.m. to 11 a.m." in text


def test_htmx_gets_only_the_list_and_the_new_count(client, world) -> None:
    client.force_login(world.student)
    url = page("scheduling:open_times", world, schedule_id=world.schedule.pk)
    response = client.get(
        url, {"date": tomorrow(world).isoformat(), "minutes": 30}, HTTP_HX_REQUEST="true"
    )
    html = response.content.decode()
    assert "<html" not in html
    assert 'hx-swap-oob="innerHTML"' in html
    assert 'id="open-times-status"' in html


def test_bad_filters_show_an_error_instead_of_a_list(client, world) -> None:
    client.force_login(world.student)
    url = page("scheduling:open_times", world, schedule_id=world.schedule.pk)
    response = client.get(url, {"date": "2001-01-01", "minutes": 30})
    assert "Choose a date from" in response.content.decode()


def test_a_student_books_through_the_pages(client, world, django_capture_on_commit_callbacks):
    client.force_login(world.student)
    url = page("scheduling:book", world, schedule_id=world.schedule.pk) + "?" + world.slot_query()
    assert client.get(url).status_code == 200
    with django_capture_on_commit_callbacks(execute=False):
        response = client.post(
            url,
            {
                "assignment": world.assignment.pk,
                "start": format_start(world.free_start),
                "minutes": 30,
                "modality": "online",
            },
        )
    appointment = Appointment.objects.get(starts_at=world.free_start)
    assert response.status_code == 302
    assert response["Location"] == page(
        "scheduling:appointment", world, appointment_id=appointment.pk
    )
    assert appointment.modality == "online"
    assert appointment.meeting_url == world.assignment.meeting_url


def test_a_taken_time_sends_the_student_back_to_the_list(client, world) -> None:
    client.force_login(make_member(world.center, Role.STUDENT))
    query = urlencode(
        {
            "assignment": str(world.assignment.pk),
            "start": format_start(world.appointment.starts_at),
            "minutes": 30,
        }
    )
    url = page("scheduling:book", world, schedule_id=world.schedule.pk) + "?" + query
    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"] == page(
        "scheduling:open_times", world, schedule_id=world.schedule.pk
    )


def test_the_front_desk_books_a_weekly_series(client, world, django_capture_on_commit_callbacks):
    staff = make_member(world.center, Role.FRONT_DESK)
    client.force_login(staff)
    url = page("scheduling:staff_book", world, schedule_id=world.schedule.pk)
    with django_capture_on_commit_callbacks(execute=False):
        response = client.post(
            url,
            {
                "assignment": world.assignment.pk,
                "start": format_start(world.free_start),
                "minutes": 30,
                "modality": "in_person",
                "student": world.student.pk,
                "weeks": 2,
            },
        )
    assert response.status_code == 302
    series = Appointment.objects.filter(
        participants__student=world.student, series_id__isnull=False
    )
    assert series.count() == 2
    assert {a.starts_at - world.free_start for a in series} <= {timedelta(0), timedelta(days=7)}


def test_staff_must_choose_a_student_or_a_placeholder(client, world) -> None:
    staff = make_member(world.center, Role.FRONT_DESK)
    client.force_login(staff)
    url = page("scheduling:staff_book", world, schedule_id=world.schedule.pk)
    response = client.post(
        url,
        {
            "assignment": world.assignment.pk,
            "start": format_start(world.free_start),
            "minutes": 30,
            "modality": "in_person",
            "weeks": 1,
        },
    )
    assert response.status_code == 200
    assert "Choose a student, or make this a placeholder." in response.content.decode()


def test_a_student_cancels_and_staff_confirm(client, world, django_capture_on_commit_callbacks):
    staff = make_member(world.center, Role.FRONT_DESK)
    client.force_login(staff)
    with django_capture_on_commit_callbacks(execute=False):
        client.post(
            page("scheduling:appointment_confirm", world, appointment_id=world.appointment.pk)
        )
    world.appointment.refresh_from_db()
    assert not world.appointment.is_tentative

    client.force_login(world.student)
    with django_capture_on_commit_callbacks(execute=False):
        response = client.post(
            page("scheduling:appointment_cancel", world, appointment_id=world.appointment.pk)
        )
    assert response.status_code == 302
    world.appointment.refresh_from_db()
    assert world.appointment.status == AppointmentStatus.CANCELED


def test_students_in_a_group_do_not_see_each_other(client, world) -> None:
    world.assignment.capacity = 3
    world.assignment.save()
    world.appointment.capacity = 3
    world.appointment.save()
    classmate = make_member(world.center, Role.STUDENT, full_name="Classmate Hidden")
    world.appointment.participants.create(student=classmate)
    client.force_login(world.student)
    html = client.get(
        page("scheduling:appointment", world, appointment_id=world.appointment.pk)
    ).content.decode()
    assert "Classmate Hidden" not in html
    client.force_login(world.tutor)
    html = client.get(
        page("scheduling:appointment", world, appointment_id=world.appointment.pk)
    ).content.decode()
    assert "Classmate Hidden" in html


def test_the_calendar_file_downloads(client, world) -> None:
    client.force_login(world.student)
    response = client.get(
        page("scheduling:appointment_ics", world, appointment_id=world.appointment.pk)
    )
    assert response["Content-Type"].startswith("text/calendar")
    assert b"BEGIN:VEVENT" in response.content
    assert f"UID:{world.appointment.pk}@testserver".encode() in response.content


# --- Setup ----------------------------------------------------------------------


@pytest.fixture
def director(world):
    return make_member(world.center, Role.DIRECTOR)


def test_a_director_sets_up_a_schedule(client, world, director) -> None:
    client.force_login(director)
    response = client.post(
        page("scheduling:term_create", world),
        {"name": "Spring 2027", "starts_on": "2027-01-11", "ends_on": "2027-05-07"},
    )
    assert response.status_code == 302
    term = Term.objects.get(name="Spring 2027")
    response = client.post(
        page("scheduling:schedule_create", world),
        {
            "name": "Spring drop-in",
            "term": term.pk,
            "starts_on": "2027-01-18",
            "ends_on": "2027-05-01",
            "opens_at": "09:00",
            "closes_at": "17:00",
            "slot_minutes": 30,
            "buffer_minutes": 0,
            "visibility": "everyone",
            "min_notice_minutes": 60,
            "horizon_days": 14,
            "change_cutoff_minutes": 60,
            "max_per_week": 2,
        },
    )
    assert response.status_code == 302
    schedule = Schedule.objects.get(name="Spring drop-in")
    tutor = make_member(world.center, Role.TUTOR)
    response = client.post(
        page("scheduling:assignment_create", world, schedule_id=schedule.pk),
        {
            "tutor": tutor.pk,
            "min_minutes": 30,
            "max_minutes": 60,
            "offers_in_person": "on",
            "capacity": 1,
            "is_active": "on",
        },
    )
    assert response.status_code == 302, response.context["form"].errors
    assignment = TutorAssignment.objects.get(schedule=schedule, tutor=tutor)
    response = client.post(
        page("scheduling:hours", world, assignment_id=assignment.pk),
        {
            "weekly_hours-TOTAL_FORMS": 3,
            "weekly_hours-INITIAL_FORMS": 0,
            "weekly_hours-0-weekday": 0,
            "weekly_hours-0-starts_at": "10:00",
            "weekly_hours-0-ends_at": "12:00",
            "weekly_hours-1-weekday": 2,
            "weekly_hours-1-starts_at": "13:00",
            "weekly_hours-1-ends_at": "15:00",
        },
    )
    assert response.status_code == 302
    assert assignment.weekly_hours.count() == 2


def test_schedule_dates_must_fit_the_term(client, world, director) -> None:
    client.force_login(director)
    response = client.post(
        page("scheduling:schedule_create", world),
        {
            "name": "Too long",
            "term": world.term.pk,
            "starts_on": "2001-01-01",
            "ends_on": "2001-02-01",
            "opens_at": "09:00",
            "closes_at": "17:00",
            "slot_minutes": 30,
            "buffer_minutes": 0,
            "visibility": "everyone",
            "min_notice_minutes": 60,
            "horizon_days": 14,
            "change_cutoff_minutes": 60,
        },
    )
    assert response.status_code == 200
    assert "inside the term" in response.content.decode()


def test_a_tutor_must_be_a_tutor_here(client, world, director) -> None:
    client.force_login(director)
    student = make_member(world.center, Role.STUDENT)
    response = client.post(
        page("scheduling:assignment_create", world, schedule_id=world.schedule.pk),
        {
            "tutor": student.pk,
            "min_minutes": 30,
            "max_minutes": 60,
            "offers_in_person": "on",
            "capacity": 1,
        },
    )
    assert response.status_code == 200
    assert not TutorAssignment.objects.filter(tutor=student).exists()


def test_time_off_is_entered_in_the_centers_time_zone(client, world, director) -> None:
    client.force_login(director)
    day = tomorrow(world) + timedelta(days=1)
    response = client.post(
        page("scheduling:exception_create", world, assignment_id=world.assignment.pk),
        {
            "kind": "blackout",
            "starts_at": f"{day}T13:00",
            "ends_at": f"{day}T15:00",
            "note": "Dentist",
        },
    )
    assert response.status_code == 302
    exception = AvailabilityException.objects.get(note="Dentist")
    assert exception.starts_at == at(world.schedule, day, 13)


def test_time_off_cannot_cover_booked_appointments(client, world, director) -> None:
    client.force_login(director)
    day = tomorrow(world)
    response = client.post(
        page("scheduling:exception_create", world, assignment_id=world.assignment.pk),
        {"kind": "blackout", "starts_at": f"{day}T09:00", "ends_at": f"{day}T12:00"},
    )
    assert response.status_code == 200
    assert "Cancel or move them first" in response.content.decode()


def test_exemptions_are_audited(client, world, director) -> None:
    client.force_login(director)
    student = make_member(world.center, Role.STUDENT)
    response = client.post(
        page("scheduling:exemption_create", world),
        {"student": student.pk, "expires_on": str(world.schedule.ends_on), "note": "Accommodation"},
    )
    assert response.status_code == 302
    exemption = Exemption.objects.get(student=student)
    client.post(page("scheduling:exemption_revoke", world, exemption_id=exemption.pk))
    exemption.refresh_from_db()
    assert exemption.revoked_at is not None
    actions = list(
        AuditEvent.objects.filter(target_id=str(exemption.pk)).values_list("action", flat=True)
    )
    assert sorted(actions) == sorted([AuditAction.EXEMPTION_ADDED, AuditAction.EXEMPTION_REVOKED])
    assert all("Accommodation" not in str(e.details) for e in AuditEvent.objects.all())


def test_a_schedule_needs_a_term_first(client, center) -> None:
    director = make_member(center, Role.DIRECTOR)
    client.force_login(director)
    response = client.get(reverse("scheduling:schedule_create", kwargs={"slug": center.slug}))
    assert response.status_code == 302
    assert response["Location"] == reverse("scheduling:term_create", kwargs={"slug": center.slug})


def test_a_link_with_only_a_date_still_lists_open_times(client, world) -> None:
    client.force_login(world.student)
    url = page("scheduling:open_times", world, schedule_id=world.schedule.pk)
    text = " ".join(client.get(url, {"date": tomorrow(world).isoformat()}).content.decode().split())
    assert "10:30 a.m. to 11 a.m." in text


# --- Moving an appointment ------------------------------------------------------


def test_a_student_moves_their_appointment(client, world, django_capture_on_commit_callbacks):
    client.force_login(world.student)
    url = page("scheduling:appointment_change", world, appointment_id=world.appointment.pk)
    text = " ".join(client.get(url, {"date": tomorrow(world).isoformat()}).content.decode().split())
    assert "Move here" in text
    assert "2 p.m. to 2:30 p.m." in text
    with django_capture_on_commit_callbacks(execute=False):
        response = client.post(
            url,
            {
                "assignment": world.assignment.pk,
                "start": format_start(world.free_start),
                "minutes": 30,
            },
        )
    assert response.status_code == 302
    world.appointment.refresh_from_db()
    assert world.appointment.starts_at == world.free_start
    assert world.appointment.sequence == 1


def test_students_cannot_move_group_appointments(client, world) -> None:
    world.appointment.capacity = 3
    world.appointment.save()
    world.appointment.participants.create(student=make_member(world.center, Role.STUDENT))
    client.force_login(world.student)
    url = page("scheduling:appointment_change", world, appointment_id=world.appointment.pk)
    text = " ".join(client.get(url).content.decode().split())
    assert "Group appointments are moved by the center" in text
    assert "Move here" not in text
    detail = page("scheduling:appointment", world, appointment_id=world.appointment.pk)
    assert "Move to another time" not in client.get(detail).content.decode()


def test_students_cannot_move_inside_the_cutoff(client, world) -> None:
    world.schedule.change_cutoff_minutes = 60 * 24 * 7
    world.schedule.save()
    client.force_login(world.student)
    url = page("scheduling:appointment_change", world, appointment_id=world.appointment.pk)
    response = client.post(
        url,
        {"assignment": world.assignment.pk, "start": format_start(world.free_start), "minutes": 30},
    )
    assert "too close to the appointment" in response.content.decode()
    world.appointment.refresh_from_db()
    assert world.appointment.sequence == 0


# --- Tutor caps and repeated posts on the booking pages ---------------------------
# The first-Monday load test (docs/decisions.md) found students offered times
# with tutors who were already full, and double-clicks shown a refusal.

BOOKED = "You're booked. A confirmation email is on its way."


def text_of(response) -> str:
    return html.unescape(" ".join(response.content.decode().split()))


def shown_messages(response) -> list[str]:
    return [str(m) for m in get_messages(response.wsgi_request)]


def slot(assignment, start) -> str:
    return urlencode(
        {"assignment": str(assignment.pk), "start": format_start(start), "minutes": 30}
    )


def book_url(world: World, assignment=None, start=None) -> str:
    url = page("scheduling:book", world, schedule_id=world.schedule.pk)
    return f"{url}?{slot(assignment or world.assignment, start or world.free_start)}"


def booking(assignment, start) -> dict[str, object]:
    return {
        "assignment": assignment.pk,
        "start": format_start(start),
        "minutes": 30,
        "modality": "in_person",
    }


@pytest.fixture
def full_tomorrow(world: World) -> World:
    """The tutor's one appointment tomorrow fills their daily cap."""
    world.assignment.max_per_day = 1
    world.assignment.save()
    return world


def test_students_do_not_see_times_with_a_tutor_at_their_cap(client, full_tomorrow) -> None:
    world = full_tomorrow
    client.force_login(make_member(world.center, Role.STUDENT))
    url = page("scheduling:open_times", world, schedule_id=world.schedule.pk)
    listed = text_of(client.get(url, {"date": tomorrow(world).isoformat(), "minutes": 30}))
    assert "No open times that day." in listed
    later = (tomorrow(world) + timedelta(days=1)).isoformat()
    assert "2 p.m. to 2:30 p.m." in text_of(client.get(url, {"date": later, "minutes": 30}))
    # A link to one of those times, from before the tutor filled up, goes back to the list.
    response = client.get(book_url(world))
    assert response["Location"] == url
    assert shown_messages(response) == ["That time isn't open anymore. Choose another."]


def test_staff_see_times_with_a_tutor_at_their_cap_marked(client, full_tomorrow) -> None:
    world = full_tomorrow
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    url = page("scheduling:open_times", world, schedule_id=world.schedule.pk)
    listed = text_of(client.get(url, {"date": tomorrow(world).isoformat(), "minutes": 30}))
    assert "2 p.m. to 2:30 p.m." in listed
    assert "Tutor's daily limit reached" in listed

    form = page("scheduling:staff_book", world, schedule_id=world.schedule.pk)
    form += "?" + slot(world.assignment, world.free_start)
    shown = text_of(client.get(form))
    assert "This tutor has reached their limit of 1 appointment a day." in shown
    assert (
        "To book anyway, check “Book even if it goes over the student's or tutor's limits”."
        in shown
    )
    other = make_member(world.center, Role.STUDENT)
    data = {**booking(world.assignment, world.free_start), "student": other.pk, "weeks": 1}
    refused = text_of(client.post(form, data))
    assert "This tutor is fully booked that day." in refused
    assert ": This tutor" not in refused  # no date in front of a single booking
    assert client.post(form, {**data, "override_limits": "on"}).status_code == 302


def test_students_can_still_move_inside_a_day_their_tutor_has_filled(client, full_tomorrow):
    # Moving doesn't count the appointment being moved, as in services.reschedule().
    world = full_tomorrow
    client.force_login(world.student)
    url = page("scheduling:appointment_change", world, appointment_id=world.appointment.pk)
    assert "2 p.m. to 2:30 p.m." in text_of(client.get(url, {"date": tomorrow(world).isoformat()}))
    response = client.post(
        url,
        {"assignment": world.assignment.pk, "start": format_start(world.free_start), "minutes": 30},
    )
    assert response.status_code == 302
    world.appointment.refresh_from_db()
    assert world.appointment.starts_at == world.free_start


@pytest.mark.parametrize(
    ("tentative", "message"),
    [(False, BOOKED), (True, "Your request is in. The center will confirm it by email.")],
)
def test_posting_the_booking_form_again_shows_the_booking(
    client, world, django_capture_on_commit_callbacks, tentative, message
) -> None:
    world.assignment.needs_confirmation = tentative
    world.assignment.save()
    student = make_member(world.center, Role.STUDENT)
    client.force_login(student)
    with django_capture_on_commit_callbacks(execute=False) as emails:
        first = client.post(book_url(world), booking(world.assignment, world.free_start))
        client.get(first["Location"])  # shows, and so uses up, the first message
        second = client.post(book_url(world), booking(world.assignment, world.free_start))
    appointment = Appointment.objects.get(participants__student=student)
    detail = page("scheduling:appointment", world, appointment_id=appointment.pk)
    assert first["Location"] == second["Location"] == detail
    assert shown_messages(second) == [message]
    assert len(emails) == 1


def test_a_refusal_caused_by_the_students_own_booking_shows_the_booking(
    client, world, monkeypatch
) -> None:
    # In a double-click, the other request can commit after this one looked for
    # it and before this one books. A group seat stays listed after the student
    # takes it, so the booking itself is what refuses here.
    group = assign(world.schedule, hours=((time(8), time(22)),), capacity=3)
    student = make_member(world.center, Role.STUDENT)
    appointment = services.book(
        actor=student,
        assignment=group,
        starts_at=world.free_start,
        minutes=30,
        modality="in_person",
        students=[student],
        as_staff=False,
        notify=False,
    )
    looks: list[object] = []
    real = views.held_time

    def first_look_misses(*args):
        looks.append(args)
        return None if len(looks) == 1 else real(*args)

    monkeypatch.setattr(views, "held_time", first_look_misses)
    client.force_login(student)
    response = client.post(book_url(world, group), booking(group, world.free_start))
    assert len(looks) == 2
    detail = page("scheduling:appointment", world, appointment_id=appointment.pk)
    assert response["Location"] == detail
    assert shown_messages(response) == [BOOKED]


def test_opening_the_booking_page_for_a_time_you_hold_shows_the_appointment(client, world):
    client.force_login(world.student)
    response = client.get(book_url(world, start=world.appointment.starts_at))
    assert response["Location"] == page(
        "scheduling:appointment", world, appointment_id=world.appointment.pk
    )
    assert shown_messages(response) == ["You already have this appointment."]


def test_another_appointment_at_that_time_is_still_refused(client, world) -> None:
    other = assign(world.schedule, hours=((time(8), time(22)),))
    client.force_login(world.student)
    start = world.appointment.starts_at
    response = client.post(book_url(world, other, start), booking(other, start))
    assert response.status_code == 200
    assert f"{world.student} already has an appointment then." in text_of(response)


def test_the_booking_forms_ignore_a_second_submit(client, world) -> None:
    """`sympraxi.js` lets a form marked data-submit-once be sent once per page."""
    assert finders.find("sympraxi/sympraxi.js")
    client.force_login(make_member(world.center, Role.STUDENT))
    student_page = client.get(book_url(world)).content.decode()
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    staff_url = page("scheduling:staff_book", world, schedule_id=world.schedule.pk)
    staff_page = client.get(staff_url + "?" + world.slot_query()).content.decode()
    for shown in (student_page, staff_page):
        assert "data-submit-once" in shown
        assert "/static/sympraxi/sympraxi.js" in shown
