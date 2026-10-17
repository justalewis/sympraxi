"""The record of disclosures (34 CFR 99.32): directors add to it, it can't be
changed, the student reads their own, and it goes into both exports."""

from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.urls import reverse

from sympraxi.centers.models import Membership, Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent, Disclosure, DisclosureBasis
from sympraxi.records import export
from tests.disclosure_world import disclosure
from tests.scheduling_world import make_member
from tests.staff_world import today

pytestmark = pytest.mark.django_db


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR)


@pytest.fixture
def student(center):
    return make_member(center, Role.STUDENT, full_name="Sam Student")


def page_url(center, student) -> str:
    membership = Membership.objects.get(center=center, user=student, role=Role.STUDENT)
    return reverse(
        "privacy:disclosures", kwargs={"slug": center.slug, "membership_id": membership.pk}
    )


def entry_data(center, **values):
    day = (today(center) - timedelta(days=1)).isoformat()
    return {
        "disclosed": "on",
        "requested_on": day,
        "disclosed_on": day,
        "party": "Office of Financial Aid, Example College",
        "interest": "Checking satisfactory academic progress for aid.",
        "basis": DisclosureBasis.FINANCIAL_AID,
        "records": "Attendance for spring 2027",
        "note": "",
        **values,
    }


def test_a_director_records_a_disclosure(client, center, director, student) -> None:
    client.force_login(director)
    url = page_url(center, student)
    assert client.get(url).status_code == 200
    response = client.post(url, entry_data(center))
    assert response.status_code == 302
    entry = Disclosure.objects.get()
    assert (entry.student, entry.center, entry.recorded_by) == (student, center, director)
    [recorded] = AuditEvent.objects.filter(action=AuditAction.DISCLOSURE_RECORDED)
    assert recorded.details["basis"] == "financial_aid"
    assert "Checking satisfactory" not in str(recorded.details)
    assert AuditEvent.objects.filter(action=AuditAction.DISCLOSURES_VIEWED).exists()


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"disclosed_on": ""}, "Give the day the records were disclosed"),
        ({"disclosed": "", "requested_on": "", "disclosed_on": ""}, "Give the day it was asked"),
        ({"requested_on": "2099-01-01", "disclosed_on": "2099-01-02"}, "hasn&#x27;t come yet"),
        ({"party": ""}, "This field is required"),
    ],
)
def test_entries_that_cant_be_recorded(client, center, director, student, changes, says) -> None:
    client.force_login(director)
    data = entry_data(center)
    data.update(changes)
    response = client.post(page_url(center, student), data)
    assert response.status_code == 200
    assert says in response.content.decode()
    assert not Disclosure.objects.exists()


def test_a_refused_request_is_recorded_without_a_disclosure_day(
    client, center, director, student
) -> None:
    client.force_login(director)
    client.post(page_url(center, student), entry_data(center, disclosed="", disclosed_on=""))
    entry = Disclosure.objects.get()
    assert not entry.disclosed and entry.disclosed_on is None


def test_entries_cant_be_changed_or_deleted(center, student) -> None:
    entry = disclosure(center, student)
    with pytest.raises(DatabaseError), transaction.atomic():
        Disclosure.objects.filter(pk=entry.pk).update(party="Someone else")
    with pytest.raises(DatabaseError), transaction.atomic():
        Disclosure.objects.filter(pk=entry.pk).delete()
    # Only the retention purge may delete, inside its own transaction.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL sympraxi.records_purge = 'on'")
        Disclosure.objects.filter(pk=entry.pk).delete()
    assert not Disclosure.objects.exists()


def test_a_mistake_is_fixed_by_a_correction(client, center, director, student) -> None:
    wrong = disclosure(center, student, party="Registrar, Wrong University")
    client.force_login(director)
    client.post(
        page_url(center, student),
        entry_data(center, party="Registrar, State University", corrects=str(wrong.pk)),
    )
    correction = Disclosure.objects.exclude(pk=wrong.pk).get()
    assert correction.corrects == wrong
    page = client.get(page_url(center, student)).content.decode()
    assert "Wrong University" in page and "Correction" in page


def test_only_this_students_entries_can_be_corrected(client, center, director, student) -> None:
    other = make_member(center, Role.STUDENT)
    theirs = disclosure(center, other)
    client.force_login(director)
    response = client.post(page_url(center, student), entry_data(center, corrects=str(theirs.pk)))
    assert response.status_code == 200
    assert Disclosure.objects.count() == 1


def test_the_student_reads_their_own(client, center, student) -> None:
    disclosure(center, student, party="Registrar, State University")
    disclosure(center, make_member(center, Role.STUDENT), party="Someone else's request")
    client.force_login(student)
    page = client.get(reverse("privacy:my_disclosures", kwargs={"slug": center.slug}))
    text = page.content.decode()
    assert page.status_code == 200
    assert "State University" in text and "Someone else" not in text
    my_writing = client.get(reverse("records:my_writing", kwargs={"slug": center.slug}))
    assert reverse("privacy:my_disclosures", kwargs={"slug": center.slug}) in (
        my_writing.content.decode()
    )


def test_tutors_and_front_desk_cant_see_or_add(client, center, student) -> None:
    for role in (Role.TUTOR, Role.FRONT_DESK):
        client.force_login(make_member(center, role))
        assert client.get(page_url(center, student)).status_code == 403


def test_the_student_export_includes_it(center, director, student) -> None:
    disclosure(center, student, party="Registrar, State University")
    membership = Membership.objects.get(center=center, user=student, role=Role.STUDENT)
    data = export.build(membership, exported_by=director)
    assert data["version"] == 4
    [entry] = data["disclosures"]
    assert entry["party"] == "Registrar, State University"
    assert entry["basis"].startswith("Another school")


def test_the_printable_export_lists_it(client, center, director, student) -> None:
    disclosure(center, student, party="Registrar, State University")
    membership = Membership.objects.get(center=center, user=student, role=Role.STUDENT)
    client.force_login(director)
    page = client.get(
        reverse("records:export", kwargs={"slug": center.slug, "membership_id": membership.pk})
    )
    assert "Record of disclosures" in page.content.decode()
    assert "State University" in page.content.decode()
