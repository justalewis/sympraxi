"""Adding people to a center: one at a time, from a CSV file, and turning roles off.

Reading files (columns, encodings, problems by line), what an import changes and
what it leaves alone, the preview and confirm steps, welcome emails, and that
someone added this way can claim the account with single sign-on or a password
reset.
"""

import re

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from sympraxi.accounts.models import User
from sympraxi.centers import people
from sympraxi.centers.models import Membership, Role
from sympraxi.centers.people_views import SESSION_KEY
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from tests.factories import CenterFactory, MembershipFactory, UserFactory, give_mfa
from tests.scheduling_world import build_world
from tests.sso_idp import entra_claims, sign_in

pytestmark = pytest.mark.django_db


def read(text: str, *, role: Role | None = Role.STUDENT, encoding: str = "utf-8") -> people.Reading:
    return people.read_csv(text.encode(encoding), default_role=role)


def problems_by_line(reading: people.Reading) -> dict[int, str]:
    return {p.line: " ".join(p.problems) for p in reading.problems}


def audit(action: AuditAction) -> list[AuditEvent]:
    return list(AuditEvent.objects.filter(action=action).order_by("id"))


@pytest.fixture
def director(center):
    user = UserFactory(full_name="Dana Director")
    MembershipFactory(user=user, center=center, role=Role.DIRECTOR)
    give_mfa(user)
    return user


@pytest.fixture
def signed_in(client, director):
    client.force_login(director)
    return client


def upload(client, center, text: str, **data):
    file = SimpleUploadedFile("people.csv", text.encode(), content_type="text/csv")
    return client.post(
        reverse("centers:import", kwargs={"slug": center.slug}),
        {"file": file, "role": Role.STUDENT, **data},
    )


def confirm(client, center):
    key = client.session[SESSION_KEY]["key"]
    return client.post(
        reverse("centers:import", kwargs={"slug": center.slug}), {"confirm": "1", "key": key}
    )


# --- Reading files ---------------------------------------------------------------


def test_columns_are_found_by_name_in_any_order() -> None:
    reading = read(
        "Role,Student Number,Pronouns,Last Name,First Name,E-mail Address,Preferred name\n"
        "tutor,A001,she/her,Ruiz,Ana,ana@example.edu,Annie\n"
    )
    [row] = reading.rows
    assert row == people.PersonRow(
        line=2,
        email="ana@example.edu",
        full_name="Ana Ruiz",
        role=Role.TUTOR,
        preferred_name="Annie",
        pronouns="she/her",
        student_id="A001",
    )


def test_a_full_name_column_wins_over_first_and_last() -> None:
    [row] = read("email,full name,first name\nana@example.edu,Ana María Ruiz,Ana\n").rows
    assert row.full_name == "Ana María Ruiz"


def test_files_from_excel_read_in_either_encoding() -> None:
    with_bom = people.read_csv(
        "﻿Email,Name\nzoe@example.edu,Zoë Ng\n".encode(), default_role=Role.STUDENT
    )
    windows = people.read_csv(
        "Email,Name\nzoe@example.edu,Zoë Ng\n".encode("cp1252"), default_role=Role.STUDENT
    )
    assert with_bom.rows[0].full_name == windows.rows[0].full_name == "Zoë Ng"
    assert with_bom.rows[0].email == "zoe@example.edu"


@pytest.mark.parametrize("sep", [";", "\t"])
def test_semicolons_and_tabs_separate_columns_too(sep) -> None:
    [row] = read(f"Email{sep}Name\nzoe@example.edu{sep}Zoe Ng\n").rows
    assert (row.email, row.full_name) == ("zoe@example.edu", "Zoe Ng")


def test_roles_come_from_the_file_or_the_choice_for_the_whole_file() -> None:
    text = (
        "email,name,role\n"
        "a@example.edu,A,\n"
        "b@example.edu,B,Front desk\n"
        "c@example.edu,C,Consultant\n"
        "d@example.edu,D,dean\n"
    )
    reading = read(text, role=Role.STUDENT)
    assert [(r.email[0], r.role) for r in reading.rows] == [
        ("a", Role.STUDENT),
        ("b", Role.FRONT_DESK),
        ("c", Role.TUTOR),
    ]
    assert "“dean” isn't a role" in problems_by_line(reading)[5]
    no_default = read(text, role=None)
    assert "There's no role" in problems_by_line(no_default)[2]


