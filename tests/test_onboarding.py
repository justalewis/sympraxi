"""Cohorts, mentors, sign-offs, observations, and finishing onboarding."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.staff.models import Category
from sympraxi.training import onboarding, progress
from sympraxi.training.models import (
    Cohort,
    CohortMember,
    Criterion,
    Observation,
    ObservationStatus,
    Rating,
    Template,
)
from tests.scheduling_world import make_member
from tests.staff_world import entry, today
from tests.training_world import make_module

pytestmark = pytest.mark.django_db


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


@pytest.fixture
def mentor(center):
    return make_member(center, Role.TUTOR, full_name="Mo Mentor")


@pytest.fixture
def newbie(center):
    return make_member(center, Role.TUTOR, full_name="Nia Newbie")


@pytest.fixture
def cohort(center):
    return Cohort.objects.create(
        center=center, name="Fall", starts_on=date.today(), required_observations=1
    )


@pytest.fixture
def checklist(center):
    template = Template.objects.create(center=center, title="Session")
    for n, text in enumerate(["Opens warmly", "Asks before telling"]):
        Criterion.objects.create(template=template, text=text, position=n)
    return template


def u(name: str, center, **kwargs) -> str:
    return reverse(f"training:{name}", kwargs={"slug": center.slug, **kwargs})


def finish_module(person, module) -> None:
    for item in module.items.exclude(kind="quiz"):
        progress.mark_done(person, item)
    quiz = module.items.get(kind="quiz")
    right = {str(q.pk): str(q.choices.get(is_correct=True).pk) for q in quiz.questions.all()}
    assert progress.submit_quiz(person, quiz, right).passed


# --- Cohorts ----------


def test_a_director_starts_a_cohort_and_adds_people_with_mentors(
    client, center, director, mentor, newbie
) -> None:
    client.force_login(director)
    response = client.post(
        u("cohorts", center),
        {"name": "Spring 2027", "starts_on": "2027-01-11", "required_observations": 2},
    )
    cohort = Cohort.objects.get()
    assert response["Location"] == u("cohort", center, cohort_id=cohort.pk)
    client.post(
        u("cohort", center, cohort_id=cohort.pk),
        {"action": "add", "person": str(newbie.pk), "mentor": str(mentor.pk)},
    )
    member = CohortMember.objects.get()
    assert (member.person, member.mentor) == (newbie, mentor)
    assert AuditEvent.objects.filter(action=AuditAction.ONBOARDING_CHANGED).count() == 2
    page = client.get(u("cohort", center, cohort_id=cohort.pk)).content.decode()
    assert "Nia Newbie" in page and "Mo Mentor" in page


def test_only_tutors_and_front_desk_join_a_cohort_and_not_twice(
    client, center, cohort, director, mentor, newbie
) -> None:
    with pytest.raises(onboarding.OnboardingError, match="Only tutors"):
        onboarding.add_member(cohort, director, None, request=_request(director))
    with pytest.raises(onboarding.OnboardingError, match="own mentor"):
        onboarding.add_member(cohort, newbie, newbie, request=_request(director))
    onboarding.add_member(cohort, newbie, mentor, request=_request(director))
    with pytest.raises(onboarding.OnboardingError, match="already in this cohort"):
        onboarding.add_member(cohort, newbie, None, request=_request(director))
    assert newbie not in onboarding.addable(cohort) and mentor in onboarding.addable(cohort)


def _request(user):
    from django.test import RequestFactory

    request = RequestFactory().post("/")
    request.user = user
    return request


def test_a_mentor_can_be_changed_and_a_member_removed(
    client, center, cohort, director, mentor, newbie
) -> None:
    member = onboarding.add_member(cohort, newbie, None, request=_request(director))
    client.force_login(director)
    url = u("cohort", center, cohort_id=cohort.pk)
    client.post(url, {"action": "mentor", "member": str(member.pk), "mentor": str(mentor.pk)})
    member.refresh_from_db()
    assert member.mentor == mentor
    client.post(url, {"action": "mentor", "member": str(member.pk), "mentor": str(newbie.pk)})
    member.refresh_from_db()
    assert member.mentor == mentor  # not their own mentor
    client.post(url, {"action": "remove", "member": str(member.pk)})
    assert CohortMember.objects.count() == 0


def test_a_member_of_another_cohort_cannot_be_changed_through_this_one(
    client, center, cohort, director, newbie
) -> None:
    other = Cohort.objects.create(center=center, name="Other", starts_on=date.today())
    member = onboarding.add_member(other, newbie, None, request=_request(director))
    client.force_login(director)
    client.post(
        u("cohort", center, cohort_id=cohort.pk), {"action": "remove", "member": str(member.pk)}
    )
    assert CohortMember.objects.count() == 1


# --- Sign-offs and finishing ----------


def member_in(cohort, newbie, mentor, director):
    return onboarding.add_member(cohort, newbie, mentor, request=_request(director))


def test_a_mentor_signs_off_only_a_finished_module(
    client, center, cohort, director, mentor, newbie
) -> None:
    module = make_module(center)
    member = member_in(cohort, newbie, mentor, director)
    client.force_login(mentor)
    url = u("mentee", center, member_id=member.pk)
    client.post(url, {"action": "sign_off", "module": str(module.pk), "note": "Great"})
    assert member.sign_offs.count() == 0
    finish_module(newbie, module)
    client.post(url, {"action": "sign_off", "module": str(module.pk), "note": "Great"})
    [signed] = member.sign_offs.all()
    assert (signed.signed_by, signed.note) == (mentor, "Great")
    assert "Signed off by Mo Mentor" in client.get(url).content.decode()
    client.post(url, {"action": "sign_off", "module": str(module.pk), "note": "again"})
    assert member.sign_offs.count() == 1


def test_a_stranger_cannot_open_or_sign_off_someone_elses_mentee(
    client, center, cohort, director, mentor, newbie
) -> None:
    member = member_in(cohort, newbie, mentor, director)
    other_tutor = make_member(center, Role.TUTOR)
    client.force_login(other_tutor)
    assert client.get(u("mentee", center, member_id=member.pk)).status_code == 404
    assert client.get(u("mentees", center)).status_code == 200
    assert "Nia Newbie" not in client.get(u("mentees", center)).content.decode()
    client.force_login(director)
    assert client.get(u("mentee", center, member_id=member.pk)).status_code == 200


def test_the_mentees_page_lists_only_my_people_for_a_mentor_and_everyone_for_a_director(
    client, center, cohort, director, mentor, newbie
) -> None:
    other = make_member(center, Role.TUTOR, full_name="Other Newbie")
    member_in(cohort, newbie, mentor, director)
    onboarding.add_member(cohort, other, None, request=_request(director))
    client.force_login(mentor)
    mine = client.get(u("mentees", center)).content.decode()
    assert "Nia Newbie" in mine and "Other Newbie" not in mine
    client.force_login(director)
    every = client.get(u("mentees", center)).content.decode()
    assert "Nia Newbie" in every and "Other Newbie" in every


def submit_observation(center, checklist, newbie, mentor, request):
    observation = onboarding.start_observation(
        checklist, newbie, mentor, date.today(), request=request
    )
    scores = {str(c.pk): "3" for c in checklist.criteria.all()}
    onboarding.save_observation(observation, scores, {}, "Good", submit=True, request=request)
    return observation


def test_onboarding_is_complete_only_when_modules_observations_and_hours_are_all_met(
    client, center, cohort, director, mentor, newbie, checklist
) -> None:
    center.training_hours_required = Decimal("2.00")
    center.save()
    module = make_module(center)
    member = member_in(cohort, newbie, mentor, director)
    request = _request(director)
    assert not onboarding.status_of(member).ready
    with pytest.raises(onboarding.OnboardingError, match="outstanding"):
        onboarding.complete(member, by=director, request=request)
    finish_module(newbie, module)
    onboarding.sign_off(member, module, by=mentor, note="", request=_request(mentor))
    status = onboarding.status_of(member)
    assert status.modules_ok and not status.observations_ok and not status.hours_ok
    submit_observation(center, checklist, newbie, mentor, _request(mentor))
    entry(center, newbie, today(center) - timedelta(days=1), (9, 0), (11, 30), Category.TRAINING)
    status = onboarding.status_of(member)
    assert status.ready and not status.complete
    client.force_login(director)
    page = client.get(u("cohort", center, cohort_id=cohort.pk)).content.decode()
    assert "onboarding complete" in page
    client.post(
        u("cohort", center, cohort_id=cohort.pk), {"action": "complete", "member": str(member.pk)}
    )
    member.refresh_from_db()
    assert member.completed_by == director and member.completed_at is not None
    assert AuditEvent.objects.filter(action=AuditAction.ONBOARDING_COMPLETED).count() == 1
    with pytest.raises(onboarding.OnboardingError, match="already complete"):
        onboarding.complete(member, by=director, request=request)


def test_an_optional_module_does_not_hold_up_onboarding(
    center, cohort, director, mentor, newbie, checklist
) -> None:
    required = make_module(center, title="Required")
    optional = make_module(center, title="Extra")
    optional.is_required = False
    optional.save()
    member = member_in(cohort, newbie, mentor, director)
    finish_module(newbie, required)
    onboarding.sign_off(member, required, by=mentor, note="", request=_request(mentor))
    submit_observation(center, checklist, newbie, mentor, _request(mentor))
    assert onboarding.status_of(member).ready


# --- Observations ----------


def test_a_mentor_fills_in_and_submits_an_observation(
    client, center, cohort, director, mentor, newbie, checklist
) -> None:
    member = member_in(cohort, newbie, mentor, director)
    client.force_login(mentor)
    response = client.post(
        u("mentee", center, member_id=member.pk),
        {
            "action": "observe",
            "template": str(checklist.pk),
            "observed_on": date.today().isoformat(),
        },
    )
    observation = Observation.objects.get()
    assert response["Location"] == u("observation", center, observation_id=observation.pk)
    url = u("observation", center, observation_id=observation.pk)
    first, second = list(checklist.criteria.all())
    client.post(
        url, {"action": "save", f"s_{first.pk}": "4", f"c_{first.pk}": "Warm", "notes": "So far"}
    )
    assert Rating.objects.get().score == 4 and not observation.is_submitted
    early = client.post(url, {"action": "submit", f"s_{first.pk}": "4"}, follow=True)
    assert "Give every item a rating" in early.content.decode()
    client.post(
        url,
        {
            "action": "submit",
            f"s_{first.pk}": "4",
            f"s_{second.pk}": "2",
            f"c_{second.pk}": "Try asking more",
            "notes": "Good start",
        },
    )
    observation.refresh_from_db()
    assert observation.status == ObservationStatus.SUBMITTED and observation.notes == "Good start"
    assert AuditEvent.objects.filter(action=AuditAction.OBSERVATION_SUBMITTED).count() == 1
    locked = client.post(url, {"action": "save", f"s_{first.pk}": "1"})
    assert locked.status_code == 403
    assert Rating.objects.get(criterion=first).score == 4


def test_the_observed_tutor_reads_it_only_after_it_is_submitted(
    client, center, cohort, director, mentor, newbie, checklist
) -> None:
    member_in(cohort, newbie, mentor, director)
    observation = onboarding.start_observation(
        checklist, newbie, mentor, date.today(), request=_request(mentor)
    )
    url = u("observation", center, observation_id=observation.pk)
    client.force_login(newbie)
    assert client.get(url).status_code == 404
    onboarding.save_observation(
        observation,
        {str(c.pk): "3" for c in checklist.criteria.all()},
        {},
        "Nicely done",
        submit=True,
        request=_request(mentor),
    )
    page = client.get(url).content.decode()
    assert "Nicely done" in page and "Solid" in page and "Submit the observation" not in page
    other = make_member(center, Role.TUTOR)
    client.force_login(other)
    assert client.get(url).status_code == 404
    student = make_member(center, Role.STUDENT)
    client.force_login(student)
    assert client.get(url).status_code == 403


def test_an_observer_cannot_observe_themselves_or_use_an_empty_or_unused_checklist(
    center, mentor, newbie
) -> None:
    request = _request(mentor)
    empty = Template.objects.create(center=center, title="Empty")
    with pytest.raises(onboarding.OnboardingError, match="nothing on it"):
        onboarding.start_observation(empty, newbie, mentor, date.today(), request=request)
    Criterion.objects.create(template=empty, text="Something")
    with pytest.raises(onboarding.OnboardingError, match="yourself"):
        onboarding.start_observation(empty, mentor, mentor, date.today(), request=request)
    empty.is_active = False
    empty.save()
    with pytest.raises(onboarding.OnboardingError, match="isn't in use"):
        onboarding.start_observation(empty, newbie, mentor, date.today(), request=request)


# --- The database keeps submitted observations ----------


def test_the_database_refuses_changes_to_a_submitted_observation(
    center, mentor, newbie, checklist
) -> None:
    observation = submit_observation(center, checklist, newbie, mentor, _request(mentor))
    rating = observation.ratings.first()
    for sql, params in [
        ("UPDATE training_observation SET notes = 'x' WHERE id = %s", [observation.pk]),
        ("DELETE FROM training_observation WHERE id = %s", [observation.pk]),
        ("UPDATE training_rating SET score = 1 WHERE id = %s", [rating.pk]),
        ("DELETE FROM training_rating WHERE id = %s", [rating.pk]),
    ]:
        with pytest.raises(DatabaseError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, params)
    with pytest.raises(DatabaseError), transaction.atomic():
        Rating.objects.create(
            observation=observation, criterion=observation.template.criteria.first(), score=2
        )
    assert observation.ratings.count() == 2


def test_a_draft_observation_changes_freely(center, mentor, newbie, checklist) -> None:
    observation = onboarding.start_observation(
        checklist, newbie, mentor, date.today(), request=_request(mentor)
    )
    observation.notes = "edited"
    observation.save()
    observation.delete()
    assert Observation.objects.count() == 0


# --- Checklists ----------


def test_a_director_builds_a_checklist(client, center, director) -> None:
    client.force_login(director)
    created = client.post(u("templates", center), {"title": "Group session", "is_active": "on"})
    template = Template.objects.get()
    edit = u("template", center, template_id=template.pk)
    assert created["Location"] == edit
    for text in ("First", "Second"):
        client.post(edit, {"action": "add", "text": text})
    first = template.criteria.first()
    client.post(edit, {"action": "down", "criterion": str(first.pk)})
    assert [c.text for c in template.criteria.all()] == ["Second", "First"]
    client.post(edit, {"action": "delete", "criterion": str(first.pk)})
    assert [c.text for c in template.criteria.all()] == ["Second"]


def test_a_criterion_used_in_an_observation_is_not_deleted(
    client, center, director, mentor, newbie, checklist
) -> None:
    submit_observation(center, checklist, newbie, mentor, _request(mentor))
    used = checklist.criteria.first()
    client.force_login(director)
    response = client.post(
        u("template", center, template_id=checklist.pk),
        {"action": "delete", "criterion": str(used.pk)},
        follow=True,
    )
    assert "can&#x27;t be deleted" in response.content.decode()
    assert checklist.criteria.count() == 2


def test_students_and_other_centers_cannot_reach_onboarding(
    client, center, other_center, cohort
) -> None:
    client.force_login(make_member(center, Role.STUDENT))
    assert client.get(u("cohorts", center)).status_code == 403
    outsider = make_member(other_center, Role.DIRECTOR)
    client.force_login(outsider)
    assert client.get(u("cohort", center, cohort_id=cohort.pk)).status_code == 404
