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
