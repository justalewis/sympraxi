"""Builders for report tests: a term with appointments whose counts are known.

`counted_center()` builds a fall 2025 term at a center in Chicago and fills it
with one of each outcome and kind of appointment. The numbers its reports must
give are written out in the docstring, so the tests compare against hand
counts rather than against the report code.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from django.core.files.base import ContentFile
from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Role
from sympraxi.records.models import RecordStatus, RecordTopic, SessionRecord
from sympraxi.reports import center_export
from sympraxi.reports.models import CenterExport, ExportStatus
from sympraxi.scheduling.models import (
    Appointment,
    AppointmentStatus,
    Attendance,
    Modality,
    Participant,
    Schedule,
    Term,
    TutorAssignment,
)
from tests.factories import CenterFactory
from tests.forms_world import director_of
from tests.records_world import topics, write_record
from tests.scheduling_world import World, assign, at, make_member, make_schedule


def book(
    schedule: Schedule,
    assignment: TutorAssignment,
    start: datetime,
    students: list[User],
    *,
    minutes: int = 30,
    modality: str = Modality.IN_PERSON,
    **fields: object,
) -> Appointment:
    """An appointment written straight to the database, without the booking rules."""
    values: dict[str, object] = {
        "schedule": schedule,
        "assignment": assignment,
        "tutor": assignment.tutor,
        "starts_at": start,
        "ends_at": start + timedelta(minutes=minutes),
        "blocked_until": start + timedelta(minutes=minutes),
        "modality": modality,
        "capacity": max(1, len(students)),
    }
    values.update(fields)
    appointment = Appointment.objects.create(**values)
    for student in students:
        Participant.objects.create(appointment=appointment, student=student)
    return appointment


def came(appointment: Appointment, student: User) -> None:
    Participant.objects.filter(appointment=appointment, student=student).update(
        attendance=Attendance.ATTENDED, attendance_marked_at=appointment.starts_at
    )


def no_show(appointment: Appointment, student: User) -> None:
    Participant.objects.filter(appointment=appointment, student=student).update(
        attendance=Attendance.NO_SHOW, attendance_marked_at=appointment.starts_at
    )


def cancel(appointment: Appointment, *, late: bool = False) -> None:
    when = appointment.starts_at - timedelta(hours=1)
    Appointment.objects.filter(pk=appointment.pk).update(
        status=AppointmentStatus.CANCELED, canceled_at=when
    )
    Participant.objects.filter(appointment=appointment).update(canceled_at=when, late_cancel=late)


@dataclass
class Counted:
    """The counted center. See `counted_center` for what each name is."""

    center: Center
    term: Term
    in_person: Schedule
    online: Schedule
    ana: User
    ben: User
    s1: User
    s2: User
    s3: User
    s4: User
    appointments: dict[str, Appointment] = field(default_factory=dict)


FALL = (date(2025, 9, 1), date(2025, 12, 15))


def counted_center() -> Counted:
    """Fall 2025 (September 1 to December 15) at a center in Chicago.

    Tutors: Ana on "In person"; Ben on "In person" and "Online". Students s1 to s4.
    Before the term, s1 came once (on a summer schedule), so none of s1's fall
    visits is a first visit. After the term, s3 came once more (not counted).

    In the term:

    | name        | when     | schedule  | tutor | kind    | students and outcome        |
    |-------------|----------|-----------|-------|---------|-----------------------------|
    | a1          | Sep 8    | in person | Ana   | single  | s1 came (a return)          |
    | a2          | Sep 9    | in person | Ana   | single  | s2 came (s2's first visit)  |
    | a3          | Sep 10   | in person | Ben   | single  | s2 no-show                  |
    | a4          | Sep 11   | in person | Ben   | single  | s3 late cancellation        |
    | a5          | Sep 12   | online    | Ben   | single  | s4 canceled, not late       |
    | a6          | Sep 15   | in person | Ana   | group   | s3 came (first), s4 unmarked|
    | a7          | Sep 16   | in person | Ana   | walk-in | s1 came                     |
    | a8          | Sep 17   | in person | Ben   | placeholder, no student     |
    | a9          | Sep 18   | in person | Ana   | series  | s2 came                     |

    So: 9 visits booked, 5 came, 1 late cancellation, 1 no-show, 1 canceled, 1 not
    marked. Ana 6 booked (5 came, 1 not marked); Ben 3 (1 no-show, 1 late, 1
    canceled). In person 8, online 1. Walk-in 1, group 2, series 1, single 5.
    8 appointments booked, 2 of them canceled, 1 placeholder. 4 students booked,
    3 came. 2 first visits (s2 at a2, s3 at a6), 3 return visits.

    Final records: a1 (s1, thesis + revising) and a2 (s2, thesis), and one for a
    meeting that wasn't booked (s4, APA). A draft for a7 doesn't count.
    """
    center = CenterFactory(time_zone="America/Chicago")
    term = Term.objects.create(center=center, name="Fall 2025", starts_on=FALL[0], ends_on=FALL[1])
    summer = Term.objects.create(
        center=center, name="Summer 2025", starts_on=date(2025, 6, 1), ends_on=date(2025, 8, 20)
    )
    spring = Term.objects.create(
        center=center, name="Spring 2026", starts_on=date(2026, 1, 5), ends_on=date(2026, 5, 1)
    )
    in_person = make_schedule(center, FALL[0], FALL[1], name="In person")
    online = make_schedule(center, FALL[0], FALL[1], name="Online")
    summer_schedule = make_schedule(center, summer.starts_on, summer.ends_on, name="Summer")
    spring_schedule = make_schedule(center, spring.starts_on, spring.ends_on, name="Spring")
    ana = make_member(center, Role.TUTOR, full_name="Ana Tutor")
    ben = make_member(center, Role.TUTOR, full_name="Ben Tutor")
    students = [make_member(center, Role.STUDENT, full_name=f"Student {n}") for n in range(1, 5)]
    s1, s2, s3, s4 = students
    ana_ip = assign(in_person, ana)
    ben_ip = assign(in_person, ben)
    ben_online = assign(online, ben, offers_in_person=False, offers_online=True)
    ana_summer = assign(summer_schedule, ana)
    ana_spring = assign(spring_schedule, ana)

    def on(schedule: Schedule, day: date, hour: int = 10) -> datetime:
        return at(schedule, day, hour)

    world = Counted(center, term, in_person, online, ana, ben, s1, s2, s3, s4)
    a = world.appointments
    a["summer"] = book(summer_schedule, ana_summer, on(summer_schedule, date(2025, 7, 1)), [s1])
    came(a["summer"], s1)
    a["a1"] = book(in_person, ana_ip, on(in_person, date(2025, 9, 8)), [s1])
    came(a["a1"], s1)
    a["a2"] = book(in_person, ana_ip, on(in_person, date(2025, 9, 9)), [s2])
    came(a["a2"], s2)
    a["a3"] = book(in_person, ben_ip, on(in_person, date(2025, 9, 10)), [s2])
    no_show(a["a3"], s2)
    a["a4"] = book(in_person, ben_ip, on(in_person, date(2025, 9, 11)), [s3])
    cancel(a["a4"], late=True)
    a["a5"] = book(
        online, ben_online, on(online, date(2025, 9, 12)), [s4], modality=Modality.ONLINE
    )
    cancel(a["a5"])
    a["a6"] = book(in_person, ana_ip, on(in_person, date(2025, 9, 15)), [s3, s4], capacity=3)
    came(a["a6"], s3)
    a["a7"] = book(in_person, ana_ip, on(in_person, date(2025, 9, 16)), [s1], is_walk_in=True)
    came(a["a7"], s1)
    a["a8"] = book(in_person, ben_ip, on(in_person, date(2025, 9, 17)), [], is_placeholder=True)
    a["a9"] = book(
        in_person, ana_ip, on(in_person, date(2025, 9, 18)), [s2], series_id=uuid.uuid4()
    )
    came(a["a9"], s2)
    a["spring"] = book(spring_schedule, ana_spring, on(spring_schedule, date(2026, 1, 10)), [s3])
    came(a["spring"], s3)

    write_record(a["a1"], s1, tags=("argument:thesis", "revising"))
    write_record(a["a2"], s2, tags=("argument:thesis",))
    write_record(a["a7"], s1, final=False, tags=("citation:mla",))
    # A meeting that wasn't booked: tagged while a draft, then finalized.
    held = at(in_person, date(2025, 10, 1), 15)
    off = SessionRecord.objects.create(
        center=center, student=s4, tutor=ana, held_at=held, minutes=20, created_by=ana
    )
    (apa,) = topics("citation:apa")
    RecordTopic.objects.create(record=off, topic=apa)
    SessionRecord.objects.filter(pk=off.pk).update(
        status=RecordStatus.FINAL, finalized_at=held, finalized_by=ana
    )
    return world


def ready_export(center: Center, *, by: User | None = None, hours: int = 24) -> CenterExport:
    """A ready export with a small file in storage, without building a real one."""
    export = CenterExport.objects.create(center=center, requested_by=by or director_of(center))
    name = center_export.storage().save(
        center_export.file_name(export), ContentFile(b"PK\x05\x06" + b"\0" * 18)
    )
    now = timezone.now()
    CenterExport.objects.filter(pk=export.pk).update(
        status=ExportStatus.READY,
        file_name=name,
        size=22,
        ready_at=now,
        expires_at=now + timedelta(hours=hours),
    )
    export.refresh_from_db()
    return export


def add_reports(world: World) -> World:
    """Give a world the report objects pages need: `center_export`, ready to download."""
    world.center_export = ready_export(world.center)
    return world
