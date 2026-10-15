"""Resources suggested from a record's topics, and the catalog search."""

import pytest
from django.core.management import call_command

from sympraxi.records import suggestions
from sympraxi.records.models import Topic
from tests.factories import CenterFactory
from tests.records_world import make_resource, topics

pytestmark = pytest.mark.django_db


def titles(found) -> list[str]:
    return [s.resource.title for s in found]


def test_exact_concerns_come_first_then_more_specific_then_related(center) -> None:
    make_resource("citation:apa", "editing", title="APA in-text citations")
    make_resource("citation:apa", title="APA reference list")
    make_resource("citation", title="Why we cite")
    make_resource("citation:mla", title="MLA works cited")
    make_resource("argument:thesis", "editing", title="Thesis tips")

    found = suggestions.suggest(center, topics("citation:apa", "editing"))
    # APA exactly (3, plus 1 for the stage), APA exactly, then the parent and a sibling.
    assert titles(found) == [
        "APA in-text citations",
        "APA reference list",
        "MLA works cited",
        "Why we cite",
    ]
    assert found[0].score == 4

    # Tagging only the parent prefers the parent, then everything under it.
    found = suggestions.suggest(center, topics("citation"))
    assert titles(found)[0] == "Why we cite"
    assert set(titles(found)[1:]) == {
        "APA in-text citations",
        "APA reference list",
        "MLA works cited",
    }


def test_a_related_tag_does_not_add_to_a_shared_one(center) -> None:
    make_resource("citation:apa", "citation:mla", title="Zotero for APA and MLA")
    make_resource("citation:apa", title="APA basics")
    found = suggestions.suggest(center, topics("citation:apa"))
    assert titles(found) == ["APA basics", "Zotero for APA and MLA"]
    assert {s.score for s in found} == {3}
    # Two concerns shared count twice.
    make_resource("citation:apa", "argument:thesis", title="Thesis and APA")
    found = suggestions.suggest(center, topics("citation:apa", "argument:thesis"))
    assert titles(found)[0] == "Thesis and APA" and found[0].score == 6


def test_stages_and_actions_alone_suggest_nothing(center) -> None:
    make_resource("argument:thesis", "revising", "asked-questions")
    assert suggestions.suggest(center, topics("revising", "asked-questions")) == []
    assert suggestions.suggest(center, []) == []


def test_attached_inactive_and_other_centers_resources_are_left_out(center) -> None:
    attached = make_resource("argument:thesis", title="Already attached")
    make_resource("argument:thesis", title="Retired", is_active=False)
    make_resource("argument:thesis", center=CenterFactory(), title="Another center's handout")
    ours = make_resource("argument:thesis", center=center, title="Our handout")
    make_resource("argument:thesis", title="A shared one")

    found = suggestions.suggest(center, topics("argument:thesis"), exclude=[attached.pk])
    # Ties go to the center's own links.
    assert titles(found) == ["Our handout", "A shared one"]
    assert found[0].resource == ours


def test_search_matches_every_word_in_title_summary_or_publisher(center) -> None:
    make_resource("citation:apa", title="APA reference list", publisher="Purdue OWL")
    make_resource("citation:mla", title="MLA works cited", publisher="Purdue OWL")
    make_resource("citation:apa", title="Citing in APA", summary="Reference pages explained")
    make_resource("citation:apa", center=CenterFactory(), title="APA reference list elsewhere")

    assert {r.title for r in suggestions.search(center, "apa reference")} == {
        "APA reference list",
        "Citing in APA",
    }
    assert {r.title for r in suggestions.search(center, "purdue")} == {
        "APA reference list",
        "MLA works cited",
    }
    assert suggestions.search(center, "   ") == []


def test_the_real_catalog_suggests_apa_pages_for_apa(center) -> None:
    call_command("load_catalog", verbosity=0, stdout=None)
    chosen = list(Topic.objects.filter(slug__in=["citation:apa", "editing"]))
    found = suggestions.suggest(center, chosen)
    assert found, "The starter catalog has APA entries"
    assert all(any(t.slug == "citation:apa" for t in s.resource.topics.all()) for s in found[:3])
