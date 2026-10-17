"""Intake and registration questions on the booking pages, the appointment page, the
pre-session brief and the FERPA export."""

import html
import json
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.forms import builder
from sympraxi.forms.builder import QuestionData
from sympraxi.forms.models import FormKind, Response
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling import services
from sympraxi.scheduling.forms import format_start
from sympraxi.scheduling.models import Appointment, Participant
from tests.attendance_world import block
from tests.forms_world import (
    INTAKE,
    INTAKE_ANSWERS,
    REGISTRATION,
    REGISTRATION_ANSWERS,
    answer,
    director_of,
    form_data,
    make_form,
    question,
)
from tests.records_world import appointment_for, membership
from tests.scheduling_world import World, build_world, make_member

pytestmark = pytest.mark.django_db

FIRST_PAGE = [q["label"] for q in INTAKE if "condition" not in q]


@pytest.fixture
def world(center) -> World:
    return build_world(center)


def text_of(response) -> str:
    return html.unescape(" ".join(response.content.decode().split()))


def book_url(world: World, name: str = "scheduling:book") -> str:
    url = reverse(name, kwargs={"slug": world.center.slug, "schedule_id": world.schedule.pk})
    return f"{url}?{world.slot_query()}"


def slot(world: World, **extra: object) -> dict[str, object]:
    return {
        "assignment": str(world.assignment.pk),
        "start": format_start(world.free_start),
        "minutes": "30",
        "modality": "in_person",
        **extra,
    }


def booked(world: World, student) -> Appointment | None:
    return Appointment.objects.filter(
        starts_at=world.free_start, participants__student=student
    ).first()


def appointment_url(world: World, appointment: Appointment) -> str:
    return reverse(
        "scheduling:appointment",
        kwargs={"slug": world.center.slug, "appointment_id": appointment.pk},
    )


# --- Students booking for themselves --------------------------------------------------------------


def test_booking_without_forms_asks_nothing(client, world) -> None:
    client.force_login(world.student)
    page = text_of(client.get(book_url(world)))
    assert "About this appointment" not in page and "About you" not in page
    response = client.post(book_url(world), slot(world))
    assert response.status_code == 302
    assert booked(world, world.student) is not None
    assert not Response.objects.exists()


def test_the_booking_page_asks_the_intake_questions(client, world) -> None:
    make_form(world.center)
    client.force_login(world.student)
    page = text_of(client.get(book_url(world)))
    assert "About this appointment" in page
    for label in FIRST_PAGE:
        assert label in page
    # The follow-up waits for its answer.
    assert "What kind of writing, then?" not in page
    assert "Continue" in page


def test_booking_saves_the_answers_with_the_appointment(client, world) -> None:
    form = make_form(world.center)
    client.force_login(world.student)
    data = slot(world, **form_data(form.published(), INTAKE_ANSWERS))
    response = client.post(book_url(world), data)
    appointment = booked(world, world.student)
    assert response.status_code == 302 and appointment is not None
    saved = Response.objects.get()
    assert (saved.appointment, saved.student, saved.answered_by) == (
        appointment,
        world.student,
        world.student,
    )
    assert saved.version.number == 1
    assert saved.answers.get(question__label="What course is this for?").text == "ENG 101"


def test_missing_required_answers_stop_the_booking(client, world) -> None:
    form = make_form(world.center)
    client.force_login(world.student)
    data = slot(world, **form_data(form.published(), {"When is it due?": "2026-12-01"}))
    response = client.post(book_url(world), data)
    assert response.status_code == 200
    assert "This field is required." in text_of(response)
    assert booked(world, world.student) is None
    assert not Response.objects.exists()


