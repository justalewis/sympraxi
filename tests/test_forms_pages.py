"""The form builder's pages, driven the way a browser would, with and without htmx."""

import html

import pytest
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.forms.models import Form, FormKind, FormVersion, Question, Response
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests.forms_world import (
    INTAKE,
    INTAKE_ANSWERS,
    answer,
    director_of,
    form_data,
    make_form,
    question,
)
from tests.scheduling_world import World, build_world, make_member

pytestmark = pytest.mark.django_db

HTMX = {"HX-Request": "true"}


@pytest.fixture
def world(center) -> World:
    return build_world(center)


@pytest.fixture
def director(client, world):
    user = director_of(world.center)
    client.force_login(user)
    return user


def text_of(response) -> str:
    return html.unescape(" ".join(response.content.decode().split()))


def url(name: str, world: World, **kwargs: object) -> str:
    return reverse(name, kwargs={"slug": world.center.slug, **kwargs})


def labels(version: FormVersion) -> list[str]:
    return [q.label for q in version.ordered_questions()]


def test_the_list_shows_each_form_and_what_can_be_added(client, world, director) -> None:
    make_form(world.center)
    page = text_of(client.get(url("forms:list", world)))
    assert "Intake: every schedule without its own form" in page
    assert "Version 1" in page
    page = text_of(client.get(url("forms:create", world)))
    assert "Registration: asked the first time a student books" in page
    assert f"Intake: asked when booking on {world.schedule.name}" in page
    # The default intake form already exists, so it isn't offered again.
    assert "Intake: asked when booking on any schedule without its own" not in page


def test_a_director_adds_a_form_and_its_questions(client, world, director) -> None:
    response = client.post(url("forms:create", world), {"place": "registration"})
    form = Form.objects.get(center=world.center, kind=FormKind.REGISTRATION)
    assert response.status_code == 302 and response["Location"].endswith(f"{form.pk}/")
    assert AuditEvent.objects.filter(action=AuditAction.FORM_CREATED).exists()
    add = url("forms:question_create", world, form_id=form.pk)
    response = client.post(
        add,
        {
            "label": "What year are you in?",
            "kind": "one_choice",
            "new_options": "First year\nSecond year\n\nThird year or later",
            "shown_to_staff": "on",
        },
    )
    assert response.status_code == 302
    draft = form.draft()
    year = question(draft, "What year are you in?")
    assert [o.label for o in year.options.all()] == [
        "First year",
        "Second year",
        "Third year or later",
    ]
    assert year.shown_to_staff and not year.required
    # A follow-up can depend on it; the page lists each of its options.
    page = text_of(client.get(add))
    assert "Only when “What year are you in?” is answered “Second year”" in page
    second = year.options.get(label="Second year")
    response = client.post(
        add,
        {
            "label": "Did you transfer?",
            "kind": "yes_no",
            "condition": f"{year.key.hex}:{second.key.hex}",
            "is_demographic": "on",
        },
    )
    assert response.status_code == 302
    follow = question(draft, "Did you transfer?")
    assert (follow.show_if_question, follow.show_if_option) == (year.key, second.key)
    assert [o.label for o in follow.options.all()] == ["Yes", "No"]


def test_demographic_questions_cannot_be_required(client, world, director) -> None:
    form = make_form(world.center, FormKind.REGISTRATION, questions=[], publish=False)
    response = client.post(
        url("forms:question_create", world, form_id=form.pk),
        {"label": "Your age?", "kind": "number", "is_demographic": "on", "required": "on"},
    )
    assert response.status_code == 200
    assert "always optional" in text_of(response)
    assert not form.draft().questions.exists()


