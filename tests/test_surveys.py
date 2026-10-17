"""Surveys after sessions: who gets one and when, sending once (two sweeps racing),
the one-time link, anonymity, the opt-out, results and their threshold."""

import re
import threading
from datetime import date, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.contrib.sessions.models import Session
from django.core import mail
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role, SurveyTiming
from sympraxi.forms import builder, services, surveys, tasks
from sympraxi.forms.builder import QuestionData
from sympraxi.forms.models import (
    Answer,
    Form,
    FormKind,
    Response,
    SurveyInvitation,
    UsedSurveyToken,
    token_hash,
    used_digest,
)
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import services as records
from sympraxi.scheduling.models import AppointmentStatus, Attendance, Participant, Term
from tests.factories import CenterFactory
from tests.forms_world import (
    INTAKE,
    SURVEY,
    SURVEY_ANSWERS,
    anonymous,
    director_of,
    form_data,
    invite,
    make_form,
    question,
    values,
)
from tests.records_world import appointment_for, topics
from tests.scheduling_world import build_world, make_member, make_schedule

BASE = "https://writing.example.edu"
HELPFUL = "How helpful was the session?"


@pytest.fixture
def world(center):
    world = build_world(center)
    world.survey_form = make_form(center, FormKind.SURVEY, questions=SURVEY)
    return world


def came(world, *, days: float = -1, student=None, **fields):
    """A past appointment the student came to (marked as here)."""
    student = student or world.student
    appointment = appointment_for(world, student, days=days, **fields)
    Participant.objects.filter(appointment=appointment, student=student).update(
        attendance=Attendance.ATTENDED, attendance_marked_at=appointment.starts_at
    )
    return appointment


def finalize(world, appointment, student=None, *, now=None):
    student = student or world.student
    rec = records.start_for_appointment(actor=world.tutor, appointment=appointment, student=student)
    return records.finalize(
        actor=world.tutor,
        rec=rec,
        content=records.Content(minutes=30, summary="Done.", topics=topics("argument:thesis")),
        now=now,
        notify=False,
    )


def sweep(now) -> int:
    return surveys.send_due_invitations(base_url=BASE, now=now)


def link(message) -> str:
    found = re.search(r"https://writing\.example\.edu(/survey/[A-Za-z0-9_-]+/)", message.body)
    assert found, message.body
    return found.group(1)


def token_of(message) -> str:
    return link(message).split("/")[2]


def after_end(world, minutes: int = 60) -> None:
    center = world.center
    center.survey_timing = SurveyTiming.AFTER_END
    center.survey_delay_minutes = minutes
    center.save()


# --- When it goes out, and to whom ----------------------------------------------------


def test_after_the_record_is_finalized(world) -> None:
    appointment = came(world)
    rec = finalize(world, appointment)
    assert sweep(rec.finalized_at - timedelta(minutes=1)) == 0
    assert sweep(rec.finalized_at + timedelta(minutes=1)) == 1
    [message] = mail.outbox
    assert message.to == [world.student.email]
    assert "anonymous" in message.body
    assert world.tutor.display_name in message.body
    assert BASE + reverse("accounts:reminders") in message.body
    invitation = SurveyInvitation.objects.get()
    assert invitation.appointment == appointment and invitation.form == world.survey_form
    # Only the token's hash is kept, and it opens the survey.
    token = token_of(message)
    assert invitation.token_hash == token_hash(token) != token
    assert surveys.open_survey(token) is not None


def test_not_while_the_record_is_a_draft(world) -> None:
    appointment = came(world)
    rec = records.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=world.student
    )
    records.save_draft(
        actor=world.tutor, rec=rec, content=records.Content(minutes=30, summary="Not yet.")
    )
    assert sweep(timezone.now()) == 0


def test_a_set_time_after_the_appointment_ends(world) -> None:
    after_end(world, 60)
    appointment = came(world, days=-0.5)
    assert sweep(appointment.ends_at + timedelta(minutes=59)) == 0
    assert sweep(appointment.ends_at + timedelta(minutes=61)) == 1
    assert len(mail.outbox) == 1


