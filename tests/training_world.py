"""Builders for training tests."""

from sympraxi.centers.models import Center
from sympraxi.training.models import Choice, Item, ItemKind, Module, Question
from tests.scheduling_world import World


def make_quiz(module: Module, *, questions: int = 2, pass_percent: int = 50) -> Item:
    quiz = Item.objects.create(
        module=module,
        kind=ItemKind.QUIZ,
        title="Check yourself",
        position=3,
        pass_percent=pass_percent,
    )
    for n in range(questions):
        question = Question.objects.create(item=quiz, prompt=f"Question {n + 1}?", position=n)
        Choice.objects.create(question=question, text="Right", is_correct=True, position=0)
        Choice.objects.create(question=question, text="Wrong", is_correct=False, position=1)
    return quiz


def make_module(center: Center, *, published: bool = True, title: str = "Welcome") -> Module:
    module = Module.objects.create(
        center=center, title=title, summary="Start here", is_published=published
    )
    Item.objects.create(
        module=module, kind=ItemKind.PAGE, title="Our values", body="We help writers.", position=1
    )
    Item.objects.create(
        module=module,
        kind=ItemKind.LINK,
        title="A reading",
        url="https://example.org/reading",
        position=2,
    )
    make_quiz(module)
    return module


def add_training(world: World) -> World:
    """Give a world `training_module` (published, three items) and `training_item` (its page)."""
    module = make_module(world.center)
    world.training_module = module  # type: ignore[attr-defined]
    world.training_item = module.items.get(kind=ItemKind.PAGE)  # type: ignore[attr-defined]
    return world


def add_onboarding(world: World) -> World:
    """Give a world a cohort (`cohort`) with the world's tutor mentoring a new tutor (`mentee`),
    a checklist (`checklist`), and a draft observation (`observation`) by the mentor."""
    from datetime import date

    from sympraxi.centers.models import Role
    from sympraxi.training.models import Cohort, CohortMember, Criterion, Observation, Template
    from tests.scheduling_world import make_member

    newbie = make_member(world.center, Role.TUTOR, full_name="New Tutor")
    cohort = Cohort.objects.create(center=world.center, name="Fall tutors", starts_on=date.today())
    world.cohort = cohort  # type: ignore[attr-defined]
    world.mentee = CohortMember.objects.create(  # type: ignore[attr-defined]
        cohort=cohort, person=newbie, mentor=world.tutor
    )
    template = Template.objects.create(center=world.center, title="Session observation")
    for n, text in enumerate(["Opens warmly", "Asks before telling"]):
        Criterion.objects.create(template=template, text=text, position=n)
    world.checklist = template  # type: ignore[attr-defined]
    world.observation = Observation.objects.create(  # type: ignore[attr-defined]
        center=world.center,
        template=template,
        tutor=newbie,
        observer=world.tutor,
        observed_on=date.today(),
    )
    return world
