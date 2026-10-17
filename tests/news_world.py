"""Builders for announcement and mass email tests."""

from datetime import timedelta

from sympraxi.centers.models import Center
from sympraxi.notify.models import Announcement, Audience, MassEmail, MassEmailRecipient
from tests.scheduling_world import World
from tests.staff_world import today


def announcement(
    center: Center, audience: Audience = Audience.EVERYONE, **fields: object
) -> Announcement:
    day = today(center)
    values = {
        "title": "Extended hours during finals",
        "body": "We're open until 9 p.m.\nSee https://wc.example.edu/hours",
        "audience": audience,
        "starts_on": day - timedelta(days=1),
        "ends_on": day + timedelta(days=6),
        **fields,
    }
    return Announcement.objects.create(center=center, **values)


def add_news(world: World) -> World:
    """Give a world `announcement` (showing now) and `mass_email` (sent to its student)."""
    world.announcement = announcement(world.center)
    world.mass_email = MassEmail.objects.create(
        center=world.center,
        subject="Workshop on thesis statements",
        body="Join us Thursday at 3.",
        audience="Students",
    )
    MassEmailRecipient.objects.create(mass_email=world.mass_email, user=world.student)
    return world