def test_a_student_marked_late_gets_it_once_marked(world) -> None:
    after_end(world, 30)
    appointment = appointment_for(world, world.student, days=-0.5)
    assert sweep(appointment.ends_at + timedelta(hours=1)) == 0
    Participant.objects.filter(appointment=appointment).update(attendance=Attendance.ATTENDED)
    assert sweep(appointment.ends_at + timedelta(hours=2)) == 1


def test_never_for_a_canceled_appointment_or_a_no_show(world) -> None:
    after_end(world, 30)
    canceled = came(world, days=-0.5)
    canceled.status = AppointmentStatus.CANCELED
    canceled.canceled_at = canceled.starts_at
    canceled.save()
    missed = appointment_for(world, world.student, days=-0.4)
    Participant.objects.filter(appointment=missed).update(attendance=Attendance.NO_SHOW)
    later = max(canceled.ends_at, missed.ends_at) + timedelta(hours=1)
    assert sweep(later) == 0
    assert not SurveyInvitation.objects.exists()


def test_not_for_a_student_who_left_the_group(world) -> None:
    after_end(world, 30)
    other = make_member(world.center, Role.STUDENT)
    group = came(world, days=-0.5, capacity=4)
    Participant.objects.create(appointment=group, student=other, attendance=Attendance.ATTENDED)
    Participant.objects.filter(appointment=group, student=world.student).update(
        canceled_at=group.starts_at
    )
    assert sweep(group.ends_at + timedelta(hours=1)) == 1
    assert [m.to for m in mail.outbox] == [[other.email]]


def test_placeholders_and_meetings_that_werent_booked_get_none(world) -> None:
    after_end(world, 30)
    placeholder = came(world, days=-0.5, is_placeholder=True)
    assert sweep(placeholder.ends_at + timedelta(hours=1)) == 0
    world.center.survey_timing = SurveyTiming.AFTER_RECORD
    world.center.save()
    rec = records.start_standalone(
        actor=world.tutor,
        center=world.center,
        student=world.student,
        held_at=timezone.now() - timedelta(hours=2),
        minutes=20,
    )
    records.finalize(
        actor=world.tutor,
        rec=rec,
        content=records.Content(minutes=20, summary="Hallway.", topics=topics("argument:thesis")),
        notify=False,
    )
    assert sweep(timezone.now()) == 0


def test_students_can_turn_them_off(client, world) -> None:
    client.force_login(world.student)
    page = client.get(reverse("accounts:reminders")).content.decode()
    assert "Email me a short survey after my sessions" in page
    client.post(reverse("accounts:reminders"), {"reminders_for_appointments": "on"})
    world.student.refresh_from_db()
    assert world.student.survey_emails is False
    rec = finalize(world, came(world))
    assert sweep(rec.finalized_at + timedelta(minutes=1)) == 0
    assert mail.outbox == []


def test_only_surveys_that_fell_due_in_the_last_two_days(world) -> None:
    after_end(world, 30)
    old = came(world, days=-4)
    assert sweep(timezone.now()) == 0
    recent = came(world, days=-1)
    assert sweep(timezone.now()) == 1
    assert SurveyInvitation.objects.get().appointment == recent
    assert old.pk != recent.pk


def test_the_schedules_own_survey_or_the_default(world) -> None:
    center = world.center
    own = make_form(center, FormKind.SURVEY, questions=SURVEY, schedule=world.schedule)
    rec = finalize(world, came(world))
    assert sweep(rec.finalized_at + timedelta(minutes=1)) == 1
    assert SurveyInvitation.objects.get().form == own
    # Stopped, the schedule's survey gives way to the default.
    builder.set_active(actor=director_of(center), form=own, active=False)
    assert services.survey_form(world.schedule) == world.survey_form
    builder.set_active(actor=director_of(center), form=world.survey_form, active=False)
    assert services.survey_form(world.schedule) is None
    rec2 = finalize(world, came(world, days=-2))
    assert sweep(rec2.finalized_at + timedelta(minutes=1)) == 0


# --- Sending once ---------------------------------------------------------------------


