"""Builders for attendance tests, and the attendance objects page tests need."""

from datetime import timedelta

from sympraxi.accounts.models import User
from sympraxi.centers.models import Role
from sympraxi.scheduling.models import (
    Appointment,
    Attendance,
    Block,
    BlockReason,
    Participant,
)
from tests.records_world import appointment_for
from tests.scheduling_world import World, make_member


def participant(appointment: Appointment, student: User) -> Participant:
    return Participant.objects.get(appointment=appointment, student=student)


def mark_no_show(appointment: Appointment, student: User, **fields: object) -> Participant:
    """Record a confirmed no-show directly, without the service or its email."""
    values: dict[str, object] = {
        "attendance": Attendance.NO_SHOW,
        "attendance_marked_at": appointment.starts_at + timedelta(minutes=20),
    }
    values.update(fields)
    Participant.objects.filter(appointment=appointment, student=student).update(**values)
    return participant(appointment, student)


def add_attendance(world: World) -> World:
    """Give a world (with records, see `add_records`) the attendance objects pages need.

    - The schedule records cancellations within two days of the start as late,
      so the world's appointment tomorrow is inside the window.
    - `unwritten` (two days ago, no record): the world's student is flagged as a
      possible no-show.
    - `blocked_student`: another student with a no-show (`missed`, three days ago)
      and a block on booking (`block`) through the end of the term.
    """
    schedule = world.schedule
    schedule.late_cancel_minutes = 2 * 24 * 60
    schedule.save(update_fields=["late_cancel_minutes"])
    Participant.objects.filter(appointment=world.unwritten, student=world.student).update(
        flagged_at=world.unwritten.starts_at + timedelta(minutes=15)
    )
    world.blocked_student = make_member(world.center, Role.STUDENT, full_name="Bea Blocked")
    world.missed = appointment_for(world, world.blocked_student, days=-3)
    mark_no_show(world.missed, world.blocked_student)
    world.block = Block.objects.create(
        center=world.center,
        student=world.blocked_student,
        reason=BlockReason.NO_SHOWS,
        count=1,
        limit=1,
        term=world.term,
        ends_on=world.term.ends_on,
    )
    return world


def block(student: User, world: World, **fields: object) -> Block:
    values: dict[str, object] = {
        "center": world.center,
        "student": student,
        "reason": BlockReason.NO_SHOWS,
        "count": 3,
        "limit": 3,
        "term": world.term,
        "ends_on": world.term.ends_on,
    }
    values.update(fields)
    return Block.objects.create(**values)
