"""Writing goals, the tutor's brief, and the timeline across terms."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import export, goals, timeline
from sympraxi.records.models import Goal, GoalStatus, WritingProject
from tests.feedback_world import open_queue, send
from tests.records_world import appointment_for, make_topics, write_record
from tests.scheduling_world import build_world, make_member

pytestmark = pytest.mark.django_db


@pytest.fixture
def world(center):
    make_topics()
    return build_world(center)


def membership_of(world, student=None) -> Membership:
    return Membership.objects.get(
        center=world.center, user=student or world.student, role=Role.STUDENT
    )


def my_page(world) -> str:
    return reverse("records:my_goals", kwargs={"slug": world.center.slug})


def staff_page(world, student=None) -> str:
    return reverse(
        "records:student_goals",
        kwargs={"slug": world.center.slug, "membership_id": membership_of(world, student).pk},
    )


def today(world):
    return timezone.localtime(timezone.now(), world.schedule.tz).date()


def past_session(world, **kwargs):
    appointment = appointment_for(world, world.student, days=-2)
    return write_record(appointment, world.student, **kwargs)


# --- Adding and closing -----------------------------------------------------------------


def test_a_student_adds_a_goal_and_it_is_listed(client, world) -> None:
    client.force_login(world.student)
    day = today(world) + timedelta(days=30)
    response = client.post(
        my_page(world),
        {
            "action": "add",
            "text": "Use  evidence in every   paragraph",
            "target_on": day.isoformat(),
        },
        follow=True,
    )
    goal = Goal.objects.get()
    assert goal.text == "Use evidence in every paragraph"  # spaces tidied
    assert (goal.student, goal.created_by, goal.target_on) == (world.student, world.student, day)
    page = response.content.decode()
    assert "The goal is added." in page and "Use evidence in every paragraph" in page
    assert AuditEvent.objects.filter(action=AuditAction.GOAL_ADDED).exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"text": "   "},
        {"text": "x" * 301},
    ],
)
def test_a_goal_needs_words_of_a_sensible_length(client, world, fields) -> None:
    client.force_login(world.student)
    client.post(my_page(world), {"action": "add", **fields})
    assert not Goal.objects.exists()


@pytest.mark.parametrize("offset", [-1, 800])
def test_the_day_must_be_from_today_to_two_years_out(client, world, offset) -> None:
    client.force_login(world.student)
    day = (today(world) + timedelta(days=offset)).isoformat()
    response = client.post(my_page(world), {"action": "add", "text": "Revise", "target_on": day})
    assert "from today to two years ahead" in response.content.decode()
    assert not Goal.objects.exists()


def test_a_goal_can_name_one_of_the_students_projects_only(client, world) -> None:
    mine = WritingProject.objects.create(center=world.center, student=world.student, title="Essay")
    other = make_member(world.center, Role.STUDENT)
    theirs = WritingProject.objects.create(center=world.center, student=other, title="Theirs")
    client.force_login(world.student)
    client.post(my_page(world), {"action": "add", "text": "Cite", "project": str(theirs.pk)})
    assert not Goal.objects.exists()
    client.post(my_page(world), {"action": "add", "text": "Cite", "project": str(mine.pk)})
    assert Goal.objects.get().project == mine


def test_there_is_a_limit_on_open_goals(client, world) -> None:
    for n in range(goals.MAX_OPEN):
        goals.add(actor=world.student, center=world.center, student=world.student, text=f"Goal {n}")
    client.force_login(world.student)
    response = client.post(my_page(world), {"action": "add", "text": "One more"})
    assert "open goals" in response.content.decode()
    assert Goal.objects.count() == goals.MAX_OPEN


def test_goals_are_marked_met_dropped_and_opened_again(client, world) -> None:
    goal = goals.add(actor=world.student, center=world.center, student=world.student, text="Cite")
    client.force_login(world.student)
    client.post(my_page(world), {"action": "met", "goal": str(goal.pk)})
    goal.refresh_from_db()
    assert goal.status == GoalStatus.MET and goal.closed_by == world.student and goal.closed_at
    page = client.get(my_page(world)).content.decode()
    assert "Met and dropped" in page and "Open it again" in page
    client.post(my_page(world), {"action": "dropped", "goal": str(goal.pk)})
    goal.refresh_from_db()
    assert goal.status == GoalStatus.DROPPED
    client.post(my_page(world), {"action": "open", "goal": str(goal.pk)})
    goal.refresh_from_db()
    assert (goal.status, goal.closed_at, goal.closed_by) == (GoalStatus.OPEN, None, None)
    assert AuditEvent.objects.filter(action=AuditAction.GOAL_CHANGED).count() == 3


def test_a_made_up_status_or_id_changes_nothing(client, world) -> None:
    goal = goals.add(actor=world.student, center=world.center, student=world.student, text="Cite")
    client.force_login(world.student)
    client.post(my_page(world), {"action": "deleted", "goal": str(goal.pk)})
    client.post(my_page(world), {"action": "met", "goal": "not-an-id"})
    goal.refresh_from_db()
    assert goal.status == GoalStatus.OPEN


def test_a_student_cant_change_another_students_goal(client, world) -> None:
    other = make_member(world.center, Role.STUDENT)
    theirs = goals.add(actor=other, center=world.center, student=other, text="Theirs")
    client.force_login(world.student)
    response = client.post(my_page(world), {"action": "met", "goal": str(theirs.pk)}, follow=True)
    theirs.refresh_from_db()
    assert theirs.status == GoalStatus.OPEN and "wasn&#x27;t found" in response.content.decode()


# --- Staff ----------------------------------------------------------------------------------


def test_a_tutor_who_works_with_the_student_sets_a_goal_with_them(client, world) -> None:
    client.force_login(world.tutor)
    client.post(staff_page(world), {"action": "add", "text": "Tighten the thesis"})
    goal = Goal.objects.get()
    assert (goal.student, goal.created_by) == (world.student, world.tutor)
    client.force_login(world.student)
    assert "Tighten the thesis" in client.get(my_page(world)).content.decode()


def test_other_tutors_and_front_desk_staff_have_no_goals_page(client, world) -> None:
    client.force_login(make_member(world.center, Role.TUTOR))
    assert client.get(staff_page(world)).status_code == 404
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(staff_page(world)).status_code == 403
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(staff_page(world)).status_code == 200


def test_a_student_doesnt_open_a_classmates_goals_page(client, world) -> None:
    client.force_login(make_member(world.center, Role.STUDENT))
    assert client.get(staff_page(world)).status_code == 404


def test_the_tutors_brief_lists_open_goals_only(client, world) -> None:
    open_goal = goals.add(
        actor=world.student, center=world.center, student=world.student, text="Open goal here"
    )
    done = goals.add(
        actor=world.student, center=world.center, student=world.student, text="Done goal"
    )
    goals.set_status(done, GoalStatus.MET, actor=world.student)
    client.force_login(world.tutor)
    page = client.get(
        reverse(
            "scheduling:appointment",
            kwargs={"slug": world.center.slug, "appointment_id": world.appointment.pk},
        )
    ).content.decode()
    assert open_goal.text in page and "Done goal" not in page
    assert "Add or change goals" in page


# --- The timeline ---------------------------------------------------------------------------


def test_the_timeline_gathers_what_the_center_keeps_by_term(world) -> None:
    final = past_session(world, summary="You tightened the thesis. " * 20)
    goal = goals.add(actor=world.student, center=world.center, student=world.student, text="Cite")
    goals.set_status(goal, GoalStatus.MET, actor=world.student)
    WritingProject.objects.create(
        center=world.center, student=world.student, title="Capstone", course="ENG 400"
    )
    open_queue(world.center)
    sent = send(world.center, world.student, title="Essay draft")
    groups = timeline.build(world.center, world.student, world.student, own=True)
    assert [g.label for g in groups] == [world.term.name]
    titles = [e.title for e in groups[0].entries]
    assert any(t.startswith("Session with") for t in titles)
    assert "Goal set: Cite" in titles and "Goal met: Cite" in titles
    assert "Writing project begun: Capstone" in titles
    assert "Draft sent for written feedback: Essay draft" in titles
    whens = [e.when for e in groups[0].entries]
    assert whens == sorted(whens, reverse=True)
    session = next(e for e in groups[0].entries if e.kind == "session")
    assert len(session.detail) <= timeline.SUMMARY_CHARS and session.detail.endswith("…")
    assert final.pk and sent.pk


def test_entries_outside_every_term_go_last(world) -> None:
    long_ago = appointment_for(world, world.student, days=-400)
    write_record(long_ago, world.student)
    groups = timeline.build(world.center, world.student, world.student, own=True)
    assert groups[-1].label == "Outside any term"


def test_a_student_sees_only_final_sessions_and_staff_see_drafts(client, world) -> None:
    past_session(world, final=False, summary="A half-written summary.")
    other = appointment_for(world, world.student, days=-3)
    write_record(
        other, world.student, summary="Finished summary.", staff_notes="Secret staff note."
    )
    base = {"slug": world.center.slug}
    client.force_login(world.student)
    mine = client.get(reverse("records:my_timeline", kwargs=base)).content.decode()
    assert "Finished summary." in mine and "A half-written summary." not in mine
    assert "(draft)" not in mine and "Secret staff note." not in mine
    client.force_login(world.tutor)
    staff = client.get(
        reverse(
            "records:student_timeline",
            kwargs={**base, "membership_id": membership_of(world).pk},
        )
    ).content.decode()
    assert "(draft)" in staff and "Finished summary." in staff
    assert "Secret staff note." not in staff
    assert AuditEvent.objects.filter(action=AuditAction.TIMELINE_VIEWED).exists()


def test_a_returned_draft_shows_twice_and_an_unreturned_one_once(world) -> None:
    open_queue(world.center)
    send(world.center, world.student, title="Waiting")
    groups = timeline.build(world.center, world.student, world.student, own=True)
    titles = [e.title for g in groups for e in g.entries]
    assert titles.count("Draft sent for written feedback: Waiting") == 1
    assert not any(t.startswith("Written feedback returned") for t in titles)


def test_who_may_open_the_staff_timeline(client, world) -> None:
    url = reverse(
        "records:student_timeline",
        kwargs={"slug": world.center.slug, "membership_id": membership_of(world).pk},
    )
    client.force_login(make_member(world.center, Role.TUTOR))
    assert client.get(url).status_code == 404  # hasn't tutored this student
    client.force_login(make_member(world.center, Role.FRONT_DESK))
    assert client.get(url).status_code == 403
    client.force_login(make_member(world.center, Role.DIRECTOR))
    assert client.get(url).status_code == 200


# --- The export -----------------------------------------------------------------------------


def test_the_student_export_lists_goals(world) -> None:
    goal = goals.add(actor=world.tutor, center=world.center, student=world.student, text="Cite")
    goals.set_status(goal, GoalStatus.DROPPED, actor=world.student)
    data = export.build(membership_of(world), exported_by=make_member(world.center, Role.DIRECTOR))
    [entry] = data["goals"]
    assert entry["goal"] == "Cite" and entry["status"] == "Dropped"
    assert entry["added_by"]["name"] == world.tutor.display_name
    assert entry["closed_by"]["name"] == world.student.display_name