def test_each_invitation_goes_out_once(world) -> None:
    rec = finalize(world, came(world))
    now = rec.finalized_at + timedelta(minutes=1)
    assert sweep(now) == 1
    assert sweep(now) == 0
    assert sweep(now + timedelta(hours=1)) == 0
    assert len(mail.outbox) == 1
    invitation = SurveyInvitation.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        SurveyInvitation.objects.create(
            appointment=invitation.appointment,
            student=invitation.student,
            form=invitation.form,
            sent_at=now,
            expires_at=now,
        )


def test_the_token_never_goes_through_the_database(world) -> None:
    """The scheduler queues the invitation's ID, not a message: the token is made by
    the worker, and only its hash is stored."""
    rec = finalize(world, came(world))
    queued = []
    task = tasks.send_survey_invitation

    def spy(invitation_id, base_url):
        queued.append((invitation_id, base_url))
        return task.call(invitation_id, base_url)

    fake = mock.Mock()
    fake.enqueue.side_effect = spy
    with mock.patch.object(tasks, "send_survey_invitation", fake):
        assert surveys.invite(
            rec.appointment_id,
            world.student.pk,
            base_url=BASE,
            now=rec.finalized_at + timedelta(minutes=1),
        )
    invitation = SurveyInvitation.objects.get()
    assert queued == [(invitation.pk, BASE)]
    token = token_of(mail.outbox[0])
    # Running the task again sends nothing.
    task.call(invitation.pk, BASE)
    assert len(mail.outbox) == 1
    assert SurveyInvitation.objects.get().token_hash == token_hash(token)


@pytest.mark.django_db(transaction=True)
def test_two_schedulers_sweeping_at_once_send_each_survey_once() -> None:
    center = CenterFactory()
    world = build_world(center)
    world.survey_form = make_form(center, FormKind.SURVEY, questions=SURVEY)
    others = [make_member(center, Role.STUDENT) for _ in range(2)]
    group = came(world, capacity=4)
    for other in others:
        Participant.objects.create(appointment=group, student=other)
    after_end(world, 30)
    Participant.objects.filter(appointment=group).update(attendance=Attendance.ATTENDED)
    now = group.ends_at + timedelta(hours=1)
    barrier = threading.Barrier(2)
    counts: list[int] = []
    lock = threading.Lock()

    def run() -> None:
        try:
            barrier.wait()
            sent = sweep(now)
        finally:
            connection.close()
        with lock:
            counts.append(sent)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sum(counts) == 3
    assert sorted(m.to[0] for m in mail.outbox) == sorted(
        [world.student.email, *(o.email for o in others)]
    )
    assert SurveyInvitation.objects.count() == 3


# --- The link ---------------------------------------------------------------------------


def answer_url(token: str) -> str:
    return reverse("survey:answer", kwargs={"token": token})


def posted(form: Form, by_label=SURVEY_ANSWERS):
    version = form.published()
    assert version is not None
    return form_data(version, by_label, prefix="survey")


def test_the_link_opens_without_signing_in_and_works_once(client, world) -> None:
    _invitation, token = invite(world.appointment, world.student, world.survey_form)
    page = client.get(answer_url(token))
    assert page.status_code == 200
    assert HELPFUL in page.content.decode()
    response = client.post(answer_url(token), posted(world.survey_form))
    assert response.status_code == 302 and response["Location"] == reverse("survey:thanks")
    assert client.get(reverse("survey:thanks")).status_code == 200
    # Used: the same page as any dead link, and nothing more is stored.
    again = client.post(answer_url(token), posted(world.survey_form))
    assert again.status_code == 404
    assert client.get(answer_url(token)).status_code == 404
    assert Response.objects.filter(form=world.survey_form).count() == 1


def test_missing_required_answers_dont_use_the_link(client, world) -> None:
    _invitation, token = invite(world.appointment, world.student, world.survey_form)
    response = client.post(answer_url(token), posted(world.survey_form, {"What helped most?": []}))
    assert response.status_code == 200
    assert "This field is required" in response.content.decode()
    assert surveys.open_survey(token) is not None
    assert not UsedSurveyToken.objects.exists()


def test_expired_and_stopped_surveys_are_dead(client, world) -> None:
    invitation, token = invite(world.appointment, world.student, world.survey_form)
    assert surveys.open_survey(token, now=invitation.expires_at - timedelta(minutes=1))
    assert surveys.open_survey(token, now=invitation.expires_at) is None
    builder.set_active(actor=director_of(world.center), form=world.survey_form, active=False)
    assert client.get(answer_url(token)).status_code == 404


