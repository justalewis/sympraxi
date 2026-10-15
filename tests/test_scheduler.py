"""The scheduler process and its health check."""

import threading
import time as clock
from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.utils import timezone

from sympraxi import healthcheck
from sympraxi.centers.models import Role
from sympraxi.scheduling import reminders, scheduler
from sympraxi.scheduling.models import Appointment, Modality, Participant
from tests.factories import CenterFactory
from tests.scheduling_world import assign, make_member, make_schedule


@pytest.fixture
def heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("SYMPRAXI_SCHEDULER_HEARTBEAT", str(path))
    return path


def appointment(assignment, student, start) -> Appointment:
    a = Appointment.objects.create(
        schedule=assignment.schedule,
        assignment=assignment,
        tutor=assignment.tutor,
        starts_at=start,
        ends_at=start + timedelta(minutes=30),
        blocked_until=start + timedelta(minutes=30),
        modality=Modality.IN_PERSON,
    )
    Participant.objects.create(appointment=a, student=student)
    return a


@pytest.mark.django_db
def test_one_sweep_sends_reminders_flags_no_shows_and_writes_a_heartbeat(
    heartbeat, settings
) -> None:
    settings.SYMPRAXI_BASE_URL = "https://writing.example.edu"
    center = CenterFactory()
    today = timezone.localdate()
    schedule = make_schedule(center, today - timedelta(days=3), today + timedelta(days=3))
    assignment = assign(schedule)
    student = make_member(center, Role.STUDENT)
    now = timezone.now().replace(microsecond=0)
    # Its hour-before reminder fell due a minute ago; it was booked long before.
    upcoming = appointment(assignment, student, now + timedelta(minutes=59))
    Appointment.objects.filter(pk=upcoming.pk).update(updated_at=now - timedelta(days=2))
    Participant.objects.filter(appointment=upcoming).update(added_at=now - timedelta(days=2))
    # Started 30 minutes ago and no one has marked the student.
    missed = appointment(assignment, student, now - timedelta(minutes=30))

    call_command("scheduler", "--once")

    assert [m.to for m in mail.outbox] == [[student.email]]
    assert "https://writing.example.edu/centers/" in mail.outbox[0].body
    assert Participant.objects.get(appointment=missed).is_possible_no_show
    assert healthcheck.scheduler_is_healthy()
    assert healthcheck.main(["healthcheck", "scheduler"]) == 0


@pytest.mark.django_db
def test_it_will_not_run_without_a_base_url(settings) -> None:
    settings.SYMPRAXI_BASE_URL = ""
    with pytest.raises(CommandError, match="SYMPRAXI_BASE_URL"):
        call_command("scheduler", "--once")


@pytest.mark.django_db
def test_a_failed_sweep_writes_no_heartbeat(heartbeat, monkeypatch) -> None:
    def broken(**kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(reminders, "send_due_reminders", broken)
    scheduler.run(base_url="https://writing.example.edu", stop=threading.Event(), once=True)
    assert not heartbeat.exists()
    assert healthcheck.main(["healthcheck", "scheduler"]) == 1


def test_the_loop_sweeps_each_minute_until_stopped(heartbeat, monkeypatch) -> None:
    sweeps: list[str] = []
    stop = threading.Event()

    def fake_sweep(*, base_url: str, now=None):
        sweeps.append(base_url)
        if len(sweeps) == 3:
            stop.set()
        return scheduler.Sweep(reminders=0, flagged=0)

    monkeypatch.setattr(scheduler, "sweep", fake_sweep)
    monkeypatch.setattr(scheduler, "seconds_to_next_minute", lambda: 0.01)
    worker = threading.Thread(
        target=scheduler.run, kwargs={"base_url": "https://x.example", "stop": stop}
    )
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert sweeps == ["https://x.example"] * 3
    assert healthcheck.scheduler_is_healthy()


def test_sweeps_line_up_with_the_minute() -> None:
    assert scheduler.seconds_to_next_minute(120.25) == pytest.approx(59.75)
    assert scheduler.seconds_to_next_minute(179.0) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("content", "healthy"),
    [
        (None, False),  # never written
        ("not a number", False),
        ("{now}", True),
        ("{fresh}", True),
        ("{stale}", False),  # more than three minutes old
        ("{future}", False),  # a clock gone wrong
    ],
)
def test_the_health_check_wants_a_recent_heartbeat(heartbeat, content, healthy) -> None:
    now = clock.time()
    if content is not None:
        heartbeat.write_text(
            content.format(now=now, fresh=now - 170, stale=now - 190, future=now + 600)
        )
    assert healthcheck.scheduler_is_healthy(now=now) is healthy


def test_the_heartbeat_is_replaced_whole(heartbeat) -> None:
    healthcheck.write_heartbeat(now=1_000)
    healthcheck.write_heartbeat(now=2_000)
    assert heartbeat.read_text() == "2000\n"
    assert list(heartbeat.parent.iterdir()) == [heartbeat]
