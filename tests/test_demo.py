"""Demo mode: the role picker, the banner, the richer sample data and the safety guards."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse

from sympraxi.accounts.models import User
from sympraxi.alerts.models import Alert, VisitShare
from sympraxi.centers.models import Center
from sympraxi.integrations import webhooks
from sympraxi.scheduling.models import WaitingEntry
from sympraxi.staff.models import TimeEntry
from sympraxi.training.models import Cohort, Module
from sympraxi.workshops.models import Registration, Workshop
from tests.factories import UserFactory

pytestmark = pytest.mark.django_db
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def demo(settings):
    settings.SYMPRAXI_DEMO = True
    settings.SYMPRAXI_SIGNUP_OPEN = False
    return settings


@pytest.fixture
def seeded(demo):
    """A few sample people, enough for the picker. The full world has its own test."""
    from sympraxi.centers.models import Center, Membership, Role

    center = Center.objects.create(name="Demo Writing Center", slug="demo")
    people = (
        ("student@demo.test", "Sam Student", Role.STUDENT),
        ("tutor@demo.test", "Tomás Tutor", Role.TUTOR),
        ("frontdesk@demo.test", "Felix Frontdesk", Role.FRONT_DESK),
        ("director@demo.test", "Dana Director", Role.DIRECTOR),
    )
    for email, name, role in people:
        user = UserFactory(email=email, full_name=name)
        Membership.objects.create(user=user, center=center, role=role)
    return User.objects.get(email="student@demo.test")


# --- Off by default ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["core:demo", "core:demo_sign_in"])
def test_the_demo_pages_do_not_exist_on_a_normal_site(client, name) -> None:
    assert client.get(reverse(name)).status_code == 404


def test_the_picker_signs_nobody_in_on_a_normal_site(client) -> None:
    person = UserFactory(email="sam@demo.test")
    assert client.post(reverse("core:demo_sign_in"), {"person": str(person.pk)}).status_code == 404
    assert "_auth_user_id" not in client.session


def test_a_normal_site_has_no_banner_and_can_be_indexed(client) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert "made-up people" not in html and "noindex" not in html


def test_seeding_is_refused_unless_this_is_development_or_a_demo(settings) -> None:
    settings.DEBUG = False
    settings.SYMPRAXI_DEMO = False
    with pytest.raises(CommandError, match="SYMPRAXI_DEMO"):
        call_command("seed_demo")


# --- The picker --------------------------------------------------------------------------------


def test_the_picker_lists_the_sample_people_by_role(client, seeded) -> None:
    html = client.get(reverse("core:demo")).content.decode()
    for heading in ("Students", "Tutors", "Front desk", "Directors"):
        assert heading in html
    assert "Sign in as Sam Student" in html and "Sign in as Dana Director" in html


def test_a_visitor_signs_in_as_a_sample_person(client, seeded) -> None:
    response = client.post(reverse("core:demo_sign_in"), {"person": str(seeded.pk)})
    assert response.status_code == 302 and response["Location"] == "/centers/demo/"
    assert client.session["_auth_user_id"] == str(seeded.pk)
    assert client.get("/centers/demo/").status_code == 200


def test_sample_staff_are_not_stopped_at_two_factor_setup(client, seeded) -> None:
    tutor = User.objects.get(email="tutor@demo.test")
    client.post(reverse("core:demo_sign_in"), {"person": str(tutor.pk)})
    response = client.get("/centers/demo/")
    assert response.status_code == 200


@pytest.mark.parametrize("make", ["real_person", "inactive_sample", "nobody"])
def test_only_active_sample_people_can_be_picked(client, seeded, make) -> None:
    if make == "real_person":
        target = str(UserFactory(email="someone@college.edu").pk)
    elif make == "inactive_sample":
        seeded.is_active = False
        seeded.save()
        target = str(seeded.pk)
    else:
        target = "not-an-id"
    assert client.post(reverse("core:demo_sign_in"), {"person": target}).status_code == 404
    assert "_auth_user_id" not in client.session


def test_the_picker_takes_only_a_post(client, seeded) -> None:
    assert client.get(reverse("core:demo_sign_in")).status_code == 405


def test_a_real_staff_account_still_needs_two_factor(client, demo) -> None:
    from sympraxi.accounts.middleware import needs_mfa_setup
    from sympraxi.centers.models import Membership, Role
    from tests.factories import CenterFactory

    staff = UserFactory(email="admin@college.edu")
    Membership.objects.create(user=staff, center=CenterFactory(), role=Role.DIRECTOR)
    assert needs_mfa_setup(staff) is True


# --- What visitors see -------------------------------------------------------------------------


def test_every_page_says_it_is_a_demo_and_asks_not_to_be_indexed(client, demo) -> None:
    html = client.get(reverse("account_login")).content.decode()
    assert "This is a demo with made-up people" in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert reverse("core:demo") in html


def test_sign_up_is_closed_in_a_demo(client, demo) -> None:
    response = client.get(reverse("account_signup"))
    assert 'name="password1"' not in response.content.decode()


# --- Nothing leaves the server ---------------------------------------------------------------


def test_a_demo_calls_no_webhooks(demo, monkeypatch) -> None:
    from sympraxi.privacy.audit import AuditAction

    class Event:
        center_id = 1
        action = next(iter(webhooks.ACTION_TO_EVENT))

    queued: list[object] = []
    monkeypatch.setattr(webhooks.transaction, "on_commit", queued.append)
    webhooks.hook(Event())  # type: ignore[arg-type]
    assert queued == [] and AuditAction  # nothing was queued


# --- The sample data ---------------------------------------------------------------------------


def test_the_full_demo_world_builds_and_every_tour_link_opens(client, demo) -> None:
    """One real run of the seed (about 20 seconds), then everything checked against it."""
    from django.utils import timezone

    from sympraxi.alerts.models import AlertStatus
    from sympraxi.centers.models import Membership, Role
    from sympraxi.core.demo_world import tour
    from sympraxi.records.models import RecordStatus, SessionRecord
    from sympraxi.scheduling.models import Appointment, Participant

    call_command("seed_demo", stdout=io.StringIO())
    center = Center.objects.get(slug="demo")
    now = timezone.now()

    # A center with history, not a handful of rows.
    assert Membership.objects.filter(center=center, role=Role.STUDENT).count() >= 24
    assert Membership.objects.filter(center=center, role=Role.TUTOR).count() >= 6
    assert Membership.objects.filter(center=center, role=Role.FRONT_DESK).count() >= 2
    assert Membership.objects.filter(center=center, role=Role.DIRECTOR).count() >= 2
    assert Appointment.objects.filter(schedule__center=center, starts_at__lt=now).count() >= 100
    assert Appointment.objects.filter(schedule__center=center, starts_at__gt=now).count() >= 15
    assert SessionRecord.objects.filter(center=center, status=RecordStatus.FINAL).count() >= 80
    assert Workshop.objects.filter(center=center).count() >= 6
    assert Registration.objects.filter(workshop__center=center).count() >= 30
    assert Module.objects.filter(center=center, is_published=True).count() >= 3
    assert Cohort.objects.filter(center=center).count() == 1
    assert Alert.objects.filter(center=center).count() >= 5
    assert Alert.objects.filter(center=center, status=AlertStatus.CLOSED).exists()
    assert VisitShare.objects.filter(center=center).count() >= 4
    assert TimeEntry.objects.filter(center=center).count() >= 150
    assert WaitingEntry.objects.count() >= 3
    center.refresh_from_db()
    assert center.visit_sharing_enabled and center.alert_email and center.sms_enabled

    # Sam leads: the student with the most history, and a next appointment booked.
    sam = User.objects.get(email="student@demo.test")
    assert SessionRecord.objects.filter(student=sam, status=RecordStatus.FINAL).count() >= 8
    assert Participant.objects.filter(student=sam, appointment__starts_at__gt=now).exists()

    # Dates are relative to today, so a nightly reset always looks current.
    assert Workshop.objects.filter(starts_at__gt=now).count() >= 2
    assert Workshop.objects.filter(starts_at__lt=now).count() >= 2

    # Running the seed again adds nothing.
    before = (
        Appointment.objects.count(),
        Alert.objects.count(),
        TimeEntry.objects.count(),
        Workshop.objects.count(),
    )
    call_command("seed_demo", stdout=io.StringIO())
    assert before == (
        Appointment.objects.count(),
        Alert.objects.count(),
        TimeEntry.objects.count(),
        Workshop.objects.count(),
    )

    # The front page makes the case, lists every role, and leads with Sam.
    html = client.get(reverse("core:demo")).content.decode()
    assert "keep their own data" in html and "Where it stands" in html
    assert html.index("Sign in as Sam Student") < html.index("Sign in as Dana Director")
    for heading in ("Students", "Tutors", "Front desk", "Directors"):
        assert heading in html
    assert "Run an hours report" in html and "Book an appointment" in html

    # Every link on the tour opens for the person it is written for.
    for entry in tour.TOURS:
        person = User.objects.get(email=entry.persona)
        client.post(reverse("core:demo_sign_in"), {"person": str(person.pk)})
        for link in entry.links:
            url = (
                reverse(link.name)
                if link.name == "sms:settings"
                else reverse(link.name, kwargs={"slug": "demo"})
            )
            response = client.get(url)
            assert response.status_code == 200, (
                f"{entry.key}: {link.name} gave {response.status_code}"
            )
        client.logout()

    # Each role's front page has real numbers and tables, not only links.
    director = User.objects.get(email="director@demo.test")
    client.force_login(director)
    page = client.get("/centers/demo/").content.decode()
    assert "At a glance" in page and "Open early alerts" in page
    client.force_login(sam)
    page = client.get("/centers/demo/").content.decode()
    assert "Coming up" in page and "From your last session" in page


def test_the_front_page_of_a_demo_site_is_the_demo_page(client, demo) -> None:
    response = client.get("/")
    assert response.status_code == 302 and response["Location"] == reverse("core:demo")


# --- Videos and the FAQ ---------------------------------------------------------------------


def test_the_video_scripts_and_the_faq_agree_with_each_other() -> None:
    from sympraxi.core.demo_world import faq, videos

    slugs = [s.slug for s in videos.SCENARIOS]
    assert len(slugs) == len(set(slugs)), "two scripts share a slug"
    ids = [f.id for f in faq.FAQS]
    assert len(ids) == len(set(ids)), "two FAQ entries share an id"
    groups = {key for key, _ in faq.GROUPS}
    audiences = {key for key, _ in videos.AUDIENCES}
    for entry in faq.FAQS:
        assert entry.group in groups
        assert entry.answer and all(p.strip() for p in entry.answer)
        for slug in entry.videos:
            assert slug in slugs, f"{entry.id} names a video that doesn't exist: {slug}"
    for scenario in videos.SCENARIOS:
        assert scenario.audience in audiences
        assert scenario.persona in videos.PERSONA_NAMES
        assert scenario.steps and any(step.caption for step in scenario.steps)
        for faq_id in scenario.faq:
            assert faq_id in ids, f"{scenario.slug} names a FAQ entry that doesn't exist: {faq_id}"
        assert all(
            step.do
            in {
                "goto",
                "click",
                "fill",
                "select",
                "check",
                "highlight",
                "scroll",
                "wait",
                "caption",
            }
            for step in scenario.steps
        )


@pytest.fixture
def recorded(demo, seeded, tmp_path, monkeypatch):
    """A manifest as the recorder writes it, for two of the scripts."""
    import json

    from sympraxi.core.demo_world import manifest

    rows = [
        {
            "slug": "book-an-appointment",
            "title": "Book an appointment",
            "audience": "student",
            "persona": "student@demo.test",
            "summary": "Choose a schedule, a day and a tutor.",
            "duration": 47.2,
            "try_url": "/centers/demo/schedules/",
            "faq": ["how-do-i-book"],
            "steps": [
                {"text": "Sam wants a tutor.", "start": 1.0},
                {"text": "He books it.", "start": 20.5},
            ],
        },
        {
            "slug": "run-an-hours-report",
            "title": "Run an hours report",
            "audience": "director",
            "persona": "director@demo.test",
            "summary": "Hours offered and booked.",
            "duration": 38.0,
            "try_url": "/centers/demo/reports/hours/",
            "faq": ["reports"],
            "steps": [{"text": "Dana opens the report.", "start": 1.0}],
        },
    ]
    path = tmp_path / "video_manifest.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(manifest, "MANIFEST", path)
    return rows


def test_with_no_videos_recorded_the_front_page_still_works_and_has_the_faq(
    client, seeded, tmp_path, monkeypatch
) -> None:
    from sympraxi.core.demo_world import manifest

    monkeypatch.setattr(manifest, "MANIFEST", tmp_path / "missing.json")
    html = client.get(reverse("core:demo")).content.decode()
    assert "Watch it first" not in html
    assert "Questions people ask" in html and "How do I book an appointment?" in html
    assert 'id="faq-how-do-i-book"' in html


def test_the_front_page_lists_the_videos_by_audience(client, recorded) -> None:
    html = client.get(reverse("core:demo")).content.decode()
    assert "Watch it first" in html and "For students" in html and "For directors" in html
    assert reverse("core:demo_video", args=["book-an-appointment"]) in html
    assert "(0:47)" in html


def test_a_faq_answer_with_a_video_shows_its_steps_and_a_link(client, recorded) -> None:
    html = client.get(reverse("core:demo")).content.decode()
    assert "Sam wants a tutor." in html and "He books it." in html
    assert "Watch: Book an appointment (0:47)" in html


def test_a_video_page_has_captions_a_transcript_and_a_way_to_try_it(client, recorded) -> None:
    html = client.get(reverse("core:demo_video", args=["book-an-appointment"])).content.decode()
    assert "<video" in html and 'kind="captions"' in html and ".vtt" in html
    assert "0:20" in html and "He books it." in html
    assert "Try it yourself as Sam Student" in html
    assert 'href="/centers/demo/schedules/"' in html
    assert "How do I book an appointment?" in html  # the related question


def test_a_video_that_was_not_recorded_is_a_404(client, recorded) -> None:
    assert client.get(reverse("core:demo_video", args=["write-up-a-session"])).status_code == 404
    assert client.get(reverse("core:demo_video", args=["no-such-video"])).status_code == 404


def test_video_pages_do_not_exist_on_a_normal_site(client, recorded, settings) -> None:
    settings.SYMPRAXI_DEMO = False
    assert client.get(reverse("core:demo_video", args=["book-an-appointment"])).status_code == 404


# --- The guards that stop a demo from reaching real people --------------------------------------


def _start(**env: str) -> subprocess.CompletedProcess[str]:
    """Load the real settings in a fresh process, the way the server does."""
    base = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "DJANGO_SETTINGS_MODULE": "sympraxi.settings",
        "DATABASE_URL": "postgres://unused@localhost/unused",
        "SYMPRAXI_SECRET_KEY": "demo-guard-test-key-demo-guard-test-key-123456",
        "SYMPRAXI_DEBUG": "false",
        "SYMPRAXI_ALLOWED_HOSTS": "demo.example.edu",
        "SYMPRAXI_DEMO": "true",
    }
    base.update(env)
    return subprocess.run(
        [sys.executable, "-c", "import django; django.setup(); print('started')"],
        env=base,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_demo_starts_with_nothing_that_reaches_real_people() -> None:
    result = _start()
    assert result.returncode == 0 and "started" in result.stdout, result.stderr[-600:]


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"SYMPRAXI_SMS_PROVIDER": "twilio"}, "SYMPRAXI_SMS_PROVIDER"),
        ({"SYMPRAXI_LINK_CHECK": "true"}, "SYMPRAXI_LINK_CHECK"),
        (
            {
                "SYMPRAXI_CAS_SERVER_URL": "https://cas.example.edu/cas",
                "SYMPRAXI_SIGNUP_EMAIL_DOMAINS": "example.edu",
            },
            "single sign-on",
        ),
    ],
    ids=["real texts", "link checker", "single sign-on"],
)
def test_a_demo_refuses_to_start_when_it_could_contact_or_sign_in_real_people(env, named) -> None:
    result = _start(**env)
    assert result.returncode != 0 and named in result.stderr


def test_a_demo_ignores_a_real_mail_relay() -> None:
    probe = (
        "import django; django.setup();"
        "from django.conf import settings;"
        "print(settings.MAILERS['delivery']['BACKEND'])"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "DJANGO_SETTINGS_MODULE": "sympraxi.settings",
        "DATABASE_URL": "postgres://unused@localhost/unused",
        "SYMPRAXI_SECRET_KEY": "demo-guard-test-key-demo-guard-test-key-123456",
        "SYMPRAXI_DEBUG": "false",
        "SYMPRAXI_ALLOWED_HOSTS": "demo.example.edu",
        "SYMPRAXI_DEMO": "true",
        "EMAIL_URL": "smtp+tls://user:pass@smtp.example.edu:587",
    }
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0 and "console" in done.stdout, done.stderr[-600:]


# --- The reset script ---------------------------------------------------------------------------


def test_the_reset_script_refuses_to_run_unless_the_site_is_a_demo() -> None:
    script = (ROOT / "deploy" / "reset-demo.sh").read_text(encoding="utf-8")
    guard = script.index("SYMPRAXI_DEMO=")
    first_destructive = script.index("dropdb")
    assert guard < first_destructive and "exit 1" in script[guard:first_destructive]
