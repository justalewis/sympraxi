"""Session-report questions: answers on drafts, finalizing, the final-record lock,
amendments, group copies, who sees what, and the FERPA export."""

import pytest
from django.db import DatabaseError, transaction
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.forms import builder, report
from sympraxi.forms.builder import QuestionData
from sympraxi.forms.models import Answer, FormKind, Response
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export, services
from sympraxi.records.models import Amendment, SessionRecord
from tests.forms_world import (
    REPORT,
    REPORT_ANSWERS,
    director_of,
    make_form,
    question,
    report_answers,
    values,
)
from tests.records_world import add_records, appointment_for, membership, topics
from tests.scheduling_world import build_world, make_member

DRAFT = "Did the student bring a draft?"
WORKED = "What did you mostly work on?"
WHICH = "Which sentences?"
NEXT = "What should the next tutor know?"


@pytest.fixture
def world(center):
    world = add_records(build_world(center))
    world.report_form = make_form(center, FormKind.REPORT, questions=REPORT)
    return world


def content(world, by_label=None, **fields) -> services.Content:
    values_given = REPORT_ANSWERS if by_label is None else by_label
    return services.Content(
        minutes=30,
        summary=fields.pop("summary", "We worked on your paragraphs."),
        topics=topics("argument:thesis"),
        answers=report_answers(world.report_form, values_given),
        **fields,
    )


def draft(world, days=-3, student=None):
    appointment = appointment_for(world, student or world.student, days=days)
    return services.start_for_appointment(
        actor=world.tutor, appointment=appointment, student=student or world.student
    )


def final(world, by_label=None, days=-3):
    rec = draft(world, days=days)
    return services.finalize(
        actor=world.tutor, rec=rec, content=content(world, by_label), notify=False
    )


def answers_by_label(rec: SessionRecord) -> dict[str, str]:
    shown = report.for_staff(rec)
    assert shown is not None
    return {a.question: a.answer for a in shown.answers}


def refused(statement) -> None:
    with pytest.raises(DatabaseError), transaction.atomic():
        statement()


def new_version(world, change):
    """Publish a new version of the report questions after `change(draft)`."""
    actor = director_of(world.center)
    next_draft = builder.start_draft(actor=actor, form=world.report_form)
    change(next_draft)
    return builder.publish(actor=actor, draft=next_draft)


def reword(draft_version, label, new_label, **flags):
    q = question(draft_version, label)
    builder.save_question(
        draft=draft_version,
        question=q,
        data=QuestionData(
            kind=q.kind,
            label=new_label,
            required=q.required,
            shown_to_student=flags.get("shown_to_student", q.shown_to_student),
            condition=(q.show_if_question, q.show_if_option) if q.show_if_question else None,
            options=[(o.key, o.label) for o in q.options.all()],
        ),
    )


# --- Drafts ---------------------------------------------------------------------------


def test_each_draft_save_replaces_the_answers(world) -> None:
    rec = draft(world)
    services.save_draft(actor=world.tutor, rec=rec, content=content(world))
    first = Response.objects.get(record=rec)
    changed = {**REPORT_ANSWERS, WORKED: "Sources", NEXT: "Bring the rubric."}
    services.save_draft(actor=world.tutor, rec=rec, content=content(world, changed))
    response = Response.objects.get(record=rec)
    assert response.pk != first.pk
    assert not Response.objects.filter(pk=first.pk).exists()
    assert answers_by_label(rec) == {DRAFT: "Yes", WORKED: "Sources", NEXT: "Bring the rubric."}
    assert response.student == world.student
    assert response.answered_by == world.tutor
    assert response.appointment == rec.appointment


def test_saving_without_answers_leaves_them_alone(world) -> None:
    rec = draft(world)
    services.save_draft(actor=world.tutor, rec=rec, content=content(world))
    services.save_draft(
        actor=world.tutor,
        rec=rec,
        content=services.Content(minutes=30, summary="Changed", topics=topics("argument:thesis")),
    )
    assert answers_by_label(rec)[WORKED] == "Organization"


def test_a_draft_moves_to_the_version_asked_now(world) -> None:
    rec = draft(world)
    services.save_draft(actor=world.tutor, rec=rec, content=content(world))
    v2 = new_version(world, lambda d: reword(d, WORKED, "What took most of the session?"))
    questions = report.questions_for(center=world.center, rec=rec, amend=False, data=None)
    assert questions is not None and questions.version == v2
    # The earlier answers fill in the new version, by key.
    field = questions.form["q-" + question(v2, "What took most of the session?").key.hex]
    assert (
        field.value()
        == question(v2, "What took most of the session?").options.get(label="Organization").key.hex
    )
    services.save_draft(
        actor=world.tutor,
        rec=rec,
        content=services.Content(
            minutes=30,
            summary="x",
            topics=topics("argument:thesis"),
            answers=report.ReportAnswers(version=v2, values=values(v2, {DRAFT: "No"})),
        ),
    )
    assert Response.objects.get(record=rec).version == v2