def test_a_link_used_twice_at_once_stores_one_response(world) -> None:
    _invitation, token = invite(world.appointment, world.student, world.survey_form)
    first = surveys.open_survey(token)
    second = surveys.open_survey(token)
    assert first is not None and second is not None
    answers = values(first.version, SURVEY_ANSWERS)
    assert surveys.submit(first, token, answers) is True
    assert surveys.submit(second, token, answers) is False
    assert Response.objects.filter(form=world.survey_form).count() == 1


def test_a_new_version_published_meanwhile_is_answered(client, world) -> None:
    _invitation, token = invite(world.appointment, world.student, world.survey_form)
    actor = director_of(world.center)
    draft = builder.start_draft(actor=actor, form=world.survey_form)
    v2 = builder.publish(actor=actor, draft=draft)
    client.post(answer_url(token), posted(world.survey_form))
    assert Response.objects.get(form=world.survey_form).version == v2


# --- Anonymity --------------------------------------------------------------------------


def _column_values(row) -> list[str]:
    return [str(getattr(row, f.attname)) for f in row._meta.concrete_fields]


def test_nothing_joins_a_response_to_the_student(client, world) -> None:
    """Answering stores the answers and a used-link digest, and nothing else that names
    the student, the appointment, the invitation or when it was answered."""
    rec = finalize(world, came(world))
    assert sweep(rec.finalized_at + timedelta(minutes=1)) == 1
    token = token_of(mail.outbox[0])
    invitation = SurveyInvitation.objects.get()
    before = _column_values(invitation)
    # Signed in, even: the page never looks at who it is.
    client.force_login(world.student)
    audit_count = AuditEvent.objects.count()
    session_count = Session.objects.count()
    client.get(answer_url(token))
    assert client.post(answer_url(token), posted(world.survey_form)).status_code == 302

    response = Response.objects.get(form=world.survey_form)
    assert response.student_id is None
    assert response.appointment_id is None
    assert response.record_id is None
    assert response.answered_by_id is None
    assert response.created_at is None
    assert response.answered_on == timezone.localdate(timezone=ZoneInfo(world.center.time_zone))
    used = UsedSurveyToken.objects.get()
    assert used.digest == used_digest(token) != invitation.token_hash
    # The invitation is exactly as it was: no used flag, no time.
    invitation.refresh_from_db()
    assert _column_values(invitation) == before
    # Nothing else was written about the visit.
    assert AuditEvent.objects.count() == audit_count
    assert Session.objects.count() == session_count

    identifying = {
        str(world.student.pk),
        str(world.tutor.pk),
        str(rec.appointment_id),
        str(rec.pk),
        str(invitation.pk),
        invitation.token_hash,
        token,
    }
    written = [response, used, *Answer.objects.filter(response=response)]
    for row in written:
        assert not identifying & set(_column_values(row)), row


def test_the_database_keeps_survey_responses_anonymous(world) -> None:
    version = world.survey_form.published()

    def refused(**fields) -> None:
        with pytest.raises(DatabaseError), transaction.atomic():
            Response.objects.create(
                form=world.survey_form, version=version, center=world.center, **fields
            )

    today = date.today()
    refused(student=world.student, answered_on=today, created_at=None)
    refused(appointment=world.appointment, answered_on=today, created_at=None)
    refused(answered_by=world.student, answered_on=today, created_at=timezone.now())
    refused(answered_on=today, created_at=timezone.now())
    # And only surveys may be anonymous.
    intake = make_form(world.center, questions=INTAKE)
    with pytest.raises(DatabaseError), transaction.atomic():
        Response.objects.create(
            form=intake,
            version=intake.published(),
            center=world.center,
            created_at=None,
            answered_on=today,
        )
    # Used links can't be removed to make them work again.
    UsedSurveyToken.objects.create(digest="x" * 64)
    with pytest.raises(DatabaseError), transaction.atomic():
        UsedSurveyToken.objects.filter(digest="x" * 64).delete()


