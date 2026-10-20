"""The open-content pack: openly licensed writing guidance kept inside Sympraxi.

Most resources are links. This is the exception: full text of openly licensed material,
loaded from `content-pack/` in the repository, so a tutor can attach a handout that lives on
the center's own server and the student reads it without leaving. Each item is a `Resource`
(so attaching, suggestions, favorites and the reports work as for any link) with a
`ContentItem` behind it that holds the text.

Two tiers, in two folders, so the line between them is plain:

- `content-pack/cc-by/` holds material under CC BY or CC0, which may be copied and adapted
  with credit. Every installation loads it.
- `content-pack/noncommercial/` holds material with a NonCommercial term, and so with the
  NoDerivatives or ShareAlike terms that usually come with it. It is loaded only when an
  installation asks (`manage.py load_content_pack --include-noncommercial`), is left out of
  the Docker image unless the build asks for it, and carries its own notice. A college running
  its own copy is fine; a paid host reselling Sympraxi should not load it. An item with a
  NoDerivatives term must be marked `whole: true` and `changes: none`: it is shown exactly
  as published, never shortened or reworded.

Every item must name its author, its source address, its license and the license's address,
and say what was changed (or "none"); the loader refuses the whole pack if any is missing.
The page for an item shows that credit above the text. Nothing about the item's text is
trusted: `clean()` rebuilds the HTML from a short list of elements, drops everything else,
and refuses links that aren't http, https or a page anchor, so a pack can't smuggle script
into a page. Reloading is safe and keyed on each item's permanent `id`; an item that leaves
the pack is marked inactive, not deleted, because records may have it attached.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from django.conf import settings
from django.db import transaction

from sympraxi.records.catalog import CatalogError, _topics_for
from sympraxi.records.models import ContentItem, ContentTier, LinkCheck, Resource, Topic

CC_BY = frozenset({"CC-BY-4.0", "CC-BY-3.0", "CC0-1.0"})
NONCOMMERCIAL = frozenset(
    {
        "CC-BY-NC-4.0",
        "CC-BY-NC-3.0",
        "CC-BY-NC-SA-4.0",
        "CC-BY-NC-SA-3.0",
        "CC-BY-NC-ND-4.0",
        "CC-BY-NC-ND-3.0",
    }
)
TIERS = {ContentTier.CC_BY: CC_BY, ContentTier.NONCOMMERCIAL: NONCOMMERCIAL}
ITEM_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,98}$")
MAX_BODY = 400_000
REQUIRED = (
    "id",
    "title",
    "author",
    "source_url",
    "license",
    "license_url",
    "summary",
    "topics",
    "changes",
    "body",
)


class PackError(CatalogError):
    pass


# --- Cleaning the text ----------------------------------------------------------------------

ALLOWED = {
    "p", "h2", "h3", "h4", "ul", "ol", "li", "em", "strong", "blockquote", "br", "hr", "a",
    "table", "thead", "tbody", "tr", "th", "td", "code", "pre", "sup", "sub",
}  # fmt: skip
RENAMED = {"b": "strong", "i": "em", "h1": "h2", "h5": "h4", "h6": "h4"}
VOID = {"br", "hr"}
# Elements whose contents are dropped with them.
DROPPED = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "svg",
    "math",
    "form",
    "head",
    "title",
    "template",
    "noscript",
}
CELL_ATTRS = {
    "scope": re.compile(r"^(row|col|rowgroup|colgroup)$"),
    "colspan": re.compile(r"^\d{1,2}$"),
    "rowspan": re.compile(r"^\d{1,2}$"),
}


def _safe_href(value: str) -> str:
    value = value.strip()
    if value.startswith("#") and re.fullmatch(r"#[\w.:-]{1,100}", value):
        return value
    parts = urlsplit(value)
    if parts.scheme in ("http", "https") and parts.netloc:
        return value
    return ""


class _Cleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROPPED:
            self.skipping += 1
            return
        if self.skipping:
            return
        tag = RENAMED.get(tag, tag)
        if tag not in ALLOWED:
            return  # unwrap: keep the text, lose the element
        attributes = ""
        if tag == "a":
            href = _safe_href(dict(attrs).get("href") or "")
            if not href:
                return
            attributes = f' href="{escape(href, quote=True)}" rel="noopener noreferrer"'
        elif tag in ("th", "td"):
            for name, value in attrs:
                pattern = CELL_ATTRS.get(name)
                if pattern and value and pattern.match(value):
                    attributes += f' {name}="{escape(value, quote=True)}"'
        self.out.append(f"<{tag}{attributes}>")
        if tag not in VOID:
            self.open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag in VOID or tag in DROPPED:
            if tag in DROPPED and self.skipping:
                self.skipping -= 1
            return
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in DROPPED:
            self.skipping = max(0, self.skipping - 1)
            return
        if self.skipping:
            return
        tag = RENAMED.get(tag, tag)
        if tag in self.open:
            while self.open:
                closing = self.open.pop()
                self.out.append(f"</{closing}>")
                if closing == tag:
                    break

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.out.append(escape(data, quote=False))

    def close(self) -> None:
        super().close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")


def clean(html: str) -> str:
    """The HTML rebuilt from the allowed elements only, with every text escaped."""
    cleaner = _Cleaner()
    cleaner.feed(html)
    cleaner.close()
    return "".join(cleaner.out).strip()


# --- Loading --------------------------------------------------------------------------------


@dataclass
class Report:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: list[str] = field(default_factory=list)
    skipped_tiers: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        lines = [
            f"Content pack: {self.created} added, {self.updated} updated, "
            f"{self.unchanged} unchanged, {len(self.retired)} retired."
        ]
        if self.skipped_tiers:
            lines.append(
                "Not loaded (needs --include-noncommercial): " + ", ".join(self.skipped_tiers)
            )
        return lines


def default_path() -> Path:
    return Path(settings.BASE_DIR) / "content-pack"


def _read(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise PackError(f"Can't read {path}: {error}") from error
    if not isinstance(data, dict):
        raise PackError(f"{path} must hold a mapping")
    return data


def _check(tier: str, item: dict[str, Any], where: str) -> None:
    missing = [k for k in REQUIRED if not item.get(k)]
    if missing:
        raise PackError(f"{where} is missing {', '.join(missing)}")
    if not ITEM_ID.match(str(item["id"])):
        raise PackError(f"{where}: ids are lowercase letters, digits and hyphens")
    license_code = str(item["license"])
    if license_code not in TIERS[ContentTier(tier)]:
        raise PackError(f"{where}: {license_code} doesn't belong in the {tier} folder")
    for name in ("source_url", "license_url"):
        if not str(item[name]).startswith("https://"):
            raise PackError(f"{where}: {name} must start with https://")
    if "-ND-" in license_code and not (
        item.get("whole") is True and str(item["changes"]) == "none"
    ):
        raise PackError(f"{where}: a NoDerivatives item must say whole: true and changes: none")
    if len(str(item["body"])) > MAX_BODY:
        raise PackError(f"{where}: the text is too long")


def _items(
    root: Path, include_noncommercial: bool, report: Report
) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for tier in ContentTier.values:
        folder = root / tier
        if not folder.is_dir():
            continue
        if tier == ContentTier.NONCOMMERCIAL and not include_noncommercial:
            report.skipped_tiers.append(tier)
            continue
        for pack_file in sorted(folder.glob("*/pack.yaml")):
            pack = _read(pack_file)
            for item_file in sorted((pack_file.parent / "items").glob("*.yaml")):
                item = {**{k: v for k, v in pack.items() if k != "id"}, **_read(item_file)}
                item["pack"] = str(pack.get("id") or pack_file.parent.name)
                _check(tier, item, str(item_file))
                found.append((tier, item))
    return found


def load(path: Path | None = None, *, include_noncommercial: bool = False) -> Report:
    root = path or default_path()
    report = Report()
    if not root.is_dir():
        return report
    items = _items(root, include_noncommercial, report)
    seen_ids = [str(i["id"]) for _tier, i in items]
    if len(seen_ids) != len(set(seen_ids)):
        raise PackError("An item id appears twice")
    topics = {t.slug: t for t in Topic.objects.all()}
    with transaction.atomic():
        existing = {c.pk: c for c in ContentItem.objects.select_related("resource")}
        for tier, item in items:
            _save(tier, item, topics, existing.get(str(item["id"])), report)
        wanted = set(seen_ids)
        for item_id, content in existing.items():
            skipped = content.tier == ContentTier.NONCOMMERCIAL and not include_noncommercial
            if item_id not in wanted and content.is_active and not skipped:
                content.is_active = False
                content.save(update_fields=["is_active"])
                content.resource.is_active = False
                content.resource.save(update_fields=["is_active", "updated_at"])
                report.retired.append(item_id)
    return report


def _save(
    tier: str,
    item: dict[str, Any],
    topics: dict[str, Topic],
    current: ContentItem | None,
    report: Report,
) -> None:
    item_id = str(item["id"])
    body = clean(str(item["body"]))
    if not body:
        raise PackError(f"{item_id}: the text is empty after cleaning")
    try:
        tagged = _topics_for(item_id, item["topics"], topics)
    except CatalogError as error:
        raise PackError(str(error)) from error
    fields: dict[str, Any] = {
        "pack": str(item["pack"]),
        "tier": tier,
        "title": str(item["title"]),
        "author": str(item["author"]),
        "source_title": str(item.get("source_title") or ""),
        "source_url": str(item["source_url"]),
        "license": str(item["license"]),
        "license_url": str(item["license_url"]),
        "changes": str(item["changes"]),
        "whole": bool(item.get("whole", False)),
        "body": body,
        "retrieved_on": _date(item.get("retrieved")),
        "is_active": True,
    }
    summary = " ".join(str(item["summary"]).split())
    resource_values: dict[str, Any] = {
        "title": fields["title"],
        "url": f"/library/{item_id}/",
        "publisher": fields["source_title"] or fields["author"],
        "license": fields["license"],
        "license_note": f"By {fields['author']}. {fields['changes']}"[:2000],
        "can_vendor": False,
        "link_check": LinkCheck.OFF,
        "summary": summary,
        "language": str(item.get("language") or "en"),
        "is_active": True,
    }
    if current is None:
        resource = Resource.objects.create(catalog_id=f"pack:{item_id}", **resource_values)
        resource.topics.set(tagged)
        ContentItem.objects.create(id=item_id, resource=resource, **fields)
        report.created += 1
        return
    resource = current.resource
    same_topics = set(resource.topics.values_list("pk", flat=True)) == {t.pk for t in tagged}
    changed = any(getattr(current, k) != v for k, v in fields.items()) or any(
        getattr(resource, k) != v for k, v in resource_values.items()
    )
    if changed or not same_topics:
        for key, value in fields.items():
            setattr(current, key, value)
        current.save()
        for key, value in resource_values.items():
            setattr(resource, key, value)
        resource.save()
        resource.topics.set(tagged)
        report.updated += 1
    else:
        report.unchanged += 1


def _date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise PackError(f"Not a date: {value!r}") from error
