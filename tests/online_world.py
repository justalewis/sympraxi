"""Builders for online-session tests: an appointment that has just begun."""

from sympraxi.scheduling.models import Appointment
from tests.records_world import appointment_for
from tests.scheduling_world import World


def starting_session(world: World, minutes: float = 5, **fields: object) -> Appointment:
    """The world's student and tutor, starting in a few minutes: the room is open."""
    return appointment_for(world, world.student, days=minutes / 1440, **fields)