def test_a_draft_record_deleted_takes_its_answers(world) -> None:
    rec = draft(world)
    services.save_draft(actor=world.tutor, rec=rec, content=content(world))
    rec.delete()
    assert not Response.objects.filter(form=world.report_form).exists()


# --- Finalizing -----------------------------------------------------------------------


def test_finalizing_needs_every_required_answer_that_shows(world) -> None:
    rec = draft(world)
    with pytest.raises(services.RecordError, match="Answer “Did the student bring a draft\\?”"):
        services.finalize(actor=world.tutor, rec=rec, content=content(world, {}), notify=False)
    rec.refresh_from_db()
    assert not rec.is_final
    # The follow-up is required only once its condition is met.
    sentences = {DRAFT: "No", WORKED: "Sentences"}
    with pytest.raises(services.RecordError, match="Which sentences"):
        services.finalize(
            actor=world.tutor, rec=rec, content=content(world, sentences), notify=False
        )
    services.finalize(
        actor=world.tutor,
        rec=rec,
        content=content(world, {**sentences, WHICH: "Run-ons"}),
        notify=False,
    )
    rec.refresh_from_db()
    assert rec.is_final


def test_problems_list_the_missing_answers(world) -> None:
    rec = draft(world)
    assert "Answer “Did the student bring a draft?”." in services.finalize_problems(rec)
    services.save_draft(actor=world.tutor, rec=rec, content=content(world))
    assert not [p for p in services.finalize_problems(rec) if p.startswith("Answer")]


def test_questions_not_in_use_are_not_asked_or_needed(world) -> None:
    builder.set_active(actor=director_of(world.center), form=world.report_form, active=False)
    rec = draft(world)
    assert report.questions_for(center=world.center, rec=rec, amend=False, data=None) is None
    assert not [p for p in services.finalize_problems(rec) if p.startswith("Answer")]


# --- The final-record lock ------------------------------------------------------------


def test_final_answers_change_only_through_an_amendment(world) -> None:
    rec = final(world)
    response = Response.objects.get(record=rec)
    answer = response.answers.get(key=question(response.version, NEXT).key)
    refused(lambda: Answer.objects.filter(pk=answer.pk).update(text="Changed"))
    refused(lambda: Answer.objects.filter(pk=answer.pk).delete())
    refused(lambda: Response.objects.filter(pk=response.pk).delete())
    refused(lambda: Response.objects.filter(pk=response.pk).update(answered_by=world.student))
    which = question(response.version, WHICH)
    refused(lambda: Answer.objects.create(response=response, question=which, key=which.key))
    # A second response for the final record is refused too.
    refused(
        lambda: Response.objects.create(
            form=world.report_form,
            version=response.version,
            center=world.center,
            student=world.student,
            appointment=rec.appointment,
            record=rec,
            answered_by=world.tutor,
        )
    )
    assert answers_by_label(rec)[NEXT] == "Ask about the conclusion."


def test_a_report_response_must_be_about_its_record(world) -> None:
    rec = draft(world)
    other = make_member(world.center, Role.STUDENT)
    version = world.report_form.published()
    refused(
        lambda: Response.objects.create(
            form=world.report_form,
            version=version,
            center=world.center,
            student=other,
            appointment=rec.appointment,
            record=rec,
            answered_by=world.tutor,
        )
    )
    # Only session-report responses belong to a record.
    intake = make_form(world.center)
    refused(
        lambda: Response.objects.create(
            form=intake,
            version=intake.published(),
            center=world.center,
            student=world.student,
            record=rec,
            answered_by=world.student,
        )
    )


def test_an_amendment_keeps_each_changed_answer_before_and_after(world) -> None:
    rec = final(world)
    response = Response.objects.get(record=rec)
    change = services.Content.of(rec)
    assert change.answers is not None
    worked = question(response.version, WORKED)
    nxt = question(response.version, NEXT)
    sources = worked.options.get(label="Sources").key
    change.answers.values[worked.key] = [sources]
    del change.answers.values[nxt.key]
    amendment = services.amend(actor=world.tutor, rec=rec, content=change, reason="Wrong choice.")
    assert amendment.changes == {
        f"answer:{worked.key}": {
            "question": WORKED,
            "question_key": str(worked.key),
            "before": "Organization",
            "after": "Sources",
        },
        f"answer:{nxt.key}": {
            "question": NEXT,
            "question_key": str(nxt.key),
            "before": "Ask about the conclusion.",
            "after": "",
        },
    }
    # The same response and version, changed in place.
    after = Response.objects.get(record=rec)
    assert after.pk == response.pk and after.version == response.version
    assert answers_by_label(rec) == {DRAFT: "Yes", WORKED: "Sources"}
    assert after.answers.get(key=worked.key).options == [sources]