def test_problems_are_listed_by_line_and_the_rest_still_read() -> None:
    UserFactory(email="taken@example.edu", student_id="S-TAKEN")
    reading = read(
        "email,name,student id,role\n"
        "not-an-address,Nobody,,\n"  # 2
        "noname@example.edu,,,\n"  # 3
        "ok@example.edu,Okay,S1,\n"  # 4
        "OK@example.edu,Okay Again,,\n"  # 5: same person, same role
        "ok@example.edu,Okay,,tutor\n"  # 6: same person, another role is fine
        "other@example.edu,Other,S1,\n"  # 7: S1 belongs to line 4
        "new@example.edu,New,S-TAKEN,\n"  # 8: another account has S-TAKEN
        "taken@example.edu,Taken,S-TAKEN,\n"  # 9: the account that has it
        f"long@example.edu,{'x' * 201},,\n"  # 10
    )
    found = problems_by_line(reading)
    assert "isn't an email address" in found[2]
    assert "There's no name" in found[3]
    assert "Line 4 already adds this person in this role" in found[5]
    assert "Line 4 gives someone else this student ID" in found[7]
    assert "Another account already has this student ID" in found[8]
    assert "longer than 200 characters" in found[10]
    assert sorted(found) == [2, 3, 5, 7, 8, 10]
    assert [r.line for r in reading.rows] == [4, 6, 9]


def test_only_college_addresses_when_the_site_limits_sign_up(settings) -> None:
    settings.SYMPRAXI_SIGNUP_EMAIL_DOMAINS = ["example.edu"]
    reading = read("email,name\nana@EXAMPLE.edu,Ana\nana@gmail.com,Ana\n")
    assert [r.email for r in reading.rows] == ["ana@example.edu"]
    assert "Only college addresses" in problems_by_line(reading)[3]


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("", "The file is empty"),
        ("name,role\nAna,student\n", "one of them must be Email"),
        ("email,role\na@example.edu,student\n", "needs a Full name column"),
        ("email,name\n", "has nobody in it"),
        ('email,name\n"a@example.edu,Ana\n', ""),
    ],
)
def test_problems_with_the_whole_file(text, says) -> None:
    reading = read(text)
    if says:
        assert says in reading.file_problem
        assert not reading.rows


def test_files_too_big_or_too_long_are_refused(monkeypatch) -> None:
    monkeypatch.setattr(people, "MAX_ROWS", 2)
    many = read("email,name\na@example.edu,A\nb@example.edu,B\nc@example.edu,C\n")
    assert "more than 2 people" in many.file_problem
    monkeypatch.setattr(people, "MAX_BYTES", 10)
    assert "larger than 2 MB" in read("email,name\na@example.edu,A\n").file_problem


# --- Adding ------------------------------------------------------------------------


def test_new_people_get_an_account_without_a_password(rf, center, director) -> None:
    request = rf.post("/")
    request.user = director
    [row] = read("email,name,pronouns,student id\nana@example.edu,Ana Ruiz,she/her,A1\n").rows
    outcome = people.add_people(center, [row], request=request, via="import")
    user = User.objects.get(email="ana@example.edu")
    assert (user.full_name, user.pronouns, user.student_id) == ("Ana Ruiz", "she/her", "A1")
    assert not user.has_usable_password()
    address = EmailAddress.objects.get(user=user)
    assert (address.email, address.primary, address.verified) == ("ana@example.edu", True, False)
    assert Membership.objects.get(user=user, center=center).is_active
    assert (outcome.added, outcome.turned_on, outcome.already) == (1, 0, 0)
    [created] = audit(AuditAction.ACCOUNT_CREATED)
    assert created.center is None and created.target_id == str(user.pk)
    [added] = audit(AuditAction.MEMBERSHIP_ADDED)
    assert added.center == center
    assert added.details["via"] == "import" and added.details["role"] == Role.STUDENT


