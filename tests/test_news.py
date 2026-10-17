"""Announcements and mass email: who sees and gets what, the review step, the log,
the opt-out, and sending each copy once."""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse

from sympraxi.centers.models import Role
from sympraxi.notify import news
from sympraxi.notify.models import Announcement, Audience, MassEmail, MassEmailRecipient
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.scheduling.models import Participant
from tests.factories import MembershipFactory
from tests.news_world import announcement
from tests.scheduling_world import build_world, make_member
from tests.staff_world import today

pytestmark = pytest.mark.django_db


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR, full_name="Dana Director")


def center_page(client, center) -> str:
    return client.get(reverse("centers:detail", kwargs={"slug": center.slug})).content.decode()


# --- Announcements ------------------------------------------------------------------


def test_announcements_show_to_their_audience_while_current(client, center) -> None:
    student = make_member(center, Role.STUDENT)
    tutor = make_member(center, Role.TUTOR)
    announcement(center, Audience.EVERYONE, title="For everyone")
    announcement(center, Audience.STUDENTS, title="For students")
    announcement(center, Audience.STAFF, title="For staff")
    announcement(
        center,
        title="Over",
        starts_on=today(center) - timedelta(days=9),
        ends_on=today(center) - timedelta(days=2),
    )
    assert {a.title for a in news.showing(center, student)} == {"For everyone", "For students"}
    assert {a.title for a in news.showing(center, tutor)} == {"For everyone", "For staff"}
    client.force_login(student)
    page = center_page(client, center)
    assert "For students" in page and "For staff" not in page and "Over" not in page


def test_announcements_are_plain_text(client, center) -> None:
    student = make_member(center, Role.STUDENT)
    announcement(center, body="<script>alert(1)</script>\nhttps://wc.example.edu/hours")
    client.force_login(student)
    page = center_page(client, center)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
    assert 'href="https://wc.example.edu/hours"' in page


def test_students_see_theirs_on_the_schedules_page(client, center) -> None:
    world = build_world(center)
    announcement(center, Audience.STUDENTS, title="Book early this week")
    announcement(center, Audience.STAFF, title="Staff meeting Friday")
    client.force_login(world.student)
    page = client.get(
        reverse("scheduling:schedules", kwargs={"slug": center.slug})
    ).content.decode()
    assert "Book early this week" in page and "Staff meeting Friday" not in page


def test_a_director_posts_changes_and_deletes_an_announcement(client, center, director) -> None:
    client.force_login(director)
    day = today(center)
    data = {
        "title": "Closed Monday",
        "body": "For the holiday.",
        "audience": Audience.EVERYONE,
        "starts_on": day.isoformat(),
        "ends_on": (day + timedelta(days=3)).isoformat(),
    }
    client.post(reverse("news:announcement_create", kwargs={"slug": center.slug}), data)
    posted = Announcement.objects.get()
    assert posted.created_by == director
    url = reverse(
        "news:announcement_edit", kwargs={"slug": center.slug, "announcement_id": posted.pk}
    )
    client.post(url, {**data, "title": "Closed Monday and Tuesday"})
    posted.refresh_from_db()
    assert posted.title == "Closed Monday and Tuesday"
    client.post(url, {"delete": "1"})
    assert not Announcement.objects.exists()


def test_an_announcement_ends_after_it_starts(client, center, director) -> None:
    client.force_login(director)
    day = today(center)
    response = client.post(
        reverse("news:announcement_create", kwargs={"slug": center.slug}),
        {
            "title": "Backwards",
            "body": "x",
            "audience": Audience.EVERYONE,
            "starts_on": day.isoformat(),
            "ends_on": (day - timedelta(days=1)).isoformat(),
        },
    )
    assert response.status_code == 200
    assert not Announcement.objects.exists()


# --- Mass email ---------------------------------------------------------------------


def compose(**values: str) -> dict[str, str]:
    return {"group": "students", "subject": "Workshop", "body": "Thursday at 3.", **values}


def mass_email_url(center) -> str:
    return reverse("news:mass_email", kwargs={"slug": center.slug})


def test_the_review_counts_who_it_reaches_and_sends_nothing(client, center, director) -> None:
    make_member(center, Role.STUDENT)
    quiet = make_member(center, Role.STUDENT)
    quiet.center_news_emails = False
    quiet.save()
    make_member(center, Role.TUTOR)
    client.force_login(director)
    page = client.post(mass_email_url(center), compose(review="1")).content.decode()
    assert "1 person" in page
    assert "1 more student turned off center news" in page
    assert not MassEmail.objects.exists() and mail.outbox == []


