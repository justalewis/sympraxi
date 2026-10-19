"""The shared document of a session, with the real editor service running.

Starts `collab/server.mjs` (Hocuspocus) with Node, points Sympraxi at it, and drives two
browsers, a tutor's and a student's. Needs `npm --prefix collab ci` and
`npm --prefix frontend run build` first; without them the tests are skipped.
"""

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from axe_playwright_python.sync_playwright import Axe
from django.urls import reverse

from sympraxi.online.models import RoomDocument, RoomVersion, VideoSettings
from tests.e2e.test_accessibility import AXE_OPTIONS, watch_for_csp_problems
from tests.factories import CenterFactory
from tests.online_world import starting_session
from tests.scheduling_world import build_world

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

ROOT = Path(__file__).resolve().parents[2]
SECRET = "an-e2e-secret-for-the-editor-service"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def collab(live_server, settings):
    """The editor service, running, and Sympraxi pointed at it."""
    node = shutil.which("node")
    bundle = ROOT / "sympraxi" / "static" / "vendor" / "room" / "room.js"
    if node is None or not (ROOT / "collab" / "node_modules").exists() or not bundle.exists():
        pytest.skip("needs Node, `npm --prefix collab ci` and `npm --prefix frontend run build`")
    port = free_port()
    env = {
        **os.environ,
        "SYMPRAXI_COLLAB_SECRET": SECRET,
        "SYMPRAXI_COLLAB_BACKEND": live_server.url,
        "SYMPRAXI_COLLAB_HOST": urlsplit(live_server.url).netloc,
        "SYMPRAXI_COLLAB_PORT": str(port),
        "SYMPRAXI_COLLAB_ADDRESS": "127.0.0.1",
    }
    process = subprocess.Popen(  # noqa: S603 (our own server, a fixed command)
        [node, str(ROOT / "collab" / "server.mjs")],
        cwd=ROOT / "collab",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            if process.poll() is not None:
                break
            time.sleep(0.2)
    else:
        process.terminate()
        pytest.fail("the editor service didn't start")
    if process.poll() is not None:
        pytest.fail("the editor service stopped: " + (process.stdout.read() or b"").decode())
    settings.SYMPRAXI_COLLAB_SECRET = SECRET
    settings.SYMPRAXI_COLLAB_URL = f"ws://localhost:{port}"
    yield port
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def wait_for(check, seconds: float = 20.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = check()
        if found:
            return found
        time.sleep(0.25)
    raise AssertionError("timed out waiting")


def open_room(browser, sign_in, world, appointment, user):
    context = browser.new_context()
    sign_in(context, user)
    page = context.new_page()
    problems = watch_for_csp_problems(page)
    url = reverse(
        "online:room", kwargs={"slug": world.center.slug, "appointment_id": appointment.pk}
    )
    return context, page, problems, url


def a_session():
    center = CenterFactory()
    world = build_world(center)
    VideoSettings.objects.create(center=center, room_is_on=True)
    return world, starting_session(world)


def test_a_tutor_and_a_student_write_and_chat_in_one_document(
    browser, live_server, sign_in, collab
) -> None:
    world, appointment = a_session()
    t_context, tutor, t_problems, url = open_room(browser, sign_in, world, appointment, world.tutor)
    s_context, student, s_problems, _ = open_room(
        browser, sign_in, world, appointment, world.student
    )
    tutor.goto(live_server.url + url)
    student.goto(live_server.url + url)
    for page in (tutor, student):
        page.wait_for_selector("text=Connected.")

    tutor.locator(".ProseMirror").click()
    tutor.keyboard.type("The plan for today.")
    student.locator(".ProseMirror").filter(has_text="The plan for today.").wait_for()
    student.locator(".ProseMirror").click()
    student.keyboard.press("Control+End")
    student.keyboard.type(" Add a source.")
    tutor.locator(".ProseMirror").filter(has_text="Add a source.").wait_for()

    student.fill("#room-chat-text", "Ready when you are")
    student.keyboard.press("Enter")
    tutor.wait_for_selector("#room-chat li:has-text('Ready when you are')")
    assert world.student.display_name in tutor.locator("#room-people").inner_text()

    tutor.get_by_role("button", name="Save a version").click()
    tutor.wait_for_selector("text=A version is saved.", timeout=15000)

    def saved():
        document = RoomDocument.objects.filter(appointment=appointment).first()
        return document and "Add a source." in str(document.content)

    wait_for(saved)
    assert RoomVersion.objects.filter(label=world.tutor.display_name).exists()
    assert RoomDocument.objects.get().chat[0]["text"] == "Ready when you are"

    # Everyone leaves; the service unloads the document; it comes back from Sympraxi.
    t_context.close()
    s_context.close()
    time.sleep(1)
    again_context, again, again_problems, _ = open_room(
        browser, sign_in, world, appointment, world.student
    )
    again.goto(live_server.url + url)
    again.locator(".ProseMirror").filter(has_text="Add a source.").wait_for()
    again_context.close()
    assert t_problems == [] and s_problems == [] and again_problems == []


def test_a_tampered_token_is_turned_away_and_nothing_is_kept(
    browser, live_server, sign_in, collab
) -> None:
    world, appointment = a_session()
    context, page, _problems, url = open_room(browser, sign_in, world, appointment, world.student)

    def tamper(route):
        response = route.fetch()
        body = response.text()
        marker = '"token": "'
        start = body.index(marker) + len(marker)
        end = body.index('"', start)
        token = body[start:end]
        flipped = token[:-1] + ("A" if token[-1] != "A" else "B")
        route.fulfill(response=response, body=body[:start] + flipped + body[end:])

    page.route("**/room/", tamper)
    page.goto(live_server.url + url)
    page.wait_for_selector("text=You're offline.")
    page.locator(".ProseMirror").click()
    page.keyboard.type("This should go nowhere.")
    time.sleep(4)  # longer than the service waits before saving
    assert not RoomDocument.objects.exists()
    assert "Connected." not in page.locator("#room-status").inner_text()
    context.close()


def test_the_room_with_text_and_chat_passes_axe(browser, live_server, sign_in, collab) -> None:
    world, appointment = a_session()
    context = browser.new_context(bypass_csp=True)
    sign_in(context, world.tutor)
    page = context.new_page()
    url = reverse(
        "online:room", kwargs={"slug": world.center.slug, "appointment_id": appointment.pk}
    )
    page.goto(live_server.url + url)
    page.wait_for_selector("text=Connected.")
    page.locator(".ProseMirror").click()
    page.keyboard.type("A shared line.")
    page.fill("#room-chat-text", "Hello")
    page.keyboard.press("Enter")
    page.wait_for_selector("#room-chat li")
    results = Axe().run(page, options=AXE_OPTIONS)
    context.close()
    assert results.violations_count == 0, results.generate_report()
