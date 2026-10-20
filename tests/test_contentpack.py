"""The open-content pack: cleaning the text, loading the folders, and the item page."""

from io import StringIO
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from sympraxi.records import catalog, contentpack, suggestions
from sympraxi.records.models import ContentItem, LinkCheck, Resource
from tests.factories import CenterFactory, UserFactory
from tests.records_world import make_topics, topics

pytestmark = pytest.mark.django_db


# --- Cleaning the text ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dirty", "clean"),
    [
        (
            "<p>Hello <b>there</b> and <i>you</i>.</p>",
            "<p>Hello <strong>there</strong> and <em>you</em>.</p>",
        ),
        ("<p>a</p><script>alert(1)</script><p>b</p>", "<p>a</p><p>b</p>"),
        ("<style>p{color:red}</style><p>x</p>", "<p>x</p>"),
        ("<iframe src='https://evil.example'>inside</iframe><p>x</p>", "<p>x</p>"),
        ('<svg><script>1</script><a href="https://x.example">t</a></svg><p>x</p>', "<p>x</p>"),
        ('<p onclick="steal()" style="x" class="y" id="z">x</p>', "<p>x</p>"),
        ('<img src="https://x.example/a.png" alt="a"><p>x</p>', "<p>x</p>"),
        ("<h1>Title</h1><h5>Deep</h5>", "<h2>Title</h2><h4>Deep</h4>"),
        ("<div><span>kept</span> text</div>", "kept text"),
        ("<p>1 &lt; 2 &amp; &lt;script&gt;</p>", "<p>1 &lt; 2 &amp; &lt;script&gt;</p>"),
        ("<p>open <em>never closed", "<p>open <em>never closed</em></p>"),
        ("</em><p>stray</p>", "<p>stray</p>"),
        ("<!-- hidden --><p>x</p>", "<p>x</p>"),
        (
            "<table><thead><tr><th scope='col' onclick='x'>A</th></tr></thead>"
            "<tbody><tr><td colspan='2' rowspan='x1'>B</td></tr></tbody></table>",
            '<table><thead><tr><th scope="col">A</th></tr></thead>'
            '<tbody><tr><td colspan="2">B</td></tr></tbody></table>',
        ),
    ],
)
def test_text_is_rebuilt_from_a_short_list_of_elements(dirty, clean) -> None:
    assert contentpack.clean(dirty) == clean


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "data:text/html,<script>1</script>",
        "//evil.example",
        "/local",
        "ftp://x.example",
    ],
)
def test_only_web_links_and_page_anchors_survive(href) -> None:
    assert contentpack.clean(f'<a href="{href}">text</a>') == "text"


def test_web_links_stay_and_are_marked_not_to_pass_on_the_page() -> None:
    out = contentpack.clean('<a href="https://x.example/a?b=1&amp;c=2" target="_blank">text</a>')
    assert out == '<a href="https://x.example/a?b=1&amp;c=2" rel="noopener noreferrer">text</a>'
    assert (
        contentpack.clean('<a href="#section-2">jump</a>')
        == '<a href="#section-2" rel="noopener noreferrer">jump</a>'
    )


# --- Loading ----------------------------------------


def write_pack(
    root: Path, tier: str, pack: str, items: list[dict[str, Any]], **shared: Any
) -> None:
    folder = root / tier / pack
    (folder / "items").mkdir(parents=True)
    defaults = {
        "id": pack,
        "source_title": "A Guide to Writing",
        "author": "Pat Author",
        "license": "CC-BY-4.0" if tier == "cc-by" else "CC-BY-NC-ND-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
    }
    (folder / "pack.yaml").write_text(yaml.safe_dump({**defaults, **shared}), encoding="utf-8")
    for item in items:
        (folder / "items" / f"{item['id']}.yaml").write_text(yaml.safe_dump(item), encoding="utf-8")


def item(item_id: str = "thesis-basics", **fields: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "title": "Thesis basics",
        "source_url": "https://example.org/guide/thesis",
        "summary": "How to state a claim a reader can argue with.",
        "topics": {"concern": ["argument:thesis"]},
        "changes": "none",
        "body": "<p>A thesis is a claim.</p>",
        **fields,
    }


@pytest.fixture
def vocab():
    return make_topics()


