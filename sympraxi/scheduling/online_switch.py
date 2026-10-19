"""Moving a day's in-person appointments online, as on a snow day.

A director picks a day in the center's time zone, on one schedule or all of
them. Every scheduled in-person appointment that starts that day moves online
at the same time, using its tutor's meeting link on that schedule
(`TutorAssignment.meeting_url`), and its room is cleared. A tutor without a
link can't be moved: the review page lists them, so the director can add a
link to their assignment first or tell them another way. Appointments already
online, canceled ones and those on other days are left alone.

Each moved appointment gets a new calendar SEQUENCE, so calendar apps update
their copy, an audit entry, and an email to its students and tutor saying it is
now online, with the director's note if there is one. Placeholders move too,
without emails. Everything moves in one transaction or not at all.

New bookings for that day can still be in person; this changes only what is
already booked.
"""

from dataclasses import dataclass, field
from datetime import date
from functools import partial
from zoneinfo import ZoneInfo

from django.db import transaction
from django.http import HttpRequest

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center
from sympraxi.online.services import video_on
from sympraxi.privacy.audit import AuditAction, record
from sympraxi.scheduling import notifications
from sympraxi.scheduling.models import Appointment, AppointmentStatus, Modality, Schedule
from sympraxi.scheduling.timeutils import day_bounds


@dataclass
class Plan:
    """What switching a day would do."""

    movable: list[Appointment] = field(default_factory=list)
    # Appointments whose tutor has no meeting link on that schedule.
    stuck: list[Appointment] = field(default_factory=list)

    @property
    def tutors_without_links(self) -> list[tuple[User, Schedule]]:
        seen: dict[tuple[object, object], tuple[User, Schedule]] = {}
        for a in self.stuck:
            seen.setdefault((a.tutor_id, a.schedule_id), (a.tutor, a.schedule))
        return sorted(seen.values(), key=lambda pair: (pair[0].display_name.lower(), pair[1].name))


def in_person_on(center: Center, day: date, schedule: Schedule | None = None) -> list[Appointment]:
    start, end = day_bounds(day, ZoneInfo(center.time_zone))
    found = Appointment.objects.filter(
        schedule__center=center,
        status=AppointmentStatus.SCHEDULED,
        modality=Modality.IN_PERSON,
        starts_at__gte=start,
        starts_at__lt=end,
    ).select_related("assignment", "tutor", "schedule", "room")
    if schedule is not None:
        found = found.filter(schedule=schedule)
    return list(found.order_by("starts_at", "pk"))


def plan(center: Center, day: date, schedule: Schedule | None = None) -> Plan:
    result = Plan()
    has_video = video_on(center)
    for appointment in in_person_on(center, day, schedule):
        if appointment.assignment.meeting_url or has_video:
            result.movable.append(appointment)
        else:
            result.stuck.append(appointment)
    return result


def switch(
    center: Center,
    day: date,
    *,
    schedule: Schedule | None = None,
    note: str = "",
    actor: User,
    request: HttpRequest,
) -> int:
    """Move the day's movable appointments online. Returns how many moved."""
    moved = 0
    has_video = video_on(center)
    base_url = notifications.base_url(request)
    with transaction.atomic():
        ids = [a.pk for a in plan(center, day, schedule).movable]
        locked = (
            Appointment.objects.select_for_update(of=("self",))
            .filter(pk__in=ids, status=AppointmentStatus.SCHEDULED, modality=Modality.IN_PERSON)
            .select_related("assignment")
            .order_by("pk")
        )
        for appointment in locked:
            link = appointment.assignment.meeting_url
            if not link and not has_video:
                continue
            appointment.modality = Modality.ONLINE
            appointment.meeting_url = link
            appointment.room = None
            appointment.sequence += 1
            appointment.save(
                update_fields=["modality", "meeting_url", "room", "sequence", "updated_at"]
            )
            record(
                AuditAction.APPOINTMENT_CHANGED,
                request=request,
                actor=actor,
                center=center,
                target=appointment,
                via="switch_online",
                day=day.isoformat(),
            )
            moved += 1
            if not appointment.is_placeholder:
                transaction.on_commit(
                    partial(
                        notifications.send,
                        notifications.MOVED_ONLINE,
                        appointment.pk,
                        base_url=base_url,
                        note=note,
                    )
                )
    return moved
