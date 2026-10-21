"""Tutor training: reading modules, quizzes, the builder, hours, and the onboarding table."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.staff.models import Category
from sympraxi.training import progress
from sympraxi.training.models import Attempt, Completion, Item, ItemKind, Module
from tests.scheduling_world import make_member
from tests.staff_world import entry, today
from tests.training_world import make_module, make_quiz

pytestmark = pytest.mark.django_db


@pytest.fixture
def tutor(center):
    return make_member(center, Role.TUTOR, full_name="Toni Tutor")


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


def u(name: str, center, **kwargs) -> str:
    return reverse(f"training:{name}", kwargs={"slug": center.slug, **kwargs})


# --- A tutor works through a module ----------


def test_a_tutor_sees_published_modules_only(client, center, tutor) -> None:
    make_module(center, title="Published one")
    make_module(center, published=False, title="Still a draft")
    client.force_login(tutor)
    page = client.get(u("home", center)).content.decode()
    assert "Published one" in page and "Still a draft" not in page


def test_a_draft_module_and_its_items_are_closed_to_tutors(client, center, tutor) -> None:
    draft = make_module(center, published=False)
    client.force_login(tutor)
    assert client.get(u("module", center, module_id=draft.pk)).status_code == 404
    item = draft.items.first()
    assert client.get(u("item", center, item_id=item.pk)).status_code == 404


def test_students_have_no_training(client, center) -> None:
    client.force_login(make_member(center, Role.STUDENT))
    assert client.get(u("home", center)).status_code == 403


def test_marking_a_page_done_counts_once_and_completes_the_module(client, center, tutor) -> None:
    module = make_module(center)
    page = module.items.get(kind=ItemKind.PAGE)
    link = module.items.get(kind=ItemKind.LINK)
    quiz = module.items.get(kind=ItemKind.QUIZ)
    client.force_login(tutor)
    assert "We help writers." in client.get(u("item", center, item_id=page.pk)).content.decode()
    client.post(u("item", center, item_id=page.pk), {"action": "done"})
    client.post(u("item", center, item_id=page.pk), {"action": "done"})
    client.post(u("item", center, item_id=link.pk), {"action": "done"})
    assert Completion.objects.filter(person=tutor).count() == 2
    [status] = progress.statuses(tutor, center)
    assert (status.done, status.total, status.complete) == (2, 3, False)
    right = {f"q_{q.pk}": str(q.choices.get(is_correct=True).pk) for q in quiz.questions.all()}
    client.post(u("item", center, item_id=quiz.pk), right)
    [status] = progress.statuses(tutor, center)
    assert status.complete
    assert AuditEvent.objects.filter(action=AuditAction.TRAINING_MODULE_COMPLETED).count() == 1
    assert "Done" in client.get(u("home", center)).content.decode()


def test_a_link_opens_in_a_new_tab_safely(client, center, tutor) -> None:
    link = make_module(center).items.get(kind=ItemKind.LINK)
    client.force_login(tutor)
    page = client.get(u("item", center, item_id=link.pk)).content.decode()
    assert 'href="https://example.org/reading"' in page and 'rel="noopener noreferrer"' in page


# --- Quizzes ----------


def answers(quiz: Item, *, right: int) -> dict[str, str]:
    """Answers with the first `right` questions right and the rest wrong."""
    data = {}
    for n, question in enumerate(quiz.questions.all()):
        choice = question.choices.get(is_correct=n < right)
        data[f"q_{question.pk}"] = str(choice.pk)
    return data


def test_a_quiz_is_scored_on_the_server_and_can_be_retaken(client, center, tutor) -> None:
    quiz = make_module(center).items.get(kind=ItemKind.QUIZ)
    quiz.pass_percent = 100
    quiz.save()
    client.force_login(tutor)
    failed = client.post(u("item", center, item_id=quiz.pk), answers(quiz, right=1))
    page = failed.content.decode()
    assert "1 of 2 right" in page and "Look at this one again" in page
    attempt = Attempt.objects.get()
    assert not attempt.passed and attempt.percent == 50
    assert progress.done_item_ids(tutor) == set()
    passed = client.post(u("item", center, item_id=quiz.pk), answers(quiz, right=2))
    assert "You passed" in passed.content.decode()
    assert Attempt.objects.count() == 2 and quiz.pk in progress.done_item_ids(tutor)


def test_unanswered_and_invented_answers_count_as_wrong(center, tutor) -> None:
    module = make_module(center)
    quiz = module.items.get(kind=ItemKind.QUIZ)
    first = quiz.questions.first()
    attempt = progress.submit_quiz(tutor, quiz, {str(first.pk): "not-a-choice", "stray": "x"})
    assert (attempt.correct, attempt.total, attempt.passed) == (0, 2, False)
    # A choice from another question is not accepted as an answer to this one.
    other = quiz.questions.last().choices.get(is_correct=True)
    attempt = progress.submit_quiz(tutor, quiz, {str(first.pk): str(other.pk)})
    assert attempt.correct == 0


def test_a_quiz_with_no_questions_cannot_be_passed(center, tutor) -> None:
    module = make_module(center)
    empty = make_quiz(module, questions=0)
    assert not progress.submit_quiz(tutor, empty, {}).passed


def test_the_page_never_shows_which_answer_is_right(client, center, tutor) -> None:
    quiz = make_module(center).items.get(kind=ItemKind.QUIZ)
    client.force_login(tutor)
    page = client.get(u("item", center, item_id=quiz.pk)).content.decode()
    assert "is_correct" not in page and "Right answer" not in page


# --- The builder ----------


def test_a_director_builds_a_module_with_a_page_a_link_and_a_quiz(client, center, director) -> None:
    client.force_login(director)
    created = client.post(
        u("module_create", center), {"title": "Tutoring basics", "summary": "First week"}
    )
    module = Module.objects.get()
    assert created.status_code == 302 and not module.is_published
    edit = u("module_edit", center, module_id=module.pk)
    client.post(
        edit,
        {
            "action": "add_item",
            "kind": "page",
            "title": "Welcome",
            "body": "Hello",
            "pass_percent": 80,
        },
    )
    bad = client.post(
        edit, {"action": "add_item", "kind": "link", "title": "A video", "pass_percent": 80}
    )
    assert "needs an address" in bad.content.decode()
    client.post(
        edit,
        {
            "action": "add_item",
            "kind": "link",
            "title": "A video",
            "url": "https://example.org/v",
            "pass_percent": 80,
        },
    )
    response = client.post(
        edit, {"action": "add_item", "kind": "quiz", "title": "Quiz", "pass_percent": 70}
    )
    quiz = module.items.get(kind="quiz")
    assert response["Location"] == u("item_edit", center, item_id=quiz.pk)
    assert [i.title for i in module.items.all()] == ["Welcome", "A video", "Quiz"]
    client.post(
        u("item_edit", center, item_id=quiz.pk),
        {
            "action": "add_question",
            "prompt": "Who comes first?",
            "choice_1": "The writer",
            "choice_2": "The tutor",
            "choice_3": "",
            "correct": "1",
        },
    )
    [question] = quiz.questions.all()
    assert [(c.text, c.is_correct) for c in question.choices.all()] == [
        ("The writer", True),
        ("The tutor", False),
    ]
    assert AuditEvent.objects.filter(action=AuditAction.TRAINING_CONTENT_CHANGED).count() >= 5


def test_a_question_needs_two_answers_and_a_filled_right_one(client, center, director) -> None:
    quiz = make_module(center).items.get(kind=ItemKind.QUIZ)
    client.force_login(director)
    url = u("item_edit", center, item_id=quiz.pk)
    one = client.post(
        url, {"action": "add_question", "prompt": "Q?", "choice_1": "Only", "correct": "1"}
    )
    assert "This field is required" in one.content.decode()
    gap = client.post(
        url,
        {
            "action": "add_question",
            "prompt": "Q?",
            "choice_1": "A",
            "choice_2": "B",
            "correct": "4",
        },
    )
    assert "has to be one you filled in" in gap.content.decode()
    assert quiz.questions.count() == 2


def test_items_move_and_delete(client, center, director) -> None:
    module = make_module(center)
    client.force_login(director)
    edit = u("module_edit", center, module_id=module.pk)
    page = module.items.get(kind="page")
    client.post(edit, {"action": "down", "item": str(page.pk)})
    assert [i.title for i in module.items.all()][:2] == ["A reading", "Our values"]
    client.post(edit, {"action": "up", "item": str(page.pk)})
    assert [i.title for i in module.items.all()][:2] == ["Our values", "A reading"]
    client.post(edit, {"action": "up", "item": str(page.pk)})  # already first: nothing happens
    client.post(edit, {"action": "delete_item", "item": str(page.pk)})
    assert module.items.count() == 2


def test_deleting_a_question_and_publishing(client, center, director) -> None:
    module = make_module(center, published=False)
    quiz = module.items.get(kind="quiz")
    client.force_login(director)
    question = quiz.questions.first()
    client.post(
        u("item_edit", center, item_id=quiz.pk),
        {"action": "delete_question", "question": str(question.pk)},
    )
    assert quiz.questions.count() == 1
    client.post(
        u("module_edit", center, module_id=module.pk),
        {
            "action": "save",
            "title": "Welcome",
            "summary": "",
            "is_published": "on",
            "is_required": "on",
        },
    )
    module.refresh_from_db()
    assert module.is_published


def test_tutors_cannot_build_and_other_centers_cannot_edit(
    client, center, other_center, tutor
) -> None:
    module = make_module(center)
    client.force_login(tutor)
    assert client.get(u("manage", center)).status_code == 403
    outsider = make_member(other_center, Role.DIRECTOR)
    client.force_login(outsider)
    assert client.get(u("module_edit", center, module_id=module.pk)).status_code == 404


# --- Hours and onboarding ----------


def test_training_hours_come_from_the_time_clock(center, tutor) -> None:
    day = today(center) - timedelta(days=2)
    entry(center, tutor, day, (9, 0), (11, 30), Category.TRAINING)
    entry(center, tutor, day, (13, 0), (14, 0), Category.TUTORING)
    entry(center, tutor, day, (15, 0), None, Category.TRAINING)  # still clocked in
    removed = entry(center, tutor, day - timedelta(days=1), (9, 0), (10, 0), Category.TRAINING)
    removed.removed_at = timezone.now()
    removed.save()
    assert progress.training_hours(center, [tutor.pk]) == {tutor.pk: Decimal("2.50")}


def test_the_onboarding_table_shows_modules_hours_and_the_requirement(
    client, center, tutor, director
) -> None:
    center.training_hours_required = Decimal("3.00")
    center.save()
    module = make_module(center)
    optional = make_module(center, title="Extra")
    optional.is_required = False
    optional.save()
    done = make_member(center, Role.TUTOR, full_name="Done Tutor")
    for item in module.items.exclude(kind="quiz"):
        progress.mark_done(done, item)
    quiz = module.items.get(kind="quiz")
    right = {k[2:]: v for k, v in answers(quiz, right=2).items()}
    progress.submit_quiz(done, quiz, right)
    day = today(center) - timedelta(days=1)
    entry(center, done, day, (9, 0), (12, 0), Category.TRAINING)
    client.force_login(director)
    page = client.get(u("onboarding", center)).content.decode()
    assert "Done Tutor" in page and "Toni Tutor" in page
    rows = {r.person.full_name: r for r in progress.onboarding_table(center)}
    assert rows["Done Tutor"].required_done() and not rows["Toni Tutor"].required_done()
    assert progress.hours_met(center, rows["Done Tutor"].hours)
    assert not progress.hours_met(center, rows["Toni Tutor"].hours)
    assert "Complete" in page and "In progress" in page


def test_the_hours_requirement_is_saved_by_a_director(client, center, director) -> None:
    client.force_login(director)
    client.post(u("manage", center), {"training_hours_required": "12.5"})
    center.refresh_from_db()
    assert center.training_hours_required == Decimal("12.50")
    assert AuditEvent.objects.filter(action=AuditAction.TRAINING_SETTINGS_CHANGED).count() == 1
    client.post(u("manage", center), {"training_hours_required": ""})
    center.refresh_from_db()
    assert center.training_hours_required is None