def test_amending_only_answers_is_a_change_and_nothing_is_not(world) -> None:
    rec = final(world)
    unchanged = services.Content.of(rec)
    with pytest.raises(services.RecordError, match="Nothing changed"):
        services.amend(actor=world.tutor, rec=rec, content=unchanged, reason="No reason.")
    change = services.Content.of(rec)
    assert change.answers is not None
    v = change.answers.version
    change.answers.values[question(v, DRAFT).key] = [question(v, DRAFT).options.get(label="No").key]
    amendment = services.amend(actor=world.tutor, rec=rec, content=change, reason="Misread.")
    assert list(amendment.changes) == [f"answer:{question(v, DRAFT).key}"]
    assert Amendment.objects.filter(record=rec).count() == 1


def test_an_amendment_keeps_required_answers(world) -> None:
    rec = final(world)
    change = services.Content.of(rec)
    assert change.answers is not None
    del change.answers.values[question(change.answers.version, DRAFT).key]
    with pytest.raises(services.RecordError, match="Answer “Did the student bring a draft"):
        services.amend(actor=world.tutor, rec=rec, content=change, reason="Oops.")
    assert answers_by_label(rec)[DRAFT] == "Yes"


def test_an_amendment_asks_the_version_answered(world) -> None:
    rec = final(world)
    v1 = Response.objects.get(record=rec).version
    new_version(world, lambda d: reword(d, WORKED, "What took most of the session?"))
    asked = report.questions_for(center=world.center, rec=rec, amend=True, data=None)
    assert asked is not None and asked.version == v1
    # A record finalized before the questions existed gets none to amend.
    assert (
        report.questions_for(center=world.center, rec=world.record, amend=True, data=None) is None
    )


# --- Group copies ---------------------------------------------------------------------


def test_group_copies_get_the_same_answers(world) -> None:
    mate = make_member(world.center, Role.STUDENT)
    group = appointment_for(world, [world.student, mate], days=-3, capacity=4)
    rec = services.start_for_appointment(
        actor=world.tutor, appointment=group, student=world.student
    )
    services.save_draft(actor=world.tutor, rec=rec, content=content(world))
    [copy] = services.copy_to_group(actor=world.tutor, rec=rec)
    response = Response.objects.get(record=copy)
    assert response.student == mate
    assert answers_by_label(copy) == answers_by_label(rec)
    assert not copy.is_final


# --- Who sees what --------------------------------------------------------------------


def test_the_student_sees_only_the_answers_marked_for_them(world) -> None:
    rec = final(world)
    shown = report.for_student(rec)
    assert [(a.question, a.answer) for a in shown] == [(WORKED, "Organization")]
    staff = report.for_staff(rec)
    assert staff is not None
    assert {a.question: a.student_sees for a in staff.answers} == {
        DRAFT: False,
        WORKED: True,
        NEXT: False,
    }
    # Drafts show the student nothing.
    rec2 = draft(world, days=-5)
    services.save_draft(actor=world.tutor, rec=rec2, content=content(world))
    assert report.for_student(rec2) == []


def test_unmarking_a_question_hides_earlier_answers_and_marking_shows_none(world) -> None:
    rec = final(world)

    def flip(d):
        reword(d, WORKED, WORKED, shown_to_student=False)
        reword(d, NEXT, NEXT, shown_to_student=True)

    new_version(world, flip)
    # The earlier answer to WORKED is hidden now; NEXT was written for staff only.
    assert report.for_student(rec) == []