def test_changing_a_question_keeps_its_keys(client, world, director) -> None:
    form = make_form(world.center)
    answer(form, world.student, INTAKE_ANSWERS)
    client.post(url("forms:detail", world, form_id=form.pk), {"action": "start_draft"})
    draft = form.draft()
    work = question(draft, "What would you like to work on?")
    keys = {o.label: o.key.hex for o in work.options.all()}
    edit = url("forms:question_edit", world, question_id=work.pk)
    page = client.get(edit).content.decode()
    # The kind of answer is locked: people answered it in version 1.
    assert 'name="kind"' in page and "disabled" in page.split('name="kind"')[1].split(">")[0]
    response = client.post(
        edit,
        {
            "label": "What should the session focus on?",
            f"option-{keys['Ideas']}": "Ideas and argument",
            f"place-{keys['Ideas']}": "3",
            f"option-{keys['Organization']}": "Organization",
            f"place-{keys['Organization']}": "1",
            f"option-{keys['Citations']}": "Citations",
            f"place-{keys['Citations']}": "2",
            f"remove-{keys['Citations']}": "on",
            "new_options": "Grammar",
        },
    )
    assert response.status_code == 302
    work.refresh_from_db()
    assert work.label == "What should the session focus on?"
    assert work.kind == "several_choices"
    after = [(o.label, o.key.hex) for o in work.options.order_by("position")]
    assert after[:2] == [
        ("Organization", keys["Organization"]),
        ("Ideas and argument", keys["Ideas"]),
    ]
    assert after[2][0] == "Grammar"
    # Version 1 is untouched.
    v1 = form.published()
    assert [o.label for o in question(v1, "What would you like to work on?").options.all()] == [
        "Ideas",
        "Organization",
        "Citations",
    ]


def test_a_published_question_is_not_edited_in_place(client, world, director) -> None:
    form = make_form(world.center)
    published = question(form.published(), "What course is this for?")
    response = client.get(url("forms:question_edit", world, question_id=published.pk))
    assert response.status_code == 302
    assert response["Location"] == url("forms:detail", world, form_id=form.pk)


def test_moving_questions_without_javascript(client, world, director) -> None:
    form = make_form(world.center, publish=False)
    draft = form.draft()
    detail = url("forms:detail", world, form_id=form.pk)
    due = question(draft, "When is it due?")
    response = client.post(detail, {"action": "up", "question": str(due.pk)})
    assert response.status_code == 302 and response["Location"] == f"{detail}#question-{due.pk}"
    assert labels(draft).index("When is it due?") == 3
    page = text_of(client.get(detail))
    assert "Moved “When is it due?” up." in page
    first = question(draft, "What course is this for?")
    client.post(detail, {"action": "up", "question": str(first.pk)})
    assert "That question is already first." in text_of(client.get(detail))
    # A follow-up stays below the question it depends on.
    follow = question(draft, "What kind of writing, then?")
    client.post(detail, {"action": "up", "question": str(follow.pk)})
    assert "has to stay below it" in text_of(client.get(detail))
    assert labels(draft).index("What kind of writing, then?") == 2


def test_moving_questions_with_htmx_keeps_focus_and_says_what_happened(
    client, world, director
) -> None:
    form = make_form(world.center, publish=False)
    draft = form.draft()
    due = question(draft, "When is it due?")
    response = client.post(
        url("forms:detail", world, form_id=form.pk),
        {"action": "down", "question": str(due.pk)},
        headers=HTMX,
    )
    page = response.content.decode()
    assert response.status_code == 200
    assert "Moved “When is it due?” down." in html.unescape(page)
    # The button keeps its id, so htmx puts focus back on it after the swap.
    assert f'id="down-{due.pk}"' in page
    assert labels(draft)[-3:] == [
        "How many pages is it?",
        "When is it due?",
        "Anything else your tutor should know?",
    ]
    # The last question's "Move down" is there but marked unavailable, so it can hold focus.
    last = question(draft, "Anything else your tutor should know?")
    assert f'id="down-{last.pk}" aria-disabled="true"' in page


def test_removing_a_question(client, world, director) -> None:
    form = make_form(world.center, publish=False)
    draft = form.draft()
    source = question(draft, "What kind of writing is it?")
    page = text_of(client.get(url("forms:question_remove", world, question_id=source.pk)))
    assert "These questions follow up on it" in page and "What kind of writing, then?" in page
    client.post(url("forms:question_remove", world, question_id=source.pk))
    assert "What kind of writing is it?" in labels(draft)
    pages = question(draft, "How many pages is it?")
    response = client.post(url("forms:question_remove", world, question_id=pages.pk))
    assert response.status_code == 302
    assert "How many pages is it?" not in labels(draft)
    assert [q.position for q in draft.ordered_questions()] == list(range(1, len(INTAKE)))


