"""Builders for workshop tests."""

from datetime import timedelta

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.workshops.models import Workshop, WorkshopStatus
from tests.scheduling_world import World


def make_workshop(
    center: Center,
    leader: User | None = None,
    *,
    starts_in: timedelta = timedelta(days=3),
    status: str = WorkshopStatus.PUBLISHED,
    **fields: object,
) -> Workshop:
    starts = timezone.now() + starts_in
    values: dict[str, object] = {
        "title": "Thesis statements",
        "description": "Writing a claim a reader can argue with.",
        "location": "Library 214",
        "capacity": 12,
    }
    values.update(fields)
    return Workshop.objects.create(
        center=center,
        leader=leader,
        starts_at=starts,
        ends_at=starts + timedelta(hours=1),
        status=status,
        **values,
    )


def add_workshops(world: World) -> World:
    """Give a world `workshop`: a published workshop the tutor leads, three days out."""
    world.workshop = make_workshop(world.center, world.tutor)  # type: ignore[attr-defined]
    return world