def test_follow_ups_appear_after_a_continue_step_without_javascript(client, world) -> None:
    version = make_form(world.center).published()
    client.force_login(world.student)
    answers = {**INTAKE_ANSWERS, "What kind of writing is it?": "Something else"}
    # The page showed only the first questions; this answer calls for a follow-up.
    data = slot(world, **form_data(version, answers, shown=FIRST_PAGE))
    response = client.post(book_url(world), data)
    page = text_of(response)
    assert response.status_code == 200
    assert "What kind of writing, then?" in page
    assert "Follow-up questions appeared below." in page
    assert "This field is required." not in page  # nothing was submitted yet
    assert booked(world, world.student) is None
    # Answered and posted again, it books.
    answers["What kind of writing, then?"] = "A zine"
    response = client.post(book_url(world), slot(world, **form_data(version, answers)))
    assert response.status_code == 302
    saved = Response.objects.get()
    assert saved.answers.get(question__label="What kind of writing, then?").text == "A zine"


def test_the_continue_button_saves_nothing(client, world) -> None:
    version = make_form(world.center).published()
    client.force_login(world.student)
    data = slot(world, **form_data(version, INTAKE_ANSWERS), **{"continue": "1"})
    response = client.post(book_url(world), data)
    assert response.status_code == 200
    assert booked(world, world.student) is None
    assert not Response.objects.exists()
    # What was typed is still there.
    assert 'value="ENG 101"' in response.content.decode()


def test_htmx_shows_follow_ups_in_place(client, world) -> None:
    version = make_form(world.center).published()
    client.force_login(world.student)
    answers = {"What kind of writing is it?": "Something else"}
    data = slot(world, **form_data(version, answers, shown=FIRST_PAGE), **{"continue": "1"})
    response = client.post(book_url(world), data, headers={"HX-Request": "true"})
    page = response.content.decode()
    assert response.status_code == 200
    assert 'id="answers-intake"' in page and "What kind of writing, then?" in page
    assert "1 follow-up question appeared." in page
    # The question with follow-ups asks htmx to redraw the block when it changes.
    source = question(version, "What kind of writing is it?")
    assert f'id="question-{source.key.hex}" hx-post=' in page
    assert booked(world, world.student) is None


def test_registration_is_asked_the_first_time_only(client, world) -> None:
    registration = make_form(world.center, FormKind.REGISTRATION, questions=REGISTRATION)
    intake = make_form(world.center)
    newcomer = make_member(world.center, Role.STUDENT)
    client.force_login(newcomer)
    page = text_of(client.get(book_url(world)))
    assert "About you" in page and "What is your major?" in page
    # Demographic and other questions not marked for staff say so.
    assert "Only the center's directors see your answer." in page
    data = slot(
        world,
        **form_data(registration.published(), REGISTRATION_ANSWERS, prefix="registration"),
        **form_data(intake.published(), INTAKE_ANSWERS),
    )
    assert client.post(book_url(world), data).status_code == 302
    mine = Response.objects.get(form=registration)
    assert mine.student == newcomer and mine.appointment is None
    assert Response.objects.filter(form=intake, student=newcomer).count() == 1
    # Booking again doesn't ask again.
    world.free_start += timedelta(hours=1)
    assert "About you" not in text_of(client.get(book_url(world)))


def test_a_refused_booking_saves_no_answers(client, world) -> None:
    form = make_form(world.center)
    block(world.student, world)
    client.force_login(world.student)
    data = slot(world, **form_data(form.published(), INTAKE_ANSWERS))
    response = client.post(book_url(world), data)
    assert response.status_code == 200
    assert "Online booking is paused" in text_of(response)
    assert booked(world, world.student) is None
    assert not Response.objects.exists()


# --- Staff booking for a student ------------------------------------------------------------------


@pytest.fixture
def desk(world):
    return make_member(world.center, Role.FRONT_DESK)


def test_staff_can_skip_the_intake(client, world, desk) -> None:
    form = make_form(world.center)
    client.force_login(desk)
    page = text_of(client.get(book_url(world, "scheduling:staff_book")))
    assert "leave them all blank to skip them" in page
    data = slot(world, student=str(world.student.pk), weeks="1", **form_data(form.published(), {}))
    response = client.post(book_url(world, "scheduling:staff_book"), data)
    assert response.status_code == 302
    assert booked(world, world.student) is not None
    assert not Response.objects.exists()


