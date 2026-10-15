"""Loading the topic vocabulary and resource catalog from catalog/."""

from io import StringIO
from pathlib import Path

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError

from sympraxi.records import catalog
from sympraxi.records.models import Resource, Topic

pytestmark = pytest.mark.django_db

REPO_CATALOG = Path(__file__).resolve().parent.parent / "catalog"


def entries_in_repo() -> list[dict]:
    found: list[dict] = []
    for file in sorted((REPO_CATALOG / "resources").glob("*.yaml")):
        found.extend(yaml.safe_load(file.read_text(encoding="utf-8")))
    return found


def terms_in_repo() -> list[dict]:
    data = yaml.safe_load((REPO_CATALOG / "vocabulary.yaml").read_text(encoding="utf-8"))
    return [t for axis in data["axes"].values() for t in axis["terms"]]


def test_the_real_catalog_loads_with_licenses_and_publishers() -> None:
    out = StringIO()
    call_command("load_catalog", stdout=out)
    entries = entries_in_repo()
    assert Topic.objects.count() == len(terms_in_repo())
    assert Resource.objects.count() == len(entries)
    assert "Catalog loaded." in out.getvalue()

    apa = Topic.objects.get(slug="citation:apa")
    assert apa.axis == "concern" and apa.parent == Topic.objects.get(slug="citation")
    assert Topic.objects.get(slug="revising").parent is None

    for entry in entries:
        resource = Resource.objects.get(catalog_id=entry["id"])
        assert resource.license == entry["license"]
        assert resource.publisher == entry["publisher"]
        assert resource.url == entry["url"]
        assert resource.center is None and resource.is_active
        tagged = {t.slug for t in resource.topics.all()}
        assert tagged == {s for slugs in entry["topics"].values() for s in slugs}

    openstax = next(e for e in entries if e.get("vendor_source"))
    resource = Resource.objects.get(catalog_id=openstax["id"])
    assert resource.vendor_source_url == openstax["vendor_source"]["url"]
    assert resource.vendor_source_license == "CC-BY-4.0"


def test_loading_again_changes_nothing() -> None:
    first = catalog.load()
    before = dict(Resource.objects.values_list("catalog_id", "pk"))
    topics_before = dict(Topic.objects.values_list("slug", "pk"))
    second = catalog.load()
    assert first.resources_created == Resource.objects.count()
    assert second.resources_created == second.resources_updated == 0
    assert second.topics_created == second.topics_updated == 0
    assert second.resources_unchanged == Resource.objects.count()
    assert dict(Resource.objects.values_list("catalog_id", "pk")) == before
    assert dict(Topic.objects.values_list("slug", "pk")) == topics_before


VOCABULARY = {
    "version": 1,
    "axes": {
        "concern": {
            "terms": [
                {"slug": "argument", "label": "Argument"},
                {"slug": "argument:thesis", "label": "Thesis"},
                {"slug": "citation", "label": "Citation"},
            ]
        },
        "stage": {"terms": [{"slug": "drafting", "label": "Drafting"}]},
        "action": {"terms": [{"slug": "modeled", "label": "Modeled"}]},
    },
}


def entry(n: int, **fields) -> dict:
    values = {
        "id": f"test-entry-{n}",
        "title": f"Entry {n}",
        "url": f"https://example.org/{n}",
        "publisher": "Example Press",
        "license": "CC-BY-4.0",
        "summary": f"Summary of entry {n}.",
        "topics": {"concern": ["argument:thesis"], "stage": ["drafting"]},
    }
    values.update(fields)
    return values


def write_catalog(root: Path, entries: list[dict], vocabulary: dict | None = None) -> Path:
    (root / "resources").mkdir(parents=True, exist_ok=True)
    (root / "vocabulary.yaml").write_text(yaml.safe_dump(vocabulary or VOCABULARY), "utf-8")
    (root / "resources" / "test.yaml").write_text(yaml.safe_dump(entries), "utf-8")
    return root


def test_changes_update_in_place_and_removed_entries_are_retired(tmp_path) -> None:
    root = write_catalog(tmp_path, [entry(1), entry(2)])
    catalog.load(root)
    kept = Resource.objects.get(catalog_id="test-entry-1")
    gone = Resource.objects.get(catalog_id="test-entry-2")
    thesis = Topic.objects.get(slug="argument:thesis")

    vocabulary = yaml.safe_load(yaml.safe_dump(VOCABULARY))
    vocabulary["axes"]["concern"]["terms"][1]["label"] = "Thesis or claim"
    vocabulary["axes"]["concern"]["terms"][1]["deprecated"] = True
    vocabulary["axes"]["concern"]["terms"][1]["replaced_by"] = "argument"
    write_catalog(
        root,
        [entry(1, title="Entry one, renamed", topics={"concern": ["citation"]})],
        vocabulary,
    )
    report = catalog.load(root)

    kept.refresh_from_db()
    assert kept.title == "Entry one, renamed"
    assert {t.slug for t in kept.topics.all()} == {"citation"}
    gone.refresh_from_db()
    assert not gone.is_active
    assert report.resources_retired == ["test-entry-2"]
    thesis.refresh_from_db()
    assert thesis.label == "Thesis or claim"
    assert thesis.is_deprecated and thesis.replaced_by == Topic.objects.get(slug="argument")
    # Same rows, so records that point to them keep pointing to them.
    assert Resource.objects.filter(pk=kept.pk).exists()
    assert Topic.objects.get(slug="argument:thesis").pk == thesis.pk


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (entry(3, topics={"concern": ["argument:nope"]}), "unknown concern topic"),
        (entry(3, topics={"stage": ["drafting"]}), "at least one concern"),
        (entry(3, url="http://example.org/3"), "https://"),
        (entry(3, license=""), "missing license"),
        (entry(1), "appears twice"),
    ],
)
def test_a_bad_entry_stops_the_whole_load(tmp_path, bad, message) -> None:
    root = write_catalog(tmp_path, [entry(1), bad])
    with pytest.raises(CommandError, match=message):
        call_command("load_catalog", path=root, stdout=StringIO())
    assert not Resource.objects.exists()
    assert not Topic.objects.exists()


def test_a_topic_removed_from_the_vocabulary_is_reported_not_deleted(tmp_path) -> None:
    root = write_catalog(tmp_path, [entry(1)])
    catalog.load(root)
    vocabulary = yaml.safe_load(yaml.safe_dump(VOCABULARY))
    vocabulary["axes"]["action"]["terms"] = []
    write_catalog(root, [entry(1)], vocabulary)
    report = catalog.load(root)
    assert report.topics_missing == ["modeled"]
    assert Topic.objects.filter(slug="modeled").exists()