def test_an_existing_account_is_never_changed(rf, center, director) -> None:
    existing = UserFactory(email="Sam@example.edu", full_name="Sam Rivera", student_id="S9")
    request = rf.post("/")
    request.user = director
    [row] = read("email,name,student id\nsam@EXAMPLE.edu,Samuel R,S10\n").rows
    people.add_people(center, [row], request=request, via="import")
    existing.refresh_from_db()
    assert (existing.full_name, existing.student_id) == ("Sam Rivera", "S9")
    assert existing.check_password("correct-horse-battery-staple")
    assert User.objects.count() == 2  # Sam and the director
    assert Membership.objects.filter(user=existing, center=center, role=Role.STUDENT).exists()
    assert not audit(AuditAction.ACCOUNT_CREATED)


def test_roles_turned_off_come_back_and_roles_already_on_are_left(rf, center, director) -> None:
    off = MembershipFactory(center=center, role=Role.STUDENT, is_active=False)
    on = MembershipFactory(center=center, role=Role.STUDENT)
    request = rf.post("/")
    request.user = director
    reading = read(f"email,name\n{off.user.email},Off\n{on.user.email},On\n")
    outcome = people.add_people(center, reading.rows, request=request, via="import")
    assert (outcome.added, outcome.turned_on, outcome.already) == (0, 1, 1)
    off.refresh_from_db()
    assert off.is_active
    [changed] = audit(AuditAction.MEMBERSHIP_CHANGED)
    assert changed.target_id == str(off.pk) and changed.details["active"] is True
    assert list(outcome.welcomed) == [off.user_id]


# --- The import pages --------------------------------------------------------------


def test_a_director_previews_the_file_then_confirms_it(signed_in, center) -> None:
    MembershipFactory(user__email="here@example.edu", center=center, role=Role.STUDENT)
    text = (
        "email,name,role\n"
        "ana@example.edu,Ana Ruiz,\n"
        "sam@example.edu,Sam Lee,tutor\n"
        "here@example.edu,Already Here,\n"
        "broken,Broken,\n"
    )
    preview = upload(signed_in, center, text)
    page = preview.content.decode()
    assert preview.status_code == 200
    assert "2 roles will be added" in page
    assert "1 line is already in the center" in page
    assert "1 line has a problem and will be skipped" in page
    assert not User.objects.filter(email="ana@example.edu").exists()  # nothing yet

    done = confirm(signed_in, center)
    assert done.status_code == 302
    assert done["Location"] == reverse("centers:members", kwargs={"slug": center.slug})
    assert Membership.objects.filter(center=center, user__email="ana@example.edu").exists()
    assert Membership.objects.get(center=center, user__email="sam@example.edu").role == Role.TUTOR
    [imported] = audit(AuditAction.PEOPLE_IMPORTED)
    assert imported.details == {
        "rows": 3,
        "skipped": 1,
        "added": 2,
        "turned_on": 0,
        "already": 1,
    }
    assert SESSION_KEY not in signed_in.session
    # The same preview can't be confirmed twice.
    again = signed_in.post(
        reverse("centers:import", kwargs={"slug": center.slug}), {"confirm": "1", "key": "x"}
    )
    assert again.status_code == 302 and again["Location"].endswith("/members/import/")
    assert len(audit(AuditAction.PEOPLE_IMPORTED)) == 1


def test_a_preview_is_confirmed_only_in_its_own_center(signed_in, center, director) -> None:
    other = CenterFactory()
    MembershipFactory(user=director, center=other, role=Role.DIRECTOR)
    upload(signed_in, center, "email,name\nana@example.edu,Ana\n")
    key = signed_in.session[SESSION_KEY]["key"]
    response = signed_in.post(
        reverse("centers:import", kwargs={"slug": other.slug}), {"confirm": "1", "key": key}
    )
    assert response.status_code == 302
    assert not Membership.objects.filter(user__email="ana@example.edu").exists()


def test_a_bad_file_says_what_is_wrong(signed_in, center) -> None:
    response = upload(signed_in, center, "name\nAna\n")
    assert response.status_code == 200
    assert "one of them must be Email" in response.content.decode()
    assert SESSION_KEY not in signed_in.session


def test_nothing_is_added_if_an_account_changed_meanwhile(signed_in, center) -> None:
    upload(
        signed_in, center, "email,name,student id\nana@example.edu,Ana,A1\nsam@example.edu,Sam,\n"
    )
    UserFactory(email="someone@example.edu", student_id="A1")  # takes A1 after the preview
    response = confirm(signed_in, center)
    assert response.status_code == 302 and response["Location"].endswith("/members/import/")
    assert not User.objects.filter(email__in=["ana@example.edu", "sam@example.edu"]).exists()
    assert not audit(AuditAction.PEOPLE_IMPORTED)