def test_staff_who_start_the_intake_answer_the_required_questions(client, world, desk) -> None:
    form = make_form(world.center)
    client.force_login(desk)
    data = slot(
        world,
        student=str(world.student.pk),
        weeks="1",
        **form_data(form.published(), {"When is it due?": "2026-12-01"}),
    )
    response = client.post(book_url(world, "scheduling:staff_book"), data)
    assert response.status_code == 200
    assert "This field is required." in text_of(response)
    assert booked(world, world.student) is None


def test_staff_answers_go_with_every_week_booked(client, world, desk) -> None:
    form = make_form(world.center)
    client.force_login(desk)
    data = slot(
        world,
        student=str(world.student.pk),
        weeks="2",
        **form_data(form.published(), INTAKE_ANSWERS),
    )
    response = client.post(book_url(world, "scheduling:staff_book"), data)
    assert response.status_code == 302
    saved = Response.objects.order_by("appointment__starts_at")
    assert saved.count() == 2
    assert {r.answered_by for r in saved} == {desk}
    assert {r.student for r in saved} == {world.student}
    assert saved[1].appointment.starts_at - saved[0].appointment.starts_at >= timedelta(days=6)
    assert all(r.by_staff for r in saved)


# --- After booking --------------------------------------------------------------------------------


def test_answers_stay_with_a_moved_appointment(client, world, desk) -> None:
    form = make_form(world.center)
    saved = answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    services.reschedule(
        actor=desk,
        appointment=world.appointment,
        assignment=world.assignment,
        starts_at=world.free_start,
        minutes=30,
        as_staff=True,
        notify=False,
    )
    world.appointment.refresh_from_db()
    assert world.appointment.starts_at == world.free_start
    client.force_login(world.tutor)
    page = text_of(client.get(appointment_url(world, world.appointment)))
    assert "ENG 101" in page
    assert Response.objects.get(appointment=world.appointment) == saved


def test_students_see_their_own_answers_and_can_change_them(client, world) -> None:
    form = make_form(world.center)
    answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    client.force_login(world.student)
    page = text_of(client.get(appointment_url(world, world.appointment)))
    assert "Your answers for this appointment" in page
    assert "ENG 101" in page and "Ideas, Citations" in page
    assert "Change your answers" in page
    # Reading your own answers isn't a disclosure, so it isn't audited.
    assert not AuditEvent.objects.filter(action=AuditAction.ANSWERS_VIEWED).exists()


def test_a_student_can_answer_later_if_staff_skipped(client, world) -> None:
    make_form(world.center)
    client.force_login(world.student)
    page = text_of(client.get(appointment_url(world, world.appointment)))
    assert "You haven't answered the questions for this appointment yet." in page
    assert "Answer the questions" in page


def test_a_group_member_sees_only_their_own_answers(client, world) -> None:
    form = make_form(world.center)
    mate = make_member(world.center, Role.STUDENT)
    group = appointment_for(world, [world.student, mate], days=2, capacity=4)
    answer(form, world.student, INTAKE_ANSWERS, appointment=group)
    answer(form, mate, {**INTAKE_ANSWERS, "What course is this for?": "HIS 330"}, appointment=group)
    client.force_login(mate)
    page = text_of(client.get(appointment_url(world, group)))
    assert "HIS 330" in page and "ENG 101" not in page


def test_the_tutor_sees_the_answers_in_the_brief(client, world) -> None:
    registration = make_form(world.center, FormKind.REGISTRATION, questions=REGISTRATION)
    reg = answer(registration, world.student, REGISTRATION_ANSWERS)
    form = make_form(world.center)
    intake = answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    client.force_login(world.tutor)
    page = text_of(client.get(appointment_url(world, world.appointment)))
    brief = page[page.index("Before the session") :]
    assert "Intake answers" in brief and "ENG 101" in brief
    assert "Registration answers" in brief and "Biology" in brief
    # Only the question marked for staff.
    assert "Is English your first language?" not in page
    assert "Are you the first in your family" not in page
    event = AuditEvent.objects.get(action=AuditAction.ANSWERS_VIEWED)
    assert event.actor == world.tutor
    assert set(event.details["responses"]) == {str(intake.pk), str(reg.pk)}
    assert event.details["students"] == [str(world.student.pk)]
    assert "ENG 101" not in json.dumps(event.details)