def test_publishing_shows_what_changes(client, world, director) -> None:
    form = make_form(world.center)
    detail = url("forms:detail", world, form_id=form.pk)
    client.post(detail, {"action": "start_draft"})
    draft = form.draft()
    course = question(draft, "What course is this for?")
    client.post(
        url("forms:question_edit", world, question_id=course.pk),
        {"label": "Which course is it for?", "required": "on"},
    )
    client.post(
        url("forms:question_remove", world, question_id=question(draft, "How many pages is it?").pk)
    )
    client.post(
        url("forms:question_create", world, form_id=form.pk),
        {"label": "Instructor?", "kind": "short_text"},
    )
    page = text_of(client.get(url("forms:publish", world, form_id=form.pk)))
    assert "Added: Instructor?" in page
    assert "Removed: How many pages is it?" in page
    assert "Changed: Which course is it for?" in page
    response = client.post(url("forms:publish", world, form_id=form.pk))
    assert response.status_code == 302
    published = form.published()
    assert published.number == 2 and form.draft() is None
    event = AuditEvent.objects.get(action=AuditAction.FORM_PUBLISHED, details__version=2)
    assert event.details["questions"] == len(INTAKE)


def test_an_empty_draft_cannot_be_published(client, world, director) -> None:
    form = make_form(world.center, questions=[], publish=False)
    page = text_of(client.get(url("forms:publish", world, form_id=form.pk)))
    assert "Add at least one question." in page
    client.post(url("forms:publish", world, form_id=form.pk))
    assert form.published() is None


def test_the_preview_runs_the_form_and_saves_nothing(client, world, director) -> None:
    form = make_form(world.center, publish=False)
    preview = url("forms:preview", world, form_id=form.pk)
    page = text_of(client.get(preview))
    assert "Nothing you enter here is saved." in page
    version = form.draft()
    first_page = [q["label"] for q in INTAKE if "condition" not in q]
    answers = {**INTAKE_ANSWERS, "What kind of writing is it?": "Something else"}
    page = text_of(
        client.post(preview, form_data(version, answers, prefix="preview", shown=first_page))
    )
    assert "What kind of writing, then?" in page and "Follow-up questions appeared below." in page
    answers["What kind of writing, then?"] = "A zine"
    page = text_of(client.post(preview, form_data(version, answers, prefix="preview")))
    assert "Those answers would be accepted. Nothing was saved." in page
    assert not Response.objects.exists()


def test_an_earlier_version_can_be_previewed(client, world, director) -> None:
    form = make_form(world.center)
    client.post(url("forms:detail", world, form_id=form.pk), {"action": "start_draft"})
    page = text_of(client.get(url("forms:preview", world, form_id=form.pk) + "?version=1"))
    assert "version 1" in page and "This is how students saw this version." in page


def test_throwing_away_a_draft(client, world, director) -> None:
    form = make_form(world.center)
    detail = url("forms:detail", world, form_id=form.pk)
    client.post(detail, {"action": "start_draft"})
    assert form.draft() is not None
    client.post(detail, {"action": "discard"})
    assert form.draft() is None and form.published().number == 1
    assert "Make changes" in text_of(client.get(detail))


def test_the_introduction_is_part_of_the_draft(client, world, director) -> None:
    form = make_form(world.center, publish=False)
    client.post(
        url("forms:detail", world, form_id=form.pk),
        {"action": "intro", "intro": "Tell us about your writing."},
    )
    assert form.draft().intro == "Tell us about your writing."
    client.post(url("forms:publish", world, form_id=form.pk))
    client.force_login(world.student)
    book = reverse(
        "scheduling:book", kwargs={"slug": world.center.slug, "schedule_id": world.schedule.pk}
    )
    assert "Tell us about your writing." in text_of(client.get(f"{book}?{world.slot_query()}"))


def test_stopping_a_form_stops_asking(client, world, director) -> None:
    form = make_form(world.center)
    client.post(url("forms:detail", world, form_id=form.pk), {"action": "stop"})
    form.refresh_from_db()
    assert not form.is_active
    assert AuditEvent.objects.filter(
        action=AuditAction.FORM_CHANGED, details__is_active=False
    ).exists()
    client.force_login(world.student)
    book = reverse(
        "scheduling:book", kwargs={"slug": world.center.slug, "schedule_id": world.schedule.pk}
    )
    assert "About this appointment" not in text_of(client.get(f"{book}?{world.slot_query()}"))


def test_only_directors_reach_the_builder(client, world) -> None:
    form = make_form(world.center)
    for role in (Role.TUTOR, Role.FRONT_DESK):
        client.force_login(make_member(world.center, role))
        assert client.get(url("forms:list", world)).status_code == 403
        assert client.get(url("forms:detail", world, form_id=form.pk)).status_code == 403
    client.force_login(world.student)
    assert client.get(url("forms:detail", world, form_id=form.pk)).status_code == 403
    assert Question.objects.filter(version__form=form).count() == len(INTAKE)
