"""A full, believable demo center: a cast of people and ten weeks of activity.

The functions import their modules when called, so the plain-data modules here (the cast,
the tour, the video scripts) can be read by tools that don't start Django.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sympraxi.centers.models import Center
    from sympraxi.core.demo_world.history import World


def build(center: "Center") -> "World":
    from sympraxi.core.demo_world.history import build as _build

    return _build(center)


def add_extras(world: "World") -> None:
    from sympraxi.core.demo_world.extras import add_extras as _add

    _add(world)


__all__ = ["add_extras", "build"]
