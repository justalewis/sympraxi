"""Tutors' favorite resources, and the director's report on which resources get attached."""

import csv
import io
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.records import favorites
from sympraxi.records.models import LinkState, ResourceFavorite
from sympraxi.reports import resources as resource_report
from sympraxi.reports.forms import Period
from tests.factories import CenterFactory
from tests.records_world import appointment_for, make_resource, make_topics, topics, write_record
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    make_topics()
    return build_world(center)


def url(name: str, world, **kwargs) -> str:
    return reverse(f"records:{name}", kwargs={"slug": world.center.slug, **kwargs})


# --- What counts as a favorite ------------------------------------------------------------


def test_toggling_adds_and_removes(world) -> None:
    resource = make_resource("argument:thesis")
    assert favorites.toggle(world.tutor, resource) is True
    assert favorites.is_favorite(world.tutor, resource)
    assert favorites.toggle(world.tutor, resource) is False
    assert not ResourceFavorite.objects.exists()


def test_a_favorite_is_the_users_own(world) -> None:
    resource = make_resource("argument:thesis")
    favorites.toggle(world.tutor, resource)
    other = make_member(world.center, Role.TUTOR)
    assert favorites.favorites_for(world.tutor, world.center) == [resource]
    assert favorites.favorites_for(other, world.center) == []


def test_only_what_can_be_attached_here_is_shown(world) -> None:
    here = make_resource("argument:thesis", center=world.center, title="Ours")
    elsewhere = make_resource("argument:thesis", center=CenterFactory(), title="Theirs")
    retired = make_resource("argument:thesis", title="Retired", is_active=False)
    broken = make_resource("argument:thesis", title="Gone", link_state=LinkState.BROKEN)
    shared = make_resource("argument:thesis", title="Shared")
    for resource in (here, elsewhere, retired, broken, shared):
        favorites.toggle(world.tutor, resource)
    assert [r.title for r in favorites.favorites_for(world.tutor, world.center)] == [
        "Ours",
        "Shared",
    ]
    assert favorites.ids_for(world.tutor, world.center) == {here.pk, shared.pk, broken.pk}


def test_favorites_that_fit_the_topics_come_first(world) -> None:
    citing = make_resource("citation:apa", title="A citing guide")
    thesis = make_resource("argument:thesis", title="Z thesis guide")
    for resource in (citing, thesis):
        favorites.toggle(world.tutor, resource)
    found = favorites.favorites_for(world.tutor, world.center)
    assert [r.title for r in found] == ["A citing guide", "Z thesis guide"]
    ordered = favorites.for_topics(found, topics("argument:thesis"))
    assert [r.title for r in ordered] == ["Z thesis guide", "A citing guide"]
    assert favorites.for_topics(found, topics("revising")) == found  # no concern: as is


# --- The record form's panel ----------------------------------------------------------------


def panel(client, world, *tags: str, attached=()) -> str:
    data = {"topics": list(tags), "resources": [str(r.pk) for r in attached]}
    return client.post(url("resource_panel", world), data).content.decode()


def test_favorites_show_first_in_the_panel_unless_already_attached(client, world) -> None:
    mine = make_resource("argument:thesis", title="My thesis handout")
    attached = make_resource("argument:thesis", title="Already attached one")
    favorites.toggle(world.tutor, mine)
    favorites.toggle(world.tutor, attached)
    client.force_login(world.tutor)
    page = panel(client, world, "argument:thesis", attached=[attached])
    assert "Your favorites" in page and "My thesis handout" in page
    assert page.index("Your favorites") < page.index("Suggested for the topics")
    assert "Already attached one" not in page.split("Suggested for the topics")[0]


def test_the_panel_has_no_favorites_heading_without_favorites(client, world) -> None:
    make_resource("argument:thesis")
    client.force_login(world.tutor)
    assert "Your favorites" not in panel(client, world, "argument:thesis")


def test_the_star_button_reflects_the_state(client, world) -> None:
    star = make_resource("argument:thesis", title="Starred one")
    plain = make_resource("argument:thesis", title="Plain one")
    favorites.toggle(world.tutor, star)
    client.force_login(world.tutor)
    page = panel(client, world, "argument:thesis")
    assert f'id="favorite-{star.pk}"' in page and "Remove from favorites" in page
    assert f'id="favorite-{plain.pk}"' in page and "Add to favorites" in page


# --- Starring ---------------------------------------------------------------------------------


