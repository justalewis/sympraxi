"""The attendance, waiver, block and reminder pages, driven the way a browser would."""

import json

import pytest
from django.urls import reverse

from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling.forms import format_start
from sympraxi.scheduling.models import Attendance, Block, Participant
from tests.attendance_world import add_attendance, participant
from tests.records_world import add_records
from tests.scheduling_world import World, build_world, make_member, make_schedule

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center) -> World:
    return add_attendance(add_records(build_world(center)))


def page(name: str, world: World, **kwargs: object) -> str:
    return reverse(name, kwargs={"slug": world.center.slug, **kwargs})


def text(response) -> str:
    return " ".join(response.content.decode().split())


# --- Marking attendance ---------------------------------------------------------------


def test_the_tutor_marks_who_came(client, world) -> None:
    client.force_login(world.tutor)
    url = page("scheduling:attendance", world, appointment_id=world.unwritten.pk)
    form = text(client.get(url))
    assert "Who came?" in form
    assert "Flagged as a possible no-show" in form
    field = f"attendance-{world.student.pk}"
    response = client.post(url, {field: Attendance.ATTENDED})
    assert response.status_code == 302
    assert response["Location"] == page(
        "scheduling:appointment", world, appointment_id=world.unwritten.pk
    )
    p = participant(world.unwritten, world.student)
    assert p.attendance == Attendance.ATTENDED and not p.is_possible_no_show


def test_attendance_waits_for_the_appointment_to_start(client, world) -> None:
    client.force_login(world.tutor)
    url = page("scheduling:attendance", world, appointment_id=world.appointment.pk)
    assert "once the appointment starts" in text(client.get(url))
    assert "Save attendance" not in text(client.get(url))


def test_front_desk_answer_possible_no_shows_from_the_list(client, world) -> None:
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    listing = page("scheduling:attendance_list", world)
    shown = text(client.get(listing))
    assert world.student.display_name in shown
    assert "Bea Blocked" in shown  # last month's no-shows, with a waive link
    response = client.post(
        page("scheduling:attendance", world, appointment_id=world.unwritten.pk),
        {f"attendance-{world.student.pk}": Attendance.NO_SHOW, "next": listing},
    )
    assert response["Location"] == listing
    assert participant(world.unwritten, world.student).is_no_show
    assert "Nothing to check right now." in text(client.get(listing))


def test_next_cannot_leave_the_center(client, world) -> None:
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    url = page("scheduling:attendance", world, appointment_id=world.unwritten.pk)
    appointment_page = page("scheduling:appointment", world, appointment_id=world.unwritten.pk)
    for target in ("https://evil.example/", "/centers/another-center/attendance/"):
        response = client.post(
            url, {f"attendance-{world.student.pk}": Attendance.ATTENDED, "next": target}
        )
        assert response["Location"] == appointment_page


def test_tutors_see_only_their_own_possible_no_shows(client, world) -> None:
    other_tutor = make_member(world.center, Role.TUTOR)
    client.force_login(other_tutor)
    assert "Nothing to check right now." in text(
        client.get(page("scheduling:attendance_list", world))
    )
    client.force_login(world.tutor)
    shown = text(client.get(page("scheduling:attendance_list", world)))
    assert world.student.display_name in shown
    assert "last 30 days" not in shown  # tutors don't waive


def test_the_appointment_page_shows_attendance(client, world) -> None:
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    flagged = text(
        client.get(page("scheduling:appointment", world, appointment_id=world.unwritten.pk))
    )
    assert "possible no-show" in flagged
    assert "Mark who came" in flagged
    missed = text(client.get(page("scheduling:appointment", world, appointment_id=world.missed.pk)))
    assert "no-show" in missed
    assert page("scheduling:waive", world, appointment_id=world.missed.pk) in missed


def test_students_see_their_own_missed_appointment_kindly(client, world) -> None:
    client.force_login(world.blocked_student)
    shown = text(client.get(page("scheduling:appointment", world, appointment_id=world.missed.pk)))
    assert "This appointment is recorded as missed." in shown
    assert "Mark who came" not in shown
    mine = text(client.get(page("scheduling:my_appointments", world)))
    assert "missed" in mine