def test_an_item_becomes_a_resource_tutors_can_attach(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item()])
    report = contentpack.load(tmp_path)
    assert (report.created, report.updated) == (1, 0)
    content = ContentItem.objects.get()
    resource = content.resource
    assert (content.id, content.pack, content.tier) == ("thesis-basics", "guide", "cc-by")
    assert (content.author, content.license) == ("Pat Author", "CC-BY-4.0")
    assert resource.catalog_id == "pack:thesis-basics" and resource.center is None
    assert resource.url == "/library/thesis-basics/" and resource.link_check == LinkCheck.OFF
    assert resource.publisher == "A Guide to Writing" and resource.license == "CC-BY-4.0"
    assert list(resource.topics.values_list("slug", flat=True)) == ["argument:thesis"]
    found = suggestions.suggest(CenterFactory(), topics("argument:thesis"))
    assert [s.resource for s in found] == [resource]


def test_loading_again_changes_nothing_and_edits_update_in_place(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item()])
    contentpack.load(tmp_path)
    assert contentpack.load(tmp_path).unchanged == 1
    (tmp_path / "cc-by" / "guide" / "items" / "thesis-basics.yaml").write_text(
        yaml.safe_dump(item(title="Thesis, revised", body="<p>New text.</p>")), encoding="utf-8"
    )
    report = contentpack.load(tmp_path)
    assert (report.updated, ContentItem.objects.count()) == (1, 1)
    content = ContentItem.objects.get()
    assert content.title == "Thesis, revised" and content.body == "<p>New text.</p>"
    assert content.resource.title == "Thesis, revised"


def test_an_item_that_leaves_the_pack_is_retired_not_deleted(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item(), item("citing-sources", title="Citing")])
    contentpack.load(tmp_path)
    (tmp_path / "cc-by" / "guide" / "items" / "citing-sources.yaml").unlink()
    report = contentpack.load(tmp_path)
    assert report.retired == ["citing-sources"]
    gone = ContentItem.objects.get(pk="citing-sources")
    assert not gone.is_active and not gone.resource.is_active


def test_the_catalog_loader_leaves_pack_resources_alone(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item()])
    contentpack.load(tmp_path)
    report = catalog.LoadReport()
    catalog._load_resources([], {}, report)
    assert report.resources_retired == [] and Resource.objects.get().is_active


def test_noncommercial_material_loads_only_when_asked(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item()])
    write_pack(
        tmp_path,
        "noncommercial",
        "essays",
        [item("essay-one", whole=True)],
        license="CC-BY-NC-ND-4.0",
    )
    report = contentpack.load(tmp_path)
    assert report.skipped_tiers == ["noncommercial"]
    assert set(ContentItem.objects.values_list("pk", flat=True)) == {"thesis-basics"}
    contentpack.load(tmp_path, include_noncommercial=True)
    assert ContentItem.objects.get(pk="essay-one").tier == "noncommercial"
    # Leaving the flag off later doesn't retire what was loaded.
    contentpack.load(tmp_path)
    assert ContentItem.objects.get(pk="essay-one").is_active


@pytest.mark.parametrize(
    ("fields", "says"),
    [
        ({"author": ""}, "missing author"),
        ({"license_url": ""}, "missing license_url"),
        ({"changes": ""}, "missing changes"),
        ({"source_url": "http://example.org/x"}, "must start with https://"),
        ({"license": "CC-BY-NC-4.0"}, "doesn't belong in the cc-by folder"),
        ({"license": "All rights reserved"}, "doesn't belong in the cc-by folder"),
        ({"id": "Has Spaces"}, "lowercase letters"),
        ({"topics": {"stage": ["revising"]}}, "needs at least one concern"),
        ({"topics": {"concern": ["no:such-topic"]}}, "unknown concern topic"),
        ({"body": "<script>1</script>"}, "empty after cleaning"),
        ({"body": "x" * 500_000}, "too long"),
    ],
)
def test_an_item_with_a_gap_stops_the_whole_load(tmp_path, vocab, fields, says) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item("fine-item"), item("bad-item", **fields)])
    with pytest.raises(contentpack.PackError, match=says):
        contentpack.load(tmp_path)
    assert not ContentItem.objects.exists()