def test_with_htmx_the_star_changes_in_place(client, world) -> None:
    resource = make_resource("argument:thesis")
    client.force_login(world.tutor)
    toggle = url("favorite_toggle", world, resource_id=resource.pk)
    response = client.post(toggle, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert 'aria-pressed="true"' in response.content.decode()
    response = client.post(toggle, HTTP_HX_REQUEST="true")
    assert 'aria-pressed="false"' in response.content.decode()
    assert not ResourceFavorite.objects.exists()


def test_without_htmx_a_page_asks_and_the_answer_goes_to_the_list(client, world) -> None:
    resource = make_resource("argument:thesis", title="The handout")
    client.force_login(world.tutor)
    toggle = url("favorite_toggle", world, resource_id=resource.pk)
    assert "Add it to my favorites" in client.get(toggle).content.decode()
    response = client.post(toggle, follow=True)
    assert response.redirect_chain[-1][0] == url("favorites", world)
    assert "The handout is one of your favorites." in response.content.decode()
    assert "Remove it from my favorites" in client.get(toggle).content.decode()


def test_a_link_of_another_center_cant_be_starred(client, world) -> None:
    theirs = make_resource("argument:thesis", center=CenterFactory())
    client.force_login(world.tutor)
    assert client.post(url("favorite_toggle", world, resource_id=theirs.pk)).status_code == 404
    assert not ResourceFavorite.objects.exists()


def test_front_desk_staff_and_students_have_none(client, world) -> None:
    resource = make_resource("argument:thesis")
    for role in (Role.FRONT_DESK, Role.STUDENT):
        client.force_login(make_member(world.center, role))
        assert client.get(url("favorites", world)).status_code == 403
        assert (
            client.post(url("favorite_toggle", world, resource_id=resource.pk)).status_code == 403
        )


def test_the_page_lists_favorites_and_finds_more(client, world) -> None:
    have = make_resource("argument:thesis", title="Thesis handout")
    make_resource("citation:apa", title="APA basics", summary="How to cite in APA.")
    favorites.toggle(world.tutor, have)
    client.force_login(world.tutor)
    page = client.get(url("favorites", world)).content.decode()
    assert "Thesis handout" in page and "Remove" in page
    found = client.get(url("favorites", world), {"q": "APA"}).content.decode()
    assert "APA basics" in found and "Add to favorites" in found
    assert "Nothing matches" in client.get(url("favorites", world), {"q": "zzz"}).content.decode()


# --- The director's report --------------------------------------------------------------------


def make_period(world) -> Period:
    today = timezone.localtime(timezone.now(), world.schedule.tz).date()
    return Period(today - timedelta(days=10), today, None, None)


def attach_to_records(world, pairs) -> None:
    """One final record per (tutor, resources), each for a different student on a different day."""
    for day, (tutor, resources) in enumerate(pairs, start=1):
        student = make_member(world.center, Role.STUDENT)
        appointment = appointment_for(world, student, days=-day, tutor=tutor)
        write_record(
            appointment,
            student,
            tags=("argument:thesis", "revising"),
            resources=[(r, "") for r in resources],
            actor=tutor,
        )


def test_the_report_counts_attachments_tutors_and_favorites(world) -> None:
    second_tutor = make_member(world.center, Role.TUTOR)
    handout = make_resource("argument:thesis", center=world.center, title="Our handout")
    guide = make_resource("argument:thesis", title="Shared guide")
    unused = make_resource("argument:thesis", center=world.center, title="Nobody uses this")
    attach_to_records(
        world,
        [(world.tutor, [handout, guide]), (second_tutor, [handout]), (world.tutor, [])],
    )
    favorites.toggle(world.tutor, handout)
    favorites.toggle(second_tutor, handout)
    favorites.toggle(make_member(CenterFactory(), Role.TUTOR), handout)  # another center's tutor
    report = resource_report.build(world.center, make_period(world))
    attached, never, concerns = report.tables
    rows = {r[0]: r for r in attached.rows}
    assert rows["Our handout"][3:6] == [2, 2, 2]  # records, tutors, favorites
    assert rows["Our handout"][2] == "Your center" and rows["Shared guide"][2] == "Shared catalog"
    assert rows["Shared guide"][3:6] == [1, 1, 0]
    assert attached.rows[0][0] == "Our handout"  # most attached first
    assert "3 final session records" in attached.note and "2 of them had" in attached.note
    assert [r[0] for r in never.rows] == [unused.title]
    thesis = next(r for r in concerns.rows if r[1] == "argument:thesis")
    assert thesis[2:] == [3, 2, 67]


def test_old_records_and_drafts_are_left_out(world) -> None:
    resource = make_resource("argument:thesis", title="Old guide")
    student = make_member(world.center, Role.STUDENT)
    old = appointment_for(world, student, days=-60)
    write_record(old, student, resources=[(resource, "")])
    other = make_member(world.center, Role.STUDENT)
    draft_appointment = appointment_for(world, other, days=-2)
    write_record(draft_appointment, other, final=False, resources=[(resource, "")])
    report = resource_report.build(world.center, make_period(world))
    assert report.tables[0].rows == []


def test_the_page_downloads_and_is_for_directors(client, world) -> None:
    handout = make_resource("argument:thesis", center=world.center, title="=Our handout")
    attach_to_records(world, [(world.tutor, [handout])])
    page = reverse("reports:resources", kwargs={"slug": world.center.slug})
    client.force_login(world.tutor)
    assert client.get(page).status_code == 403
    client.force_login(make_member(world.center, Role.DIRECTOR))
    shown = client.get(page).content.decode()
    assert "Resources attached" in shown and "No student is named" in shown
    download = client.get(page, {"format": "csv"})
    assert download.status_code == 200 and "text/csv" in download["Content-Type"]
    rows = list(csv.reader(io.StringIO(download.content.decode())))
    assert any(row and row[0] == "'=Our handout" for row in rows)  # never a formula
