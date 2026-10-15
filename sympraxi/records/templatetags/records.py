"""Template helpers for session records."""

from typing import Any

from django import template
from django.utils.translation import gettext_lazy as _

from sympraxi.records.models import Amendment, Topic

register = template.Library()

FIELD_LABELS = {
    "summary": _("Summary for the student"),
    "next_steps": _("Next steps"),
    "staff_notes": _("Notes for staff"),
    "topics": _("Topics"),
    "resources": _("Resources"),
    "minutes": _("Length in minutes"),
    "project": _("Writing project"),
}


@register.filter
def amendment_rows(amendment: Amendment) -> list[dict[str, str]]:
    """Each changed field of an amendment as readable text, before and after."""
    slugs: set[str] = set()
    for name in ("topics",):
        change = amendment.changes.get(name)
        if change:
            slugs.update(change.get("before") or [])
            slugs.update(change.get("after") or [])
    labels = dict(Topic.objects.filter(slug__in=slugs).values_list("slug", "label"))
    rows = []
    for name, change in amendment.changes.items():
        rows.append(
            {
                "label": str(FIELD_LABELS.get(name, name)),
                "before": _text(name, change.get("before"), labels),
                "after": _text(name, change.get("after"), labels),
            }
        )
    return rows


def _text(name: str, value: Any, labels: dict[str, str]) -> str:
    if value in (None, "", []):
        return ""
    if name == "topics":
        return "; ".join(labels.get(slug, slug) for slug in value)
    if name == "project":
        return str(value.get("title", ""))
    if name == "resources":
        return "\n".join(
            f"{r.get('title', '')}: {r['note']}" if r.get("note") else str(r.get("title", ""))
            for r in value
        )
    return str(value)