def test_welcome_emails_go_only_when_asked_and_only_for_changes(
    signed_in, center, django_capture_on_commit_callbacks
) -> None:
    center.contact_email = "writing@example.edu"
    center.save()
    MembershipFactory(user__email="here@example.edu", center=center, role=Role.STUDENT)
    upload(
        signed_in,
        center,
        "email,name\nana@example.edu,Ana Ruiz\nhere@example.edu,Here\n",
        welcome="on",
    )
    with django_capture_on_commit_callbacks(execute=True):
        confirm(signed_in, center)
    [message] = mail.outbox
    assert message.to == ["ana@example.edu"]
    assert center.name in message.subject
    assert "as: Student" in message.body
    assert reverse("account_login") in message.body
    assert "Forgot your password?" in message.body
    assert message.reply_to == ["writing@example.edu"]
    assert not re.search(r"/password/reset/key/|/confirm-email/", message.body)


def test_no_welcome_email_unless_asked(
    signed_in, center, django_capture_on_commit_callbacks
) -> None:
    upload(signed_in, center, "email,name\nana@example.edu,Ana Ruiz\n")
    with django_capture_on_commit_callbacks(execute=True):
        confirm(signed_in, center)
    assert mail.outbox == []


def test_the_welcome_email_points_to_single_sign_on_when_there_is_one(sso, center) -> None:
    from sympraxi.centers.people_views import welcome_message

    user = UserFactory(full_name="Ana Ruiz")
    body = welcome_message(center, user, [Role.TUTOR], url_base="https://wc.example.edu").body
    assert "Use your college account to sign in." in body
    assert "https://wc.example.edu/accounts/login/" in body


# --- One at a time -----------------------------------------------------------------


def test_adding_one_person(signed_in, center) -> None:
    url = reverse("centers:person_add", kwargs={"slug": center.slug})
    response = signed_in.post(
        url, {"email": "ana@example.edu", "full_name": "Ana Ruiz", "role": Role.TUTOR}
    )
    assert response.status_code == 302
    assert Membership.objects.get(user__email="ana@example.edu", center=center).role == Role.TUTOR
    again = signed_in.post(
        url, {"email": "ANA@example.edu", "full_name": "Ana Ruiz", "role": Role.TUTOR}, follow=True
    )
    assert "already has that role here" in again.content.decode()
    assert Membership.objects.filter(user__email="ana@example.edu").count() == 1


def test_adding_one_person_checks_the_student_id(signed_in, center) -> None:
    UserFactory(student_id="A1")
    response = signed_in.post(
        reverse("centers:person_add", kwargs={"slug": center.slug}),
        {"email": "ana@example.edu", "full_name": "Ana", "student_id": "A1", "role": "student"},
    )
    assert response.status_code == 200
    assert "Another account already has this student ID" in response.content.decode()


def test_a_persons_roles_turn_on_and_off_and_can_be_added(signed_in, center) -> None:
    membership = MembershipFactory(center=center, role=Role.STUDENT)
    url = reverse("centers:person", kwargs={"slug": center.slug, "membership_id": membership.pk})
    assert signed_in.get(url).status_code == 200
    signed_in.post(url, {"membership": str(membership.pk), "active": "0"})
    membership.refresh_from_db()
    assert not membership.is_active
    signed_in.post(url, {"membership": str(membership.pk), "active": "1"})
    membership.refresh_from_db()
    assert membership.is_active
    signed_in.post(url, {"add_role": Role.TUTOR})
    assert Membership.objects.filter(user=membership.user, center=center, role=Role.TUTOR).exists()
    assert [e.details["via"] for e in audit(AuditAction.MEMBERSHIP_CHANGED)] == [
        "person_page",
        "person_page",
    ]


def test_a_director_cant_turn_off_their_own_director_role(signed_in, center, director) -> None:
    mine = Membership.objects.get(user=director, center=center)
    url = reverse("centers:person", kwargs={"slug": center.slug, "membership_id": mine.pk})
    response = signed_in.post(url, {"membership": str(mine.pk), "active": "0"}, follow=True)
    assert "turn off your own director role" in response.content.decode()
    mine.refresh_from_db()
    assert mine.is_active