def test_the_export_leaves_survey_answers_out(client, world) -> None:
    from tests.records_world import membership

    _invitation, token = invite(world.appointment, world.student, world.survey_form)
    client.post(answer_url(token), posted(world.survey_form))
    from sympraxi.records import export

    data = export.build(membership(world.center, world.student), exported_by=world.tutor)
    assert "More evening hours." not in str(data)


# --- Results ----------------------------------------------------------------------------


def current(world):
    found = surveys.current_period(surveys.periods(world.center), timezone.localdate())
    assert found is not None
    return found


def test_results_show_only_from_five_responses(world) -> None:
    for _ in range(4):
        anonymous(world.survey_form, SURVEY_ANSWERS)
    hidden = surveys.results(world.survey_form, current(world))
    assert hidden.responses == 4 and not hidden.shown and hidden.questions == []
    anonymous(world.survey_form, {HELPFUL: "Not helpful"})
    shown = surveys.results(world.survey_form, current(world))
    assert shown.shown and shown.responses == 5
    helpful = next(q for q in shown.questions if q.label == HELPFUL)
    assert [(o.label, o.count, o.percent) for o in helpful.options] == [
        ("Very helpful", 4, 80),
        ("Somewhat helpful", 0, 0),
        ("Not helpful", 1, 20),
    ]
    better = next(q for q in shown.questions if q.label == "What could we do better?")
    assert better.answered == 4 and better.texts == ["More evening hours."] * 4


def test_results_count_by_key_across_versions(world) -> None:
    for _ in range(3):
        anonymous(world.survey_form, SURVEY_ANSWERS)
    actor = director_of(world.center)
    draft = builder.start_draft(actor=actor, form=world.survey_form)
    q = question(draft, HELPFUL)
    very = q.options.get(label="Very helpful")
    builder.save_question(
        draft=draft,
        question=q,
        data=QuestionData(
            kind=q.kind,
            label="How useful was it?",
            required=True,
            options=[
                (very.key, "Extremely helpful"),
                (q.options.get(label="Somewhat helpful").key, "Somewhat helpful"),
                (None, "I'm not sure"),
            ],
        ),
    )
    builder.publish(actor=actor, draft=draft)
    anonymous(world.survey_form, {"How useful was it?": "Extremely helpful"})
    anonymous(world.survey_form, {"How useful was it?": "I'm not sure"})
    found = surveys.results(world.survey_form, current(world))
    helpful = found.questions[0]
    assert helpful.label == "How useful was it?" and helpful.answered == 5
    assert [(o.label, o.count) for o in helpful.options] == [
        ("Extremely helpful", 4),
        ("Somewhat helpful", 0),
        ("I'm not sure", 1),
        ("Not helpful", 0),
    ]


def test_written_answers_are_sorted_not_in_the_order_given(world) -> None:
    for text in ["zebra", "Apple", "mango", "banana", "Cherry"]:
        anonymous(world.survey_form, {HELPFUL: "Very helpful", "What could we do better?": text})
    found = surveys.results(world.survey_form, current(world))
    better = next(q for q in found.questions if q.label == "What could we do better?")
    assert better.texts == ["Apple", "banana", "Cherry", "mango", "zebra"]


def test_each_response_counts_in_one_term_only(world) -> None:
    """Terms don't overlap here, so comparing two can't single out a response."""
    center = world.center
    old_first = date(2025, 1, 6)
    spring = Term.objects.create(
        center=center, name="Spring 2025", starts_on=old_first, ends_on=date(2025, 5, 2)
    )
    summer = Term.objects.create(
        center=center, name="Summer 2025", starts_on=date(2025, 6, 2), ends_on=date(2025, 8, 1)
    )
    # Between terms (May 20) counts in spring; summer starts June 2.
    for day in [date(2025, 3, 1)] * 4 + [date(2025, 5, 20)]:
        anonymous(world.survey_form, SURVEY_ANSWERS, on=day)
    for _ in range(5):
        anonymous(world.survey_form, SURVEY_ANSWERS, on=date(2025, 6, 10))
    by_term = {p.term: p for p in surveys.periods(center)}
    assert surveys.results(world.survey_form, by_term[spring]).responses == 5
    assert surveys.results(world.survey_form, by_term[summer]).responses == 5
    assert by_term[spring].ends_before == summer.starts_on


