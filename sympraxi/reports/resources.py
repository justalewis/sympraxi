"""Which resources tutors attach, and for which concerns, for a term or a range of dates.

It counts final session records held in the period, the same records the statistics
count topics from, so a record for a meeting that wasn't booked belongs only to a
report on every schedule. Nothing here names a student: it counts attachments, and
the tutors behind them are counted, not named.

- Resources attached: each resource that was attached to at least one of those
  records, how many records it was attached to, how many different tutors attached
  it, how many tutors of the center keep it as a favorite, and what the link
  checker last found.
- The center's own links that weren't attached in the period, with how many times
  each was attached before, so a director can see what to retire or promote.
- Concerns and resources: for each concern tagged on those records, how many records
  carry it and how many of those got at least one resource, which shows where tutors
  find something to give and where the catalog falls short.
"""

from django.db.models import Count, Exists, OuterRef
from django.utils.translation import gettext as _

from sympraxi.centers.models import Center, Role
from sympraxi.records.models import (
    AttachedResource,
    Axis,
    RecordTopic,
    Resource,
    ResourceFavorite,
)
from sympraxi.reports import statistics
from sympraxi.reports.forms import Period
from sympraxi.reports.tables import Column, Report, Table


def build(center: Center, period: Period) -> Report:
    records = statistics.final_records(center, period)
    total = records.count()
    with_resources = records.filter(
        Exists(AttachedResource.objects.filter(record=OuterRef("pk")))
    ).count()
    tables = [
        _attached(center, records),
        _unused(center, records),
        _concerns(records),
    ]
    tables[0].note = _(
        "%(total)s final session records were held in this period, and %(with)s of them had "
        "at least one resource attached."
    ) % {"total": total, "with": with_resources}
    return Report(
        kind="resources",
        title=_("Resources"),
        described=period.described(),
        tables=tables,
        meta=period.meta(),
    )


def _source(resource: Resource) -> str:
    return _("Your center") if resource.center_id else _("Shared catalog")


def _attached(center: Center, records) -> Table:
    counts = {
        row["resource"]: row
        for row in AttachedResource.objects.filter(record__in=records)
        .values("resource")
        .annotate(
            records=Count("record", distinct=True), tutors=Count("record__tutor", distinct=True)
        )
    }
    resources = Resource.objects.filter(pk__in=counts)
    favorited = {
        row["resource"]: row["tutors"]
        for row in ResourceFavorite.objects.filter(
            resource__in=resources,
            user__memberships__center=center,
            user__memberships__is_active=True,
            user__memberships__role__in=[Role.TUTOR, Role.DIRECTOR],
        )
        .values("resource")
        .annotate(tutors=Count("user", distinct=True))
    }
    rows = sorted(
        resources,
        key=lambda r: (-counts[r.pk]["records"], r.title.lower()),
    )
    return Table(
        "attached",
        _("Resources attached"),
        [
            Column("resource", _("Resource")),
            Column("publisher", _("Publisher")),
            Column("source", _("From")),
            Column("records", _("Records attached to"), numeric=True),
            Column("tutors", _("Tutors who attached it"), numeric=True),
            Column("favorited_by", _("Tutors who favorited it"), numeric=True),
            Column("link", _("Link")),
        ],
        [
            [
                r.title,
                r.publisher,
                _source(r),
                counts[r.pk]["records"],
                counts[r.pk]["tutors"],
                favorited.get(r.pk, 0),
                str(r.get_link_state_display()),
            ]
            for r in rows
        ],
    )


def _unused(center: Center, records) -> Table:
    used = AttachedResource.objects.filter(record__in=records).values("resource")
    ever = {
        row["resource"]: row["n"]
        for row in AttachedResource.objects.filter(resource__center=center)
        .values("resource")
        .annotate(n=Count("record", distinct=True))
    }
    rows = (
        Resource.objects.filter(center=center, is_active=True)
        .exclude(pk__in=used)
        .order_by("title")
    )
    return Table(
        "unused",
        _("Your center's links not attached in this period"),
        [
            Column("resource", _("Resource")),
            Column("ever", _("Records it was ever attached to"), numeric=True),
            Column("link", _("Link")),
        ],
        [[r.title, ever.get(r.pk, 0), str(r.get_link_state_display())] for r in rows],
    )


def _concerns(records) -> Table:
    has_resource = Exists(AttachedResource.objects.filter(record=OuterRef("record_id")))
    rows = (
        RecordTopic.objects.filter(record__in=records, topic__axis=Axis.CONCERN)
        .values("topic__label", "topic__slug", "topic__position")
        .annotate(
            tagged=Count("record_id"),
            given=Count("record_id", filter=has_resource),
        )
        .order_by("-tagged", "topic__position")
    )
    return Table(
        "concerns",
        _("Concerns and resources"),
        [
            Column("concern", _("Concern")),
            Column("key", _("Topic key")),
            Column("tagged", _("Records tagged"), numeric=True),
            Column("given", _("With a resource attached"), numeric=True),
            Column("share", _("Share, in percent"), numeric=True),
        ],
        [
            [
                r["topic__label"],
                r["topic__slug"],
                r["tagged"],
                r["given"],
                round(100 * r["given"] / r["tagged"]) if r["tagged"] else None,
            ]
            for r in rows
        ],
    )