def test_a_membership_from_another_center_cant_be_changed_here(signed_in, center) -> None:
    here = MembershipFactory(center=center, role=Role.STUDENT)
    elsewhere = MembershipFactory(user=here.user, role=Role.STUDENT)
    url = reverse("centers:person", kwargs={"slug": center.slug, "membership_id": here.pk})
    signed_in.post(url, {"membership": str(elsewhere.pk), "active": "0"})
    elsewhere.refresh_from_db()
    assert elsewhere.is_active


# --- Turning a role off for everyone -----------------------------------------------


def test_turning_off_every_student_role(signed_in, center, director) -> None:
    world = build_world(center)  # its student has an appointment tomorrow
    MembershipFactory(user=director, center=center, role=Role.STUDENT)  # the director's own
    MembershipFactory(center=center, role=Role.STUDENT)
    url = reverse("centers:roles_off", kwargs={"slug": center.slug})
    review = signed_in.post(url, {"role": Role.STUDENT})
    page = review.content.decode()
    assert "This turns off 2 student roles. Yours stays on." in page
    assert "1 of them has an appointment still to come" in page
    assert Membership.objects.filter(center=center, role=Role.STUDENT, is_active=True).count() == 3

    signed_in.post(url, {"role": Role.STUDENT, "confirm": "1"})
    active = Membership.objects.filter(center=center, role=Role.STUDENT, is_active=True)
    assert [m.user for m in active] == [director]
    assert not Membership.objects.get(user=world.student, role=Role.STUDENT).is_active
    assert Membership.objects.get(user=world.tutor, role=Role.TUTOR).is_active
    [summary] = audit(AuditAction.ROLES_TURNED_OFF)
    assert summary.details == {"role": "student", "count": 2}
    assert len(audit(AuditAction.MEMBERSHIP_CHANGED)) == 2


# --- Who may ------------------------------------------------------------------------


def test_front_desk_staff_see_people_but_cant_change_them(client, center) -> None:
    desk = UserFactory()
    MembershipFactory(user=desk, center=center, role=Role.FRONT_DESK)
    give_mfa(desk)
    client.force_login(desk)
    page = client.get(reverse("centers:members", kwargs={"slug": center.slug}))
    assert page.status_code == 200
    assert "Add a person" not in page.content.decode()
    file = SimpleUploadedFile("p.csv", b"email,name\nana@example.edu,Ana\n")
    response = client.post(
        reverse("centers:import", kwargs={"slug": center.slug}), {"file": file, "role": "student"}
    )
    assert response.status_code == 403
    assert not User.objects.filter(email="ana@example.edu").exists()


def test_the_people_page_shows_directors_their_tools(signed_in, center) -> None:
    page = signed_in.get(reverse("centers:members", kwargs={"slug": center.slug}))
    text = page.content.decode()
    for label in ("Add a person", "Add people from a CSV file", "Turn off a role for everyone"):
        assert label in text


# --- Claiming the account ----------------------------------------------------------


def test_someone_added_signs_in_with_single_sign_on(client, sso, signed_in, center) -> None:
    upload(signed_in, center, "email,name\nsam@example.edu,Sam Rivera\n")
    confirm(signed_in, center)
    added = User.objects.get(email="sam@example.edu")
    client.logout()
    sign_in(client, sso, "entra", entra_claims())
    assert client.session.get("_auth_user_id") == str(added.pk)
    assert User.objects.filter(email__iexact="sam@example.edu").count() == 1
    # The provider vouched for the address, so it isn't confirmed by email as well.
    assert EmailAddress.objects.get(user=added).verified


def test_someone_added_can_set_a_password(client, signed_in, center) -> None:
    upload(signed_in, center, "email,name\nsam@example.edu,Sam Rivera\n")
    confirm(signed_in, center)
    client.logout()
    mail.outbox.clear()
    client.post(reverse("account_reset_password"), {"email": "sam@example.edu"})
    [message] = mail.outbox
    assert message.to == ["sam@example.edu"]
    assert "/accounts/password/reset/key/" in message.body