def test_the_results_page_for_directors(client, world) -> None:
    for _ in range(5):
        anonymous(world.survey_form, SURVEY_ANSWERS)
    director = director_of(world.center)
    client.force_login(director)
    url = reverse("forms:survey_results", kwargs={"slug": world.center.slug})
    page = client.get(url).content.decode()
    assert "5 people answered in this term." in page
    assert "More evening hours." in page and "Very helpful" in page
    tutor = make_member(world.center, Role.TUTOR)
    client.force_login(tutor)
    assert client.get(url).status_code == 403


def test_the_results_page_hides_too_few_responses(client, world) -> None:
    for _ in range(4):
        anonymous(world.survey_form, SURVEY_ANSWERS)
    client.force_login(director_of(world.center))
    url = reverse("forms:survey_results", kwargs={"slug": world.center.slug})
    page = client.get(url).content.decode()
    assert "Fewer than 5 people have answered in this term" in page
    assert "More evening hours." not in page
    # Choosing another term or survey through the form works the same way.
    term = Term.objects.filter(center=world.center).first()
    assert term is not None
    other = client.get(url, {"survey": str(world.survey_form.pk), "term": str(term.pk)})
    assert other.status_code == 200


def test_directors_say_when_surveys_go_out(client, world) -> None:
    client.force_login(director_of(world.center))
    url = reverse("forms:survey_settings", kwargs={"slug": world.center.slug})
    assert client.get(url).status_code == 200
    response = client.post(url, {"survey_timing": "end", "survey_delay_minutes": "120"})
    assert response.status_code == 302
    world.center.refresh_from_db()
    assert world.center.survey_timing == SurveyTiming.AFTER_END
    assert world.center.survey_delay_minutes == 120
    event = AuditEvent.objects.get(action="forms.survey_settings_changed")
    assert event.details == {"survey_timing": "end", "survey_delay_minutes": 120}


# --- Building them -----------------------------------------------------------------------


def test_directors_add_report_questions_and_surveys(client, center) -> None:
    world = build_world(center)
    second = make_schedule(center, world.schedule.starts_on, world.schedule.ends_on, name="Online")
    client.force_login(director_of(center))
    url = reverse("forms:create", kwargs={"slug": center.slug})
    page = client.get(url).content.decode()
    assert "Session report questions: tutors answer them in each session record" in page
    assert "Survey: sent after sessions on any schedule without its own" in page
    assert "Survey: sent after sessions on Online" in page
    for place in ("report", "survey", f"survey:{second.pk}"):
        assert client.post(url, {"place": place}).status_code == 302
    kinds = {(f.kind, f.schedule_id) for f in Form.objects.filter(center=center)}
    assert kinds == {(FormKind.REPORT, None), (FormKind.SURVEY, None), (FormKind.SURVEY, second.pk)}
    page = client.get(reverse("forms:create", kwargs={"slug": center.slug})).content.decode()
    assert "Session report questions:" not in page


def test_only_report_questions_can_be_shown_to_the_student(center) -> None:
    survey = make_form(center, FormKind.SURVEY, questions=[], publish=False)
    asked = builder.save_question(
        draft=survey.draft(),
        question=None,
        data=QuestionData(kind="short_text", label="Anything else?", shown_to_student=True),
    )
    assert not asked.shown_to_student
    report_form = make_form(center, FormKind.REPORT, questions=[], publish=False)
    shown = builder.save_question(
        draft=report_form.draft(),
        question=None,
        data=QuestionData(kind="short_text", label="Focus?", shown_to_student=True),
    )
    assert shown.shown_to_student
    actor = director_of(center)
    builder.publish(actor=actor, draft=shown.version)
    copy = builder.start_draft(actor=actor, form=report_form)
    assert question(copy, "Focus?").shown_to_student
    # A report form has no schedule, whatever is asked.
    other = CenterFactory()
    schedule = build_world(other).schedule
    made = builder.create_form(
        actor=director_of(other), center=other, kind=FormKind.REPORT, schedule=schedule
    )
    assert made.schedule is None
