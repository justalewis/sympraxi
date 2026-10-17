"""Builders for record-of-disclosures tests."""

from datetime import timedelta

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.privacy.models import Disclosure, DisclosureBasis
from tests.scheduling_world import World
from tests.staff_world import today


def disclosure(center: Center, student: User, **fields: object) -> Disclosure:
    day = today(center) - timedelta(days=3)
    values = {
        "disclosed": True,
        "requested_on": day,
        "disclosed_on": day,
        "party": "Registrar, State University",
        "interest": "The student is transferring and asked us to send their records.",
        "basis": DisclosureBasis.OTHER_SCHOOL,
        "records": "Session records from fall 2026",
        **fields,
    }
    return Disclosure.objects.create(center=center, student=student, **values)


def add_disclosures(world: World) -> World:
    """Give the world's student one entry in their record of disclosures."""
    world.disclosure = disclosure(world.center, world.student)
    return world