def test_front_desk_sees_the_answers_without_a_brief(client, world, desk) -> None:
    registration = make_form(world.center, FormKind.REGISTRATION, questions=REGISTRATION)
    answer(registration, world.student, REGISTRATION_ANSWERS)
    answer(make_form(world.center), world.student, INTAKE_ANSWERS, appointment=world.appointment)
    client.force_login(desk)
    page = text_of(client.get(appointment_url(world, world.appointment)))
    assert "Before the session" not in page
    assert "Answers from booking" in page and "ENG 101" in page and "Biology" in page
    assert "Is English your first language?" not in page
    assert AuditEvent.objects.filter(action=AuditAction.ANSWERS_VIEWED, actor=desk).exists()


def test_directors_see_every_registration_answer(client, world) -> None:
    registration = make_form(world.center, FormKind.REGISTRATION, questions=REGISTRATION)
    answer(registration, world.student, REGISTRATION_ANSWERS)
    client.force_login(director_of(world.center))
    page = text_of(client.get(appointment_url(world, world.appointment)))
    assert "Biology" in page
    assert "Is English your first language?" in page
    assert "Are you the first in your family to go to college?" in page


# --- A student's registration answers, for staff --------------------------------------------------


def registration_url(world: World) -> str:
    return reverse(
        "forms:student_registration",
        kwargs={
            "slug": world.center.slug,
            "membership_id": membership(world.center, world.student).pk,
        },
    )


@pytest.fixture
def answered(world):
    registration = make_form(world.center, FormKind.REGISTRATION, questions=REGISTRATION)
    first = answer(registration, world.student, REGISTRATION_ANSWERS)
    later = answer(
        registration, world.student, {**REGISTRATION_ANSWERS, "What is your major?": "Chemistry"}
    )
    return registration, first, later


def test_the_registration_page_by_role(client, world, desk, answered) -> None:
    _form, first, later = answered
    client.force_login(director_of(world.center))
    page = text_of(client.get(registration_url(world)))
    assert "Chemistry" in page and "Is English your first language?" in page
    # Directors also see earlier answers.
    assert "Earlier answers" in page and "Biology" in page
    event = AuditEvent.objects.filter(action=AuditAction.ANSWERS_VIEWED).latest("occurred_at")
    assert event.details["responses"] == [str(later.pk), str(first.pk)]

    for staff in (world.tutor, desk):
        client.force_login(staff)
        page = text_of(client.get(registration_url(world)))
        assert "Chemistry" in page
        assert "Is English your first language?" not in page
        assert "Earlier answers" not in page and "Biology" not in page

    # A tutor who hasn't worked with the student can't find the page; a student can't open it.
    client.force_login(make_member(world.center, Role.TUTOR))
    assert client.get(registration_url(world)).status_code == 404
    client.force_login(world.student)
    assert client.get(registration_url(world)).status_code == 403


def test_staff_visibility_follows_the_current_version(client, world, answered) -> None:
    registration, _first, _later = answered
    director = director_of(world.center)
    draft = builder.start_draft(actor=director, form=registration)
    major = question(draft, "What is your major?")
    builder.save_question(
        draft=draft,
        question=major,
        data=QuestionData(kind=major.kind, label=major.label, shown_to_staff=False),
    )
    builder.publish(actor=director, draft=draft)
    client.force_login(world.tutor)
    page = text_of(client.get(registration_url(world)))
    assert "Chemistry" not in page and "No answers to show." in page
    client.force_login(director)
    assert "Chemistry" in text_of(client.get(registration_url(world)))


# --- The FERPA export -----------------------------------------------------------------------------