def test_the_day_sheet_has_an_attendance_column(client, world) -> None:
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    day = world.unwritten.starts_at.astimezone(world.schedule.tz).date()
    shown = text(
        client.get(
            page("scheduling:day_sheet", world, schedule_id=world.schedule.pk),
            {"date": day.isoformat()},
        )
    )
    assert f"{world.student.display_name}: possible no-show" in shown
    assert "—" not in shown


# --- Waivers ------------------------------------------------------------------------------


def test_front_desk_waive_a_no_show(client, world) -> None:
    desk = make_member(world.center, Role.FRONT_DESK)
    client.force_login(desk)
    url = page("scheduling:waive", world, appointment_id=world.missed.pk)
    shown = text(client.get(url, {"student": world.blocked_student.pk}))
    assert "Waive Bea Blocked's no-show?" in shown
    response = client.post(url, {"student": world.blocked_student.pk, "reason": ""})
    assert response.status_code == 200
    assert participant(world.missed, world.blocked_student).waived_at is None
    response = client.post(
        url, {"student": world.blocked_student.pk, "reason": "Car broke down; called ahead."}
    )
    assert response.status_code == 302
    p = participant(world.missed, world.blocked_student)
    assert p.waived_by == desk
    assert "This was waived on" in text(client.get(url, {"student": world.blocked_student.pk}))


