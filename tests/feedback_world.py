"""Builders for queue tests: an open queue, submissions, and a world with two of them."""

import io
from datetime import timedelta

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.feedback import services
from sympraxi.feedback.models import QueueSettings, Submission
from tests.files_world import PDF
from tests.scheduling_world import World


def open_queue(center: Center, **fields: object) -> QueueSettings:
    queue = services.settings_for(center)
    queue.is_open = True
    for name, value in fields.items():
        setattr(queue, name, value)
    queue.save()
    return queue


def send(
    center: Center,
    student: User,
    *,
    title: str = "Essay 2, first draft",
    days: int = 5,
    name: str = "draft.pdf",
    data: bytes = PDF,
) -> Submission:
    """A student's request for feedback, through the service."""
    return services.send(
        student=student,
        center=center,
        title=title,
        course="ENG 101",
        wanted="Is my argument clear?",
        day=services.today(center) + timedelta(days=days),
        upload=(io.BytesIO(data), name, len(data)),
    )


def add_queue(world: World) -> World:
    """Open the center's queue and give the world two submissions by its student.

    - `submission`: waiting for a tutor, with one draft (`submission_file`).
    - `claimed`: claimed by the world's tutor, who has started the feedback
      (`claimed_file` is its draft).
    """
    open_queue(world.center)
    world.submission = send(world.center, world.student)
    world.submission_file = world.submission.files.get()
    world.claimed = send(world.center, world.student, title="Essay 1", name="essay-one.pdf")
    services.claim(world.claimed.pk, world.tutor)
    world.claimed.refresh_from_db()
    world.claimed_file = world.claimed.files.get()
    return world