def test_the_export_holds_every_answer(client, world, answered) -> None:
    form = make_form(world.center)
    answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    answer(
        form,
        world.student,
        {**INTAKE_ANSWERS, "What course is this for?": "ENG 102"},
        appointment=world.appointment,
    )
    client.force_login(director_of(world.center))
    kwargs = {
        "slug": world.center.slug,
        "membership_id": membership(world.center, world.student).pk,
    }
    data = json.loads(client.get(reverse("records:export_json", kwargs=kwargs)).content)
    assert [r["version"] for r in data["registration_answers"]] == [1, 1]
    first = data["registration_answers"][0]["answers"]
    assert first[0] == {
        "question": "What is your major?",
        "question_key": first[0]["question_key"],
        "answer": "Biology",
        "value": "Biology",
    }
    english = next(a for a in first if a["question"] == "Is English your first language?")
    assert english["answer"] == "No" and len(english["value"]) == 1
    (appointment,) = [a for a in data["appointments"] if a["id"] == str(world.appointment.pk)]
    courses = [
        next(a["answer"] for a in r["answers"] if a["question"] == "What course is this for?")
        for r in appointment["intake_answers"]
    ]
    assert courses == ["ENG 101", "ENG 102"]
    page = text_of(client.get(reverse("records:export", kwargs=kwargs)))
    assert "Registration answers" in page and "Chemistry" in page
    assert "Intake answers" in page and "ENG 102" in page


# --- The student's own pages ----------------------------------------------------------------------


def intake_url(world: World, appointment: Appointment) -> str:
    return reverse(
        "forms:intake", kwargs={"slug": world.center.slug, "appointment_id": appointment.pk}
    )


def test_a_student_changes_their_intake_answers_before_the_appointment(client, world) -> None:
    form = make_form(world.center)
    first = answer(form, world.student, INTAKE_ANSWERS, appointment=world.appointment)
    client.force_login(world.student)
    page = client.get(intake_url(world, world.appointment)).content.decode()
    assert 'value="ENG 101"' in page  # starts from the earlier answers
    changed = {**INTAKE_ANSWERS, "What course is this for?": "ENG 102"}
    response = client.post(
        intake_url(world, world.appointment), form_data(form.published(), changed)
    )
    assert response.status_code == 302
    responses = Response.objects.filter(appointment=world.appointment).order_by("created_at")
    assert responses.first() == first and responses.count() == 2
    page = text_of(client.get(appointment_url(world, world.appointment)))
    assert "ENG 102" in page and "ENG 101" not in page


def test_intake_answers_freeze_when_the_appointment_starts(client, world) -> None:
    form = make_form(world.center)
    past = appointment_for(world, world.student, days=-1)
    answer(form, world.student, INTAKE_ANSWERS, appointment=past)
    client.force_login(world.student)
    page = text_of(client.get(intake_url(world, past)))
    assert "can't change after the appointment starts" in page and "ENG 101" in page
    client.post(intake_url(world, past), form_data(form.published(), INTAKE_ANSWERS))
    assert Response.objects.filter(appointment=past).count() == 1
    # A student who left a group can't answer for it.
    group = appointment_for(world, [world.student, make_member(world.center, Role.STUDENT)], days=3)
    Participant.objects.filter(appointment=group, student=world.student).update(
        canceled_at=timezone.now()
    )
    assert client.get(intake_url(world, group)).status_code == 404


def test_a_student_changes_their_registration_answers(client, world, answered) -> None:
    registration, _first, _later = answered
    url = reverse("forms:my_registration", kwargs={"slug": world.center.slug})
    client.force_login(world.student)
    page = client.get(url).content.decode()
    assert 'value="Chemistry"' in page
    changed = {**REGISTRATION_ANSWERS, "What is your major?": "Physics"}
    data = form_data(registration.published(), changed, prefix="registration")
    assert client.post(url, data).status_code == 302
    assert Response.objects.filter(form=registration, student=world.student).count() == 3
    assert 'value="Physics"' in client.get(url).content.decode()
    # The center page links to it.
    center_page = text_of(client.get(world.center.get_absolute_url()))
    assert "Your registration answers" in center_page