def test_nothing_to_waive(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    url = page("scheduling:waive", world, appointment_id=world.past_appointment.pk)
    shown = text(client.get(url, {"student": world.student.pk}))
    assert "There's nothing to waive" in shown
    assert "not-a-uuid" not in text(client.get(url, {"student": "not-a-uuid"}))


# --- Blocks --------------------------------------------------------------------------------


def test_a_blocked_student_sees_why_and_gets_no_booking_links(client, world) -> None:
    client.force_login(world.blocked_student)
    open_times = text(
        client.get(page("scheduling:open_times", world, schedule_id=world.schedule.pk))
    )
    assert "You can't book online right now" in open_times
    assert "1 of your appointments this term was recorded as missed" in open_times
    assert "You can book online again starting" in open_times
    assert page("scheduling:book", world, schedule_id=world.schedule.pk) not in open_times
    book = page("scheduling:book", world, schedule_id=world.schedule.pk)
    response = client.get(f"{book}?{world.slot_query()}")
    assert response.status_code == 200
    shown = text(response)
    assert "You can't book online right now" in shown and "Book it" not in shown
    # A form posted anyway is refused by the booking rules.
    slot = {
        "assignment": world.assignment.pk,
        "start": format_start(world.free_start),
        "minutes": 30,
        "modality": "in_person",
    }
    response = client.post(book, slot)
    assert response.status_code == 200
    assert "You can't book online right now" in text(response)
    assert Participant.objects.filter(student=world.blocked_student).count() == 1
    assert "You can't book online right now" in text(
        client.get(page("scheduling:my_appointments", world))
    )


def test_a_block_on_one_schedule_hides_only_that_schedule(client, world) -> None:
    other = make_schedule(
        world.center, world.schedule.starts_on, world.schedule.ends_on, name="Evening"
    )
    world.block.schedule = world.schedule
    world.block.save()
    client.force_login(world.blocked_student)
    shown = text(client.get(page("scheduling:my_appointments", world)))
    assert "This applies to Main schedule only." in shown
    assert page("scheduling:open_times", world, schedule_id=other.pk) in shown
    assert page("scheduling:open_times", world, schedule_id=world.schedule.pk) not in shown


def test_other_students_book_as_usual(client, world) -> None:
    client.force_login(world.student)
    shown = text(client.get(page("scheduling:open_times", world, schedule_id=world.schedule.pk)))
    assert "You can't book online right now" not in shown


def test_directors_see_and_lift_blocks(client, world) -> None:
    director = make_member(world.center, Role.DIRECTOR)
    client.force_login(director)
    shown = text(client.get(page("scheduling:blocks", world)))
    assert "Bea Blocked" in shown and "1 no-show in" in shown
    lift = page("scheduling:block_lift", world, block_id=world.block.pk)
    detail = text(client.get(lift))
    assert "What counts now" in detail
    assert page("scheduling:appointment", world, appointment_id=world.missed.pk) in detail
    response = client.post(lift, {"note": "Met with the student."})
    assert response.status_code == 302
    world.block.refresh_from_db()
    assert world.block.lifted_by == director
    assert world.block.lift_note == "Met with the student."
    assert "Lifted" in text(client.get(page("scheduling:blocks", world)))


def test_directors_set_the_center_limits(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    response = client.post(
        page("scheduling:block_settings", world),
        {"no_show_block_limit": "3", "late_cancel_block_limit": "", "block_count_from": ""},
    )
    assert response.status_code == 302
    world.center.refresh_from_db()
    assert world.center.no_show_block_limit == 3
    assert world.center.late_cancel_block_limit is None
    event = AuditEvent.objects.get(action=AuditAction.ATTENDANCE_SETTINGS_CHANGED)
    assert event.details["changed"] == ["no_show_block_limit"]


def test_schedules_set_reminders_and_attendance_rules(client, world) -> None:
    client.force_login(make_member(world.center, Role.DIRECTOR))
    url = page("scheduling:schedule_edit", world, schedule_id=world.schedule.pk)
    form = text(client.get(url))
    assert "24 hours before" in form and "No reminder" in form
    schedule = world.schedule
    data = {
        "name": schedule.name,
        "term": schedule.term_id,
        "starts_on": schedule.starts_on.isoformat(),
        "ends_on": schedule.ends_on.isoformat(),
        "opens_at": "08:00",
        "closes_at": "22:00",
        "slot_minutes": 30,
        "buffer_minutes": 0,
        "visibility": "everyone",
        "min_notice_minutes": 0,
        "horizon_days": 30,
        "change_cutoff_minutes": 0,
        "reminder_1_minutes": "1440",
        "reminder_2_minutes": "1440",
    }
    response = client.post(url, data)
    assert response.status_code == 200
    assert "Choose a different time for each reminder." in text(response)
    data.update({"reminder_2_minutes": "60", "reminder_3_minutes": "", "no_show_block_limit": "2"})
    assert client.post(url, data).status_code == 302
    schedule.refresh_from_db()
    assert schedule.reminder_offsets() == {1440, 60}
    assert (schedule.late_cancel_minutes, schedule.no_show_grace_minutes) == (0, 15)
    assert schedule.no_show_block_limit == 2


# --- Reminders --------------------------------------------------------------------------


def test_students_turn_reminders_off(client, world) -> None:
    client.force_login(world.student)
    url = reverse("accounts:reminders")
    shown = text(client.get(url))
    assert "Email me before appointments I booked" in shown
    assert "tutoring" not in shown
    assert client.post(url, {}).status_code == 302
    world.student.refresh_from_db()
    assert not world.student.reminders_for_appointments


def test_tutors_turn_tutoring_reminders_on(client, world) -> None:
    client.force_login(world.tutor)
    url = reverse("accounts:reminders")
    assert "Email me before appointments I&#x27;m tutoring" in text(client.get(url))
    client.post(url, {"reminders_for_appointments": "on", "reminders_for_tutoring": "on"})
    world.tutor.refresh_from_db()
    assert world.tutor.reminders_for_tutoring and world.tutor.reminders_for_appointments


# --- The FERPA export -------------------------------------------------------------------


def test_the_export_includes_attendance_waivers_and_blocks(client, world) -> None:
    p = participant(world.missed, world.blocked_student)
    p.waived_at = p.attendance_marked_at
    p.waiver_reason = "Weather."
    p.save()
    membership = Membership.objects.get(user=world.blocked_student, role=Role.STUDENT)
    client.force_login(make_member(world.center, Role.DIRECTOR))
    data = json.loads(
        client.get(page("records:export_json", world, membership_id=membership.pk)).content
    )
    [appointment] = data["appointments"]
    assert appointment["attendance"] == "no_show"
    assert appointment["waiver_reason"] == "Weather."
    assert appointment["late_cancellation"] is False
    [block] = data["booking_blocks"]
    assert (block["reason"], block["count"], block["limit"]) == ("no_shows", 1, 1)
    shown = text(client.get(page("records:export", world, membership_id=membership.pk)))
    assert "Booking blocks" in shown and "Weather." in shown
    assert Block.objects.count() == 1