def test_sending_gives_each_person_their_own_copy(
    client, center, director, django_capture_on_commit_callbacks
) -> None:
    center.contact_email = "writing@example.edu"
    center.save()
    first = make_member(center, Role.STUDENT)
    second = make_member(center, Role.STUDENT)
    client.force_login(director)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(mass_email_url(center), compose(send="1"))
    mass = MassEmail.objects.get()
    assert response.status_code == 302
    assert sorted(m.to[0] for m in mail.outbox) == sorted([first.email, second.email])
    for message in mail.outbox:
        assert len(message.to) == 1 and not message.cc and not message.bcc
        assert message.subject.endswith("Workshop")
        assert message.reply_to == ["writing@example.edu"]
        assert "turn off center news" in message.body
    assert set(mass.recipients.values_list("user", flat=True)) == {first.pk, second.pk}
    assert not mass.recipients.filter(queued_at__isnull=True).exists()
    [event] = AuditEvent.objects.filter(action=AuditAction.MASS_EMAIL_SENT)
    assert event.details["recipients"] == 2
    assert "Thursday" not in str(event.details)


def test_staff_get_staff_email_whatever_they_chose(
    client, center, director, django_capture_on_commit_callbacks
) -> None:
    tutor = make_member(center, Role.TUTOR)
    tutor.center_news_emails = False
    tutor.save()
    client.force_login(director)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(mass_email_url(center), compose(group="tutors", send="1"))
    [message] = mail.outbox
    assert message.to == [tutor.email]
    assert "turn off center news" not in message.body


def test_students_with_appointments_in_a_span_of_days(client, center, director) -> None:
    world = build_world(center)  # its student has an appointment tomorrow
    make_member(center, Role.STUDENT)  # no appointment
    client.force_login(director)
    day = today(center)
    reach = news.Reach("students", first=day, last=day + timedelta(days=2))
    recipients, _skipped = reach.split(center)
    assert list(recipients) == [world.student]
    Participant.objects.filter(student=world.student).update(
        canceled_at=world.appointment.starts_at
    )
    assert not reach.split(center)[0].exists()


def test_days_only_narrow_down_students(client, center, director) -> None:
    client.force_login(director)
    day = today(center).isoformat()
    response = client.post(
        mass_email_url(center), compose(group="tutors", first=day, last=day, review="1")
    )
    assert "only narrow down students" in response.content.decode()


def test_a_test_copy_goes_only_to_the_director(client, center, director) -> None:
    make_member(center, Role.STUDENT)
    client.force_login(director)
    client.post(mass_email_url(center), compose(test="1"))
    [message] = mail.outbox
    assert message.to == [director.email]
    assert not MassEmail.objects.exists()


def test_each_copy_is_queued_once(center, director) -> None:
    student = make_member(center, Role.STUDENT)
    mass = MassEmail.objects.create(center=center, subject="S", body="B", audience="Students")
    MassEmailRecipient.objects.create(mass_email=mass, user=student)
    assert news.queue_copies(str(mass.pk), "https://wc.example.edu") == 1
    assert news.queue_copies(str(mass.pk), "https://wc.example.edu") == 0
    assert len(mail.outbox) == 1


def test_the_log_shows_who_it_went_to(client, center, director) -> None:
    student = make_member(center, Role.STUDENT, full_name="Sam Student")
    mass = MassEmail.objects.create(center=center, subject="Hours", body="B", audience="Students")
    MassEmailRecipient.objects.create(mass_email=mass, user=student)
    client.force_login(director)
    log = client.get(reverse("news:mass_email_log", kwargs={"slug": center.slug}))
    assert "Hours" in log.content.decode()
    detail = client.get(
        reverse("news:mass_email_detail", kwargs={"slug": center.slug, "mass_email_id": mass.pk})
    )
    assert "Sam Student" in detail.content.decode()


def test_students_turn_off_center_news_on_their_email_settings(client, center) -> None:
    student = make_member(center, Role.STUDENT)
    client.force_login(student)
    client.post(
        reverse("accounts:reminders"),
        {"reminders_for_appointments": "on", "survey_emails": "on"},
    )
    student.refresh_from_db()
    assert not student.center_news_emails


def test_only_directors_send_mass_email(client, center) -> None:
    desk = make_member(center, Role.FRONT_DESK)
    client.force_login(desk)
    assert client.get(mass_email_url(center)).status_code == 403
    MembershipFactory(user=desk, center=center, role=Role.DIRECTOR)
    assert client.get(mass_email_url(center)).status_code == 200