def test_record_pages_show_the_answers_to_staff_and_audit_the_read(client, world) -> None:
    rec = final(world)
    response = Response.objects.get(record=rec)
    client.force_login(world.tutor)
    url = reverse("records:detail", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    page = client.get(url).content.decode()
    assert "Ask about the conclusion." in page
    assert "the student sees this" in page
    event = AuditEvent.objects.filter(action="records.record_viewed", target_id=str(rec.pk)).last()
    assert event is not None
    assert event.details["report_answers"] == str(response.pk)
    assert "Ask about the conclusion." not in str(event.details)


def test_the_student_page_shows_only_their_answers(client, world) -> None:
    rec = final(world)
    client.force_login(world.student)
    page = client.get(
        reverse("records:my_record", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    ).content.decode()
    assert "Organization" in page
    assert "Ask about the conclusion." not in page
    assert DRAFT not in page
    writing = client.get(
        reverse("records:my_writing", kwargs={"slug": world.center.slug})
    ).content.decode()
    assert "Organization" in writing and "Ask about the conclusion." not in writing


def test_front_desk_staff_dont_see_the_answers(client, world) -> None:
    rec = final(world)
    desk = make_member(world.center, Role.FRONT_DESK)
    client.force_login(desk)
    url = reverse("records:detail", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    assert client.get(url).status_code == 403


# --- The record form ------------------------------------------------------------------


def record_post(world, rec, by_label, *, action="save", shown=None, **extra):
    from tests.forms_world import form_data

    version = world.report_form.published()
    data = {
        "summary": "We reorganized your paragraphs.",
        "next_steps": "",
        "staff_notes": "",
        "minutes": "30",
        "topics": ["argument:thesis"],
        "project": "",
        "action": action,
        **form_data(version, by_label, prefix="report", shown=shown),
        **extra,
    }
    url = reverse("records:edit", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    return url, data


def test_the_record_form_asks_the_questions_and_saves_them(client, world) -> None:
    rec = draft(world)
    client.force_login(world.tutor)
    url = reverse("records:edit", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    page = client.get(url).content.decode()
    assert "Questions from the center" in page
    assert DRAFT in page and "needed to finalize" in page
    assert "The student sees your answer" in page
    url, data = record_post(world, rec, REPORT_ANSWERS)
    response = client.post(url, data)
    assert response.status_code == 302
    assert answers_by_label(rec)[WORKED] == "Organization"


def test_finalizing_from_the_form_without_answers_keeps_a_draft(client, world) -> None:
    rec = draft(world)
    client.force_login(world.tutor)
    url, data = record_post(world, rec, {WORKED: "Ideas"}, action="finalize")
    response = client.post(url, data, follow=True)
    assert "It isn&#x27;t final yet" in response.content.decode()
    assert "Answer “Did the student bring a draft?”." in response.content.decode()
    rec.refresh_from_db()
    assert not rec.is_final
    assert answers_by_label(rec) == {WORKED: "Ideas"}


def test_a_follow_up_the_page_didnt_show_is_asked_before_saving(client, world) -> None:
    rec = draft(world)
    client.force_login(world.tutor)
    # The page showed every question but the follow-up; the answers call for it.
    url, data = record_post(
        world, rec, {DRAFT: "Yes", WORKED: "Sentences"}, shown=[DRAFT, WORKED, NEXT]
    )
    response = client.post(url, data)
    assert response.status_code == 200
    page = response.content.decode()
    assert WHICH in page
    assert "Follow-up questions appeared below" in page
    assert not Response.objects.filter(record=rec).exists()


def test_the_amend_form_changes_answers_with_a_reason(client, world) -> None:
    rec = final(world)
    client.force_login(world.tutor)
    url = reverse("records:amend", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    _url, data = record_post(world, rec, {**REPORT_ANSWERS, WORKED: "Sources"}, action="amend")
    data["reason"] = "Picked the wrong one."
    assert client.post(url, data).status_code == 302
    [amendment] = rec.amendments.all()
    key = question(Response.objects.get(record=rec).version, WORKED).key
    assert amendment.changes[f"answer:{key}"]["after"] == "Sources"
    page = client.get(
        reverse("records:detail", kwargs={"slug": world.center.slug, "record_id": rec.pk})
    ).content.decode()
    assert WORKED in page and "Organization" in page and "Sources" in page


# --- The FERPA export -----------------------------------------------------------------


def test_the_export_includes_every_answer(client, world) -> None:
    rec = final(world)
    director = director_of(world.center)
    data = export.build(membership(world.center, world.student), exported_by=director)
    assert data["version"] == 9
    [found] = [r for r in data["session_records"] if r["id"] == str(rec.pk)]
    [given] = found["report_answers"]
    assert {a["question"]: a["answer"] for a in given["answers"]} == {
        DRAFT: "Yes",
        WORKED: "Organization",
        NEXT: "Ask about the conclusion.",
    }
    worked = next(a for a in given["answers"] if a["question"] == WORKED)
    assert worked["value"] == [
        str(
            question(rec.report_responses.get().version, WORKED)
            .options.get(label="Organization")
            .key
        )
    ]
    client.force_login(director)
    page = client.get(
        reverse(
            "records:export",
            kwargs={
                "slug": world.center.slug,
                "membership_id": membership(world.center, world.student).pk,
            },
        )
    ).content.decode()
    assert "Ask about the conclusion." in page
    assert "Answers to the center's questions" in page
