"""Load the topic vocabulary and the shared resource catalog from catalog/.

Loading is idempotent and keyed on what the catalog promises never changes:
topic slugs and resource IDs. Running it again updates labels, summaries,
licenses and topic tags in place, so records that point to a topic or a
resource keep pointing to the same row.

Nothing is deleted. A resource that has left the catalog is marked inactive,
because records may still have it attached. A topic missing from the file is
reported; the vocabulary's rule is that terms are deprecated, never removed.

`catalog/validate.py` is the full check for contributors. This loader repeats
only the checks it needs to load safely, and refuses the whole load if any
fails.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.db import transaction

from sympraxi.records.models import Axis, LinkCheck, Resource, Topic

AXES = {Axis.CONCERN.value, Axis.STAGE.value, Axis.ACTION.value}


class CatalogError(Exception):
    pass


@dataclass
class LoadReport:
    topics_created: int = 0
    topics_updated: int = 0
    topics_unchanged: int = 0
    topics_missing: list[str] = field(default_factory=list)
    resources_created: int = 0
    resources_updated: int = 0
    resources_unchanged: int = 0
    resources_retired: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        lines = [
            f"Topics: {self.topics_created} added, {self.topics_updated} updated, "
            f"{self.topics_unchanged} unchanged.",
            f"Resources: {self.resources_created} added, {self.resources_updated} updated, "
            f"{self.resources_unchanged} unchanged, {len(self.resources_retired)} retired.",
        ]
        if self.topics_missing:
            lines.append(
                "These topics are in the database but not in vocabulary.yaml. Slugs are "
                "permanent, so mark a term deprecated instead of removing it: "
                + ", ".join(self.topics_missing)
            )
        if self.resources_retired:
            lines.append(
                "No longer in the catalog, now inactive: " + ", ".join(self.resources_retired)
            )
        return lines


def default_path() -> Path:
    return Path(settings.BASE_DIR) / "catalog"


def _read(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        raise CatalogError(f"Can't read {path}: {exc}") from exc


@transaction.atomic
def load(path: Path | None = None) -> LoadReport:
    root = path or default_path()
    report = LoadReport()
    topics = _load_vocabulary(_read(root / "vocabulary.yaml"), report)
    files = sorted((root / "resources").glob("*.yaml"))
    if not files:
        raise CatalogError(f"No resource files in {root / 'resources'}")
    entries: list[dict[str, Any]] = []
    for file in files:
        data = _read(file) or []
        if not isinstance(data, list):
            raise CatalogError(f"{file.name} must be a list of entries")
        entries.extend(data)
    _load_resources(entries, topics, report)
    return report


# --- Topics -------------------------------------------------------------------


def _load_vocabulary(data: Any, report: LoadReport) -> dict[str, Topic]:
    if not isinstance(data, dict) or not isinstance(data.get("axes"), dict):
        raise CatalogError("vocabulary.yaml has no axes")
    terms: list[tuple[str, dict[str, Any]]] = []
    for axis, spec in data["axes"].items():
        if axis not in AXES:
            raise CatalogError(f"Unknown axis {axis!r} in vocabulary.yaml")
        for term in spec.get("terms") or []:
            terms.append((axis, term))

    seen: set[str] = set()
    existing = {t.slug: t for t in Topic.objects.all()}
    by_slug: dict[str, Topic] = {}
    for position, (axis, term) in enumerate(terms):
        slug = str(term.get("slug", ""))
        if not slug or not term.get("label"):
            raise CatalogError(f"A {axis} term has no slug or no label")
        if slug in seen:
            raise CatalogError(f"Topic {slug!r} appears twice")
        seen.add(slug)
        values = {
            "axis": axis,
            "label": str(term["label"]),
            "definition": str(term.get("definition") or ""),
            "position": position,
            "is_deprecated": bool(term.get("deprecated", False)),
        }
        topic = existing.get(slug)
        if topic is None:
            topic = Topic.objects.create(slug=slug, **values)
            report.topics_created += 1
        elif topic.axis != axis:
            raise CatalogError(f"Topic {slug!r} moved from {topic.axis} to {axis}")
        elif _changed(topic, values):
            _assign(topic, values)
            topic.save()
            report.topics_updated += 1
        else:
            report.topics_unchanged += 1
        by_slug[slug] = topic

    # Second pass: parents and replacements refer to other terms.
    for axis, term in terms:
        slug = str(term["slug"])
        topic = by_slug[slug]
        parent = None
        if axis == Axis.CONCERN and ":" in slug:
            parent_slug = slug.split(":", 1)[0]
            parent = by_slug.get(parent_slug)
            if parent is None:
                raise CatalogError(f"Topic {slug!r} has no parent term {parent_slug!r}")
        replaced_by = None
        if term.get("replaced_by"):
            replaced_by = by_slug.get(str(term["replaced_by"]))
            if replaced_by is None:
                raise CatalogError(f"Topic {slug!r} is replaced by an unknown term")
        if topic.parent_id != (parent.pk if parent else None) or topic.replaced_by_id != (
            replaced_by.pk if replaced_by else None
        ):
            topic.parent = parent
            topic.replaced_by = replaced_by
            topic.save(update_fields=["parent", "replaced_by"])

    report.topics_missing = sorted(set(existing) - seen)
    return by_slug


# --- Resources ----------------------------------------------------------------

REQUIRED = ("id", "title", "url", "publisher", "license", "summary", "topics")


def _load_resources(
    entries: Iterable[dict[str, Any]], topics: dict[str, Topic], report: LoadReport
) -> None:
    existing = {r.catalog_id: r for r in Resource.objects.filter(catalog_id__isnull=False)}
    seen: set[str] = set()
    for entry in entries:
        missing = [k for k in REQUIRED if not entry.get(k)]
        if missing:
            raise CatalogError(f"Entry {entry.get('id', '?')!r} is missing {', '.join(missing)}")
        catalog_id = str(entry["id"])
        if catalog_id in seen:
            raise CatalogError(f"Resource {catalog_id!r} appears twice")
        seen.add(catalog_id)
        url = str(entry["url"])
        if not url.startswith("https://"):
            raise CatalogError(f"Resource {catalog_id!r} must link with https://")
        tagged = _topics_for(catalog_id, entry["topics"], topics)
        vendor = entry.get("vendor_source") or {}
        link_check = str(entry.get("link_check") or LinkCheck.AUTO)
        if link_check not in LinkCheck.values:
            raise CatalogError(f"Resource {catalog_id!r} has an unknown link_check")
        values: dict[str, Any] = {
            "title": str(entry["title"]),
            "url": url,
            "publisher": str(entry["publisher"]),
            "license": str(entry["license"]),
            "license_note": str(entry.get("license_note") or ""),
            "can_vendor": bool(entry.get("can_vendor", False)),
            "vendor_source_url": str(vendor.get("url") or ""),
            "vendor_source_license": str(vendor.get("license") or ""),
            "link_check": link_check,
            "summary": " ".join(str(entry["summary"]).split()),
            "audience": [str(a) for a in entry.get("audience") or []],
            "language": str(entry.get("language") or "en"),
            "last_checked": _date(entry.get("last_checked")),
            "last_status": entry.get("last_status"),
            "is_active": True,
        }
        resource = existing.get(catalog_id)
        if resource is None:
            resource = Resource.objects.create(catalog_id=catalog_id, **values)
            resource.topics.set(tagged)
            report.resources_created += 1
            continue
        current = set(resource.topics.values_list("pk", flat=True))
        if _changed(resource, values) or current != {t.pk for t in tagged}:
            _assign(resource, values)
            resource.save()
            resource.topics.set(tagged)
            report.resources_updated += 1
        else:
            report.resources_unchanged += 1

    for key, resource in sorted(existing.items(), key=lambda item: str(item[0])):
        if key not in seen and resource.is_active:
            resource.is_active = False
            resource.save(update_fields=["is_active", "updated_at"])
            report.resources_retired.append(str(key))


def _topics_for(catalog_id: str, spec: Any, topics: dict[str, Topic]) -> list[Topic]:
    if not isinstance(spec, dict) or not spec.get("concern"):
        raise CatalogError(f"Resource {catalog_id!r} needs at least one concern topic")
    tagged: list[Topic] = []
    for axis, slugs in spec.items():
        if axis not in AXES:
            raise CatalogError(f"Resource {catalog_id!r} uses unknown axis {axis!r}")
        for slug in slugs or []:
            topic = topics.get(str(slug))
            if topic is None or topic.axis != axis:
                raise CatalogError(f"Resource {catalog_id!r} uses unknown {axis} topic {slug!r}")
            tagged.append(topic)
    return tagged


def _date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CatalogError(f"Not a date: {value!r}") from exc


def _changed(obj: Any, values: dict[str, Any]) -> bool:
    return any(getattr(obj, k) != v for k, v in values.items())


def _assign(obj: Any, values: dict[str, Any]) -> None:
    for k, v in values.items():
        setattr(obj, k, v)
