"""Builders for session-record tests: a small vocabulary, resources, records and a world."""

from datetime import datetime, timedelta

from django.utils import timezone

from sympraxi.accounts.models import User
from sympraxi.centers.models import Center, Membership, Role
from sympraxi.records import services
from sympraxi.records.models import (
    Axis,
    RecordStatus,
    Resource,
    SessionRecord,
    Topic,
    WritingProject,
)
from sympraxi.scheduling.models import Appointment, Modality, Participant, TutorAssignment
from tests.scheduling_world import World

# slug -> (axis, label). Parents come before their children.
TERMS = {
    "argument": (Axis.CONCERN, "Ideas and argument"),
    "argument:thesis": (Axis.CONCERN, "Thesis, claim or focus"),
    "argument:analysis": (Axis.CONCERN, "Analysis"),
    "citation": (Axis.CONCERN, "Citation style"),
    "citation:apa": (Axis.CONCERN, "APA style"),
    "citation:mla": (Axis.CONCERN, "MLA style"),
    "evidence": (Axis.CONCERN, "Using sources and evidence"),
    "evidence:quoting": (Axis.CONCERN, "Quoting"),
    "drafting": (Axis.STAGE, "Drafting"),
    "revising": (Axis.STAGE, "Revising"),
    "editing": (Axis.STAGE, "Editing and proofreading"),
    "asked-questions": (Axis.ACTION, "Asked questions"),
    "modeled": (Axis.ACTION, "Modeled a revision"),
}


def make_topics() -> dict[str, Topic]:
    """A small vocabulary, unless the real one is already loaded."""
    topics: dict[str, Topic] = {}
    for position, (slug, (axis, label)) in enumerate(TERMS.items()):
        parent = topics.get(slug.split(":", 1)[0]) if ":" in slug else None
        topics[slug], _ = Topic.objects.get_or_create(
            slug=slug,
            defaults={"axis": axis, "label": label, "position": position, "parent": parent},
        )
    return topics


def topics(*slugs: str) -> list[Topic]:
    make_topics()
    found = {t.slug: t for t in Topic.objects.filter(slug__in=slugs)}
    return [found[s] for s in slugs]


_counter = iter(range(1, 1_000_000))


def make_resource(
    *slugs: str,
    center: Center | None = None,
    title: str | None = None,
    **fields: object,
) -> Resource:
    n = next(_counter)
    values: dict[str, object] = {
        "title": title or f"Resource {n}",
        "url": f"https://example.org/resource-{n}",
        "publisher": "Example Press",
        "license": "CC-BY-4.0",
        "summary": f"What resource {n} helps a student do.",
    }
    values.update(fields)
    if center is None:
        values.setdefault("catalog_id", f"example-resource-{n}")
    resource = Resource.objects.create(center=center, **values)
    resource.topics.set(topics(*slugs))
    return resource


def appointment_for(
    world: World,
    student: User | list[User],
    *,
    days: float = -1,
    tutor: User | None = None,
    assignment: TutorAssignment | None = None,
    minutes: int = 30,
    capacity: int = 1,
    **fields: object,
) -> Appointment:
    """An appointment `days` from now (negative: in the past) with one or more students."""
    students = student if isinstance(student, list) else [student]
    start = timezone.now().replace(microsecond=0) + timedelta(days=days)
    tutor = tutor or world.tutor
    values: dict[str, object] = {
        "schedule": world.schedule,
        "assignment": assignment or world.assignment,
        "tutor": tutor,
        "starts_at": start,
        "ends_at": start + timedelta(minutes=minutes),
        "blocked_until": start + timedelta(minutes=minutes),
        "modality": Modality.IN_PERSON,
        "capacity": max(capacity, len(students)),
    }
    values.update(fields)
    appointment = Appointment.objects.create(**values)
    for s in students:
        Participant.objects.create(appointment=appointment, student=s)
    return appointment


def write_record(
    appointment: Appointment,
    student: User,
    *,
    final: bool = True,
    summary: str = "We worked on your thesis.",
    staff_notes: str = "",
    next_steps: str = "Revise the introduction.",
    tags: tuple[str, ...] = ("argument:thesis", "revising"),
    resources: list[tuple[Resource, str]] | None = None,
    project: WritingProject | None = None,
    actor: User | None = None,
    now: datetime | None = None,
) -> SessionRecord:
    """A record for one student in an appointment, through the services."""
    actor = actor or appointment.tutor
    rec = services.start_for_appointment(actor=actor, appointment=appointment, student=student)
    content = services.Content(
        minutes=appointment.minutes,
        summary=summary,
        staff_notes=staff_notes,
        next_steps=next_steps,
        topics=topics(*tags),
        project=project,
        resources=resources or [],
    )
    if final:
        return services.finalize(actor=actor, rec=rec, content=content, now=now, notify=False)
    return services.save_draft(actor=actor, rec=rec, content=content)


def standalone(
    center: Center,
    student: User,
    tutor: User,
    *,
    final: bool = False,
    days: float = -2,
    summary: str = "A meeting in the hallway.",
    appointment: Appointment | None = None,
) -> SessionRecord:
    """A record inserted directly: for a meeting that wasn't booked, unless `appointment`."""
    held = appointment.starts_at if appointment else timezone.now() + timedelta(days=days)
    rec = SessionRecord.objects.create(
        center=center,
        appointment=appointment,
        student=student,
        tutor=tutor,
        held_at=held,
        minutes=20,
        summary=summary,
        created_by=tutor,
        status=RecordStatus.FINAL if final else RecordStatus.DRAFT,
        finalized_at=held if final else None,
        finalized_by=tutor if final else None,
    )
    return rec


def membership(center: Center, student: User) -> Membership:
    return Membership.objects.get(center=center, user=student, role=Role.STUDENT)


def add_records(world: World) -> World:
    """Give a scheduling world the records objects page tests need.

    - `past_appointment`: yesterday, the world's tutor with the world's student.
    - `record`: that appointment's final record, with a resource and staff notes.
    - `draft`: a draft for a meeting that wasn't booked, by the same tutor.
    - `unwritten`: two days ago, the same tutor and student, no record yet.
    - `project`, `resource` (the center's own link), `student_membership`.
    """
    center = world.center
    world.resource = make_resource("argument:thesis", center=center, title="Our thesis handout")
    world.project = WritingProject.objects.create(
        center=center, student=world.student, title="Literature review", course="BIO 210"
    )
    world.past_appointment = appointment_for(world, world.student, days=-1)
    world.record = write_record(
        world.past_appointment,
        world.student,
        summary="You tightened your thesis.",
        staff_notes="Talked fast, seemed rushed.",
        resources=[(world.resource, "Read the second half.")],
        project=world.project,
    )
    content = services.Content.of(world.record)
    content.summary = "You tightened your thesis and moved it to the first paragraph."
    services.amend(actor=world.tutor, rec=world.record, content=content, reason="Added a detail.")
    world.record.refresh_from_db()
    world.draft = standalone(center, world.student, world.tutor)
    world.unwritten = appointment_for(world, world.student, days=-2)
    world.student_membership = membership(center, world.student)
    return world