def test_noadderivatives_items_must_be_whole_and_unchanged(tmp_path, vocab) -> None:
    write_pack(tmp_path, "noncommercial", "essays", [item("essay-one", whole=False)])
    with pytest.raises(contentpack.PackError, match="NoDerivatives"):
        contentpack.load(tmp_path, include_noncommercial=True)


def test_a_noncommercial_license_cant_sit_in_the_open_folder(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item()], license="CC-BY-NC-4.0")
    with pytest.raises(contentpack.PackError):
        contentpack.load(tmp_path)


def test_duplicate_ids_are_refused(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "one", [item()])
    write_pack(tmp_path, "cc-by", "two", [item()])
    with pytest.raises(contentpack.PackError, match="twice"):
        contentpack.load(tmp_path)


def test_a_missing_pack_folder_loads_nothing(tmp_path) -> None:
    assert contentpack.load(tmp_path / "nowhere").created == 0


def test_the_repositorys_own_pack_loads_cleanly(vocab) -> None:
    """Whatever is checked in under content-pack/ must pass the loader's rules."""
    contentpack.load()


def test_the_command_reports_and_refuses(tmp_path, vocab) -> None:
    write_pack(tmp_path, "cc-by", "guide", [item()])
    out = StringIO()
    call_command("load_content_pack", path=tmp_path, stdout=out)
    assert "1 added" in out.getvalue() and "Content pack loaded." in out.getvalue()
    write_pack(tmp_path, "cc-by", "broken", [item("other-item", author="")])
    with pytest.raises(CommandError, match="Nothing was loaded"):
        call_command("load_content_pack", path=tmp_path, stdout=StringIO())


# --- The page ----------------------------------------


def make_item(vocab, tmp_path: Path, **fields: Any) -> ContentItem:
    write_pack(tmp_path, "cc-by", "guide", [item(**fields)])
    contentpack.load(tmp_path)
    return ContentItem.objects.get(pk=fields.get("id", "thesis-basics"))


def test_the_page_shows_the_credit_above_the_text(client, tmp_path, vocab) -> None:
    content = make_item(vocab, tmp_path, changes="Shortened; two examples removed.")
    client.force_login(UserFactory())
    page = client.get(reverse("library:item", kwargs={"item_id": content.pk})).content.decode()
    assert "Thesis basics" in page and "By Pat Author." in page
    assert "From A Guide to Writing." in page and "https://example.org/guide/thesis" in page
    assert "CC-BY-4.0" in page and "https://creativecommons.org/licenses/by/4.0/" in page
    assert "Changes: Shortened; two examples removed." in page
    assert page.index("Credit") < page.index("A thesis is a claim.")
    assert "noncommercial purposes only" not in page


def test_a_whole_item_and_an_unchanged_one_say_so(client, tmp_path, vocab) -> None:
    client.force_login(UserFactory())
    plain = make_item(vocab, tmp_path)
    page = client.get(reverse("library:item", kwargs={"item_id": plain.pk})).content.decode()
    assert "No changes were made." in page
    ContentItem.objects.filter(pk=plain.pk).update(whole=True, tier="noncommercial")
    page = client.get(reverse("library:item", kwargs={"item_id": plain.pk})).content.decode()
    assert "Shown whole and unchanged." in page and "noncommercial purposes only" in page


def test_the_page_cleans_the_text_again(client, tmp_path, vocab) -> None:
    content = make_item(vocab, tmp_path)
    ContentItem.objects.filter(pk=content.pk).update(body="<p>ok</p><script>alert(1)</script>")
    client.force_login(UserFactory())
    page = client.get(reverse("library:item", kwargs={"item_id": content.pk})).content.decode()
    assert "<p>ok</p>" in page and "alert(1)" not in page


def test_the_page_needs_a_sign_in_and_hides_retired_items(client, tmp_path, vocab) -> None:
    content = make_item(vocab, tmp_path)
    url = reverse("library:item", kwargs={"item_id": content.pk})
    response = client.get(url)
    assert response.status_code == 302 and response["Location"].startswith(reverse("account_login"))
    client.force_login(UserFactory())
    assert client.get(url).status_code == 200
    ContentItem.objects.filter(pk=content.pk).update(is_active=False)
    assert client.get(url).status_code == 404
    assert (
        client.get(reverse("library:item", kwargs={"item_id": "nothing-here"})).status_code == 404
    )
