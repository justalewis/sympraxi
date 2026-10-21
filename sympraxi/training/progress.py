"""Who has done what: items, modules, quizzes, hours, and the onboarding table."""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.http import HttpRequest

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.staff.models import Category, TimeEntry, to_hours
from sympraxi.training.models import Attempt, Completion, Item, ItemKind, Module

# Roles that do training. Directors can see everyone's progress without doing it.
TRAINEE_ROLES = (Role.TUTOR, Role.FRONT_DESK)


def done_item_ids(person: User, module_ids: list[Any] | None = None) -> set[Any]:
    """Ids of items the person has finished: pages and links marked done, quizzes passed."""
    items = Item.objects.all()
    if module_ids is not None:
        items = items.filter(module_id__in=module_ids)
    marked = set(
        Completion.objects.filter(person=person, item__in=items).values_list("item_id", flat=True)
    )
    passed = set(
        Attempt.objects.filter(person=person, item__in=items, passed=True).values_list(
            "item_id", flat=True
        )
    )
    return marked | passed


@dataclass
class ModuleStatus:
    module: Module
    done: int
    total: int

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done >= self.total

    @property
    def started(self) -> bool:
        return self.done > 0


def statuses(person: User, center: Center) -> list[ModuleStatus]:
    modules = list(
        Module.objects.filter(center=center, is_published=True).prefetch_related("items")
    )
    done = done_item_ids(person, [m.pk for m in modules])
    return [
        ModuleStatus(m, sum(1 for i in m.items.all() if i.pk in done), len(m.items.all()))
        for m in modules
    ]


def mark_done(person: User, item: Item, *, request: HttpRequest | None = None) -> bool:
    """Mark a page or link done. False for a quiz (passing it does that) or if already done."""
    if item.kind == ItemKind.QUIZ:
        return False
    _, made = Completion.objects.get_or_create(item=item, person=person)
    if made:
        _note_if_module_done(person, item.module, request)
    return made


def submit_quiz(
    person: User, item: Item, answers: dict[str, str], *, request: HttpRequest | None = None
) -> Attempt:
    """Score a quiz. A question with no answer, or an unknown choice, counts as wrong."""
    questions = list(item.questions.prefetch_related("choices"))
    correct = 0
    chosen: dict[str, str] = {}
    for question in questions:
        picked = answers.get(str(question.pk), "")
        choice = next((c for c in question.choices.all() if str(c.pk) == picked), None)
        if choice is not None:
            chosen[str(question.pk)] = str(choice.pk)
            if choice.is_correct:
                correct += 1
    total = len(questions)
    passed = total > 0 and round(100 * correct / total) >= item.pass_percent
    with transaction.atomic():
        attempt = Attempt.objects.create(
            item=item, person=person, correct=correct, total=total, passed=passed, answers=chosen
        )
        if passed:
            _note_if_module_done(person, item.module, request)
    return attempt


def _note_if_module_done(person: User, module: Module, request: HttpRequest | None) -> None:
    ids = list(module.items.values_list("pk", flat=True))
    if ids and set(ids) <= done_item_ids(person, [module.pk]):
        record(
            AuditAction.TRAINING_MODULE_COMPLETED,
            request=request,
            actor=person,
            center=module.center,
            target=module,
            person=str(person.pk),
        )


def training_hours(center: Center, person_ids: list[Any] | None = None) -> dict[Any, Decimal]:
    """Hours on the time clock's training category, by person. Open or removed entries skip."""
    entries = TimeEntry.objects.filter(
        center=center,
        category=Category.TRAINING,
        removed_at__isnull=True,
        ended_at__isnull=False,
    )
    if person_ids is not None:
        entries = entries.filter(person_id__in=person_ids)
    totals: dict[Any, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for entry in entries.only("person_id", "started_at", "ended_at"):
        totals[entry.person_id] += to_hours(entry.ended_at - entry.started_at)  # type: ignore[operator]
    return dict(totals)


@dataclass
class Row:
    person: User
    modules: list[ModuleStatus] = field(default_factory=list)
    hours: Decimal = Decimal("0.00")

    def required_done(self) -> bool:
        return all(m.complete for m in self.modules if m.module.is_required)


def onboarding_table(center: Center) -> list[Row]:
    """Every active tutor and front desk person, with each published module's status."""
    people = list(
        User.objects.filter(
            memberships__center=center,
            memberships__is_active=True,
            memberships__role__in=TRAINEE_ROLES,
            is_active=True,
        )
        .distinct()
        .order_by("full_name", "email")
    )
    modules = list(
        Module.objects.filter(center=center, is_published=True).prefetch_related("items")
    )
    hours = training_hours(center, [p.pk for p in people])
    completed: dict[Any, set[Any]] = {
        p.pk: done_item_ids(p, [m.pk for m in modules]) for p in people
    }
    rows = []
    for person in people:
        done = completed[person.pk]
        rows.append(
            Row(
                person,
                [
                    ModuleStatus(
                        m, sum(1 for i in m.items.all() if i.pk in done), len(m.items.all())
                    )
                    for m in modules
                ],
                hours.get(person.pk, Decimal("0.00")),
            )
        )
    return rows


def hours_met(center: Center, hours: Decimal) -> bool:
    return center.training_hours_required is None or hours >= center.training_hours_required
