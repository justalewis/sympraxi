"""The link checker: the address guard, what each answer means, robots.txt and delays,
the weekly run, and the director's page. No test visits a real site."""

import http.server
import threading
from collections.abc import Iterator
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from sympraxi.centers.models import Role
from sympraxi.privacy.audit import AuditAction
from sympraxi.privacy.models import AuditEvent
from sympraxi.records import linkcheck, suggestions
from sympraxi.records.models import LinkCheckRun, LinkState, Resource
from tests.factories import CenterFactory
from tests.records_world import make_resource, make_topics, topics
from tests.scheduling_world import make_member

pytestmark = pytest.mark.django_db


# --- A scripted fetcher -------------------------------------------------------------------


class Fake:
    """Stands in for the network: answers by (method, url), and remembers every request."""

    def __init__(self, answers: dict[tuple[str, str], linkcheck.Response | Exception]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, str]] = []

    def request(self, method: str, url: str, *, max_body: int = 4096) -> linkcheck.Response:
        self.requests.append((method, url))
        answer = self.answers.get((method, url)) or self.answers.get(("*", url))
        if answer is None:
            return linkcheck.Response(404)
        if isinstance(answer, Exception):
            raise answer
        return answer


def ok(body: bytes = b"") -> linkcheck.Response:
    return linkcheck.Response(200, {}, body)


def moved(to: str, status: int = 301) -> linkcheck.Response:
    return linkcheck.Response(status, {"location": to})


REAL_CHECKER = linkcheck.Checker


def checker(answers, slept=None) -> linkcheck.Checker:
    slept = slept if slept is not None else []
    ticks = {"now": 0.0}

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        ticks["now"] += seconds

    return REAL_CHECKER(Fake(answers), sleep=sleep, clock=lambda: ticks["now"])  # type: ignore[arg-type]


def robots(text: str) -> linkcheck.Response:
    return ok(text.encode())


PAGE = "https://example.org/page"
ROBOTS = ("GET", "https://example.org/robots.txt")


# --- The address guard --------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "10.1.2.3",
        "127.0.0.1",
        "169.254.169.254",
        "192.168.0.9",
        "172.16.5.5",
        "::1",
        "fc00::1",
        "0.0.0.0",  # noqa: S104,
    ],
)
def test_private_addresses_are_not_public(address) -> None:
    assert not linkcheck.is_public(address)


def test_an_ordinary_address_is_public() -> None:
    assert linkcheck.is_public("93.184.216.34")


def test_a_name_with_any_private_address_is_refused(monkeypatch) -> None:
    def resolve(host, port, **kwargs):
        return [(2, 1, 6, "", ("93.184.216.34", port)), (2, 1, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(linkcheck.socket, "getaddrinfo", resolve)
    with pytest.raises(linkcheck.Unreachable, match="not a public"):
        linkcheck.public_address("example.org", 443)
    assert linkcheck.public_address("example.org", 443, allow_private=True) == "93.184.216.34"


def test_the_fetcher_never_connects_to_a_private_address(monkeypatch) -> None:
    called = []
    monkeypatch.setattr(
        linkcheck.socket, "create_connection", lambda *a, **k: called.append(a) or None
    )
    with pytest.raises(linkcheck.Unreachable, match="not a public"):
        linkcheck.PublicFetcher().request("HEAD", "http://127.0.0.1:9/")
    assert not called
    with pytest.raises(linkcheck.Unreachable, match="not a web address"):
        linkcheck.PublicFetcher().request("HEAD", "file:///etc/passwd")


# --- The real fetcher, against a local server ----------------------------------------------


class Handler(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []

    def _answer(self, with_body: bool) -> None:
        Handler.seen.append(self.headers.get("User-Agent", ""))
        if self.path == "/ok":
            self.send_response(200)
            self.send_header("Content-Length", "10000")
            self.end_headers()
            if with_body:
                self.wfile.write(b"x" * 10000)
        elif self.path == "/old":
            self.send_response(301)
            self.send_header("Location", "/new")
            self.end_headers()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def do_HEAD(self) -> None:
        self._answer(False)

    def do_GET(self) -> None:
        self._answer(True)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def local_site() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    Handler.seen = []
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_the_real_fetcher_asks_politely_and_reads_little(local_site) -> None:
    fetcher = linkcheck.PublicFetcher(allow_private=True)
    head = fetcher.request("HEAD", local_site + "/ok")
    assert head.status == 200 and head.body == b""
    got = fetcher.request("GET", local_site + "/ok", max_body=100)
    assert got.status == 200 and len(got.body) == 100
    assert fetcher.request("GET", local_site + "/missing").status == 404
    assert fetcher.request("HEAD", local_site + "/old").headers["location"] == "/new"
    assert all(a.startswith("Mozilla/5.0 (compatible; ") for a in Handler.seen)
    assert "SympraxiLinkChecker" in Handler.seen[0]


def test_the_checker_follows_a_real_redirect(local_site) -> None:
    real = linkcheck.Checker(linkcheck.PublicFetcher(allow_private=True), sleep=lambda s: None)
    assert real.check(local_site + "/ok").state == LinkState.OK
    assert real.check(local_site + "/gone").state == LinkState.UNREACHABLE
    # /old redirects to /new, which is missing: the page is gone.
    assert real.check(local_site + "/old").state == LinkState.UNREACHABLE


# --- What each answer means -----------------------------------------------------------------


def test_a_page_that_answers_is_fine() -> None:
    result = checker({("HEAD", PAGE): ok()}).check(PAGE)
    assert (result.state, result.status) == (LinkState.OK, 200)


def test_a_missing_page_fails_and_two_failures_in_a_row_mean_broken() -> None:
    resource = make_resource("argument:thesis", url=PAGE)
    today = timezone.now().date()
    result = checker({}).check(PAGE)
    assert result.state == LinkState.UNREACHABLE
    linkcheck.apply(resource, result, today)
    assert resource.link_state == LinkState.UNREACHABLE
    linkcheck.apply(resource, result, today)  # the same day doesn't count twice
    assert resource.link_state == LinkState.UNREACHABLE
    linkcheck.apply(resource, result, today + timedelta(days=7))
    assert resource.link_state == LinkState.BROKEN
    linkcheck.apply(resource, linkcheck.Result(LinkState.OK, 200), today + timedelta(days=14))
    assert (resource.link_state, resource.link_failing_since) == (LinkState.OK, None)


def test_a_permanent_redirect_elsewhere_is_moved_and_says_where() -> None:
    new = "https://example.org/new-home"
    result = checker({("HEAD", PAGE): moved(new), ("HEAD", new): ok()}).check(PAGE)
    assert (result.state, result.moved_to) == (LinkState.MOVED, new)


@pytest.mark.parametrize(
    ("first", "target"),
    [
        (301, "https://example.org/page/"),  # a trailing slash
        (308, "https://www.example.org/page"),  # a www prefix
        (302, "https://example.org/elsewhere"),  # temporary
        (307, "https://example.org/elsewhere"),
    ],
)
def test_small_or_temporary_redirects_are_not_a_move(first, target) -> None:
    answers = {("HEAD", PAGE): moved(target, first), ("HEAD", target): ok()}
    assert checker(answers).check(PAGE).state == LinkState.OK


def test_a_redirect_loop_is_given_up_on() -> None:
    answers = {("HEAD", PAGE): moved(PAGE + "?a")}
    for n in range(10):
        answers[("HEAD", PAGE + "?a" * (n + 1))] = moved(PAGE + "?a" * (n + 2))
    result = checker(answers).check(PAGE)
    assert result.state == LinkState.UNREACHABLE and "redirects" in result.detail


def test_a_redirect_off_the_web_is_refused() -> None:
    result = checker({("HEAD", PAGE): moved("ftp://example.org/file")}).check(PAGE)
    assert result.state == LinkState.UNREACHABLE


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_site_that_turns_the_checker_away_says_nothing_about_the_page(status) -> None:
    answers = {
        ("HEAD", PAGE): linkcheck.Response(status),
        ("GET", PAGE): linkcheck.Response(status),
    }
    assert checker(answers).check(PAGE).state == LinkState.BLOCKED


def test_a_server_that_refuses_head_is_asked_with_get() -> None:
    answers = {("HEAD", PAGE): linkcheck.Response(405), ("GET", PAGE): ok()}
    fake = checker(answers)
    assert fake.check(PAGE).state == LinkState.OK
    assert [m for m, u in fake.fetcher.requests if u == PAGE] == ["HEAD", "GET"]  # type: ignore[attr-defined]


def test_no_answer_is_unreachable() -> None:
    result = checker({("HEAD", PAGE): linkcheck.Unreachable("timed out")}).check(PAGE)
    assert (result.state, result.detail) == (LinkState.UNREACHABLE, "timed out")


def test_a_bad_address_is_broken_at_once_in_wording() -> None:
    assert checker({}).check("not a url").state == LinkState.BROKEN


# --- robots.txt and delays -------------------------------------------------------------------


def test_a_page_robots_txt_disallows_is_never_visited() -> None:
    answers = {ROBOTS: robots("User-agent: *\nDisallow: /page\n"), ("HEAD", PAGE): ok()}
    probe = checker(answers)
    result = probe.check(PAGE)
    assert result.state == LinkState.BLOCKED
    assert ("HEAD", PAGE) not in probe.fetcher.requests  # type: ignore[attr-defined]


def test_a_rule_for_this_checker_by_name_is_obeyed() -> None:
    text = "User-agent: SympraxiLinkChecker\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
    answers = {ROBOTS: robots(text), ("HEAD", PAGE): ok()}
    assert checker(answers).check(PAGE).state == LinkState.BLOCKED


def test_no_robots_txt_means_everything_is_allowed() -> None:
    answers = {ROBOTS: linkcheck.Response(404), ("HEAD", PAGE): ok()}
    assert checker(answers).check(PAGE).state == LinkState.OK


def test_a_robots_txt_that_fails_leaves_the_link_for_another_day() -> None:
    answers = {ROBOTS: linkcheck.Response(503), ("HEAD", PAGE): ok()}
    probe = checker(answers)
    assert probe.check(PAGE).state == LinkState.UNCHECKED
    assert ("HEAD", PAGE) not in probe.fetcher.requests  # type: ignore[attr-defined]
    down = checker({ROBOTS: linkcheck.Unreachable("no route")})
    assert down.check(PAGE).state == LinkState.UNCHECKED


def test_the_crawl_delay_is_waited_between_requests_to_one_site() -> None:
    slept: list[float] = []
    other = "https://example.org/other"
    answers = {
        ROBOTS: robots("User-agent: *\nCrawl-delay: 5\n"),
        ("HEAD", PAGE): ok(),
        ("HEAD", other): ok(),
    }
    probe = checker(answers, slept)
    probe.check(PAGE)
    probe.check(other)
    assert max(slept) >= 5
    # A site with no crawl delay still gets a second between requests.
    slept.clear()
    quick = checker(
        {ROBOTS: linkcheck.Response(404), ("HEAD", PAGE): ok(), ("HEAD", other): ok()}, slept
    )
    quick.check(PAGE)
    quick.check(other)
    assert slept and all(s >= 0 for s in slept) and max(slept) >= linkcheck.MIN_DELAY - 0.001


def test_robots_txt_is_fetched_once_per_site_per_run() -> None:
    answers = {ROBOTS: linkcheck.Response(404), ("HEAD", PAGE): ok(), ("HEAD", PAGE + "2"): ok()}
    probe = checker(answers)
    probe.check(PAGE)
    probe.check(PAGE + "2")
    assert [r for r in probe.fetcher.requests if r == ROBOTS] == [ROBOTS]  # type: ignore[attr-defined]


# --- The weekly run ---------------------------------------------------------------------------


def test_only_links_that_are_due_and_automatic_are_checked() -> None:
    old = timezone.now().date() - timedelta(days=8)
    fresh = make_resource(
        "argument:thesis", url="https://a.example/fresh", last_checked=timezone.now().date()
    )
    never = make_resource("argument:thesis", url="https://a.example/never")
    stale = make_resource("argument:thesis", url="https://a.example/stale", last_checked=old)
    manual = make_resource("argument:thesis", url="https://a.example/manual", link_check="manual")
    off = make_resource("argument:thesis", url="https://a.example/off", link_check="off")
    hidden = make_resource("argument:thesis", url="https://a.example/hidden", is_active=False)
    due = set(linkcheck.due())
    assert due == {never, stale}
    assert not ({fresh, manual, off, hidden} & due)


def test_a_run_records_what_it_found() -> None:
    good = make_resource("argument:thesis", url="https://a.example/good")
    gone = make_resource("argument:thesis", url="https://a.example/gone")
    new = "https://a.example/new"
    shifted = make_resource("argument:thesis", url="https://a.example/shifted")
    answers = {
        ("GET", "https://a.example/robots.txt"): linkcheck.Response(404),
        ("HEAD", good.url): ok(),
        ("HEAD", shifted.url): moved(new),
        ("HEAD", new): ok(),
    }
    row = linkcheck.run(LinkCheckRun.objects.create(), checker=checker(answers))
    assert (row.checked, row.ok, row.moved, row.broken) == (3, 1, 1, 0)
    assert row.finished_at is not None
    for resource in (good, gone, shifted):
        resource.refresh_from_db()
    assert good.link_state == LinkState.OK and good.last_checked == timezone.now().date()
    assert gone.link_state == LinkState.UNREACHABLE
    assert shifted.link_moved_to == new
    assert AuditEvent.objects.filter(action=AuditAction.LINK_CHECK_RUN).exists()


def test_the_next_weeks_failed_check_makes_it_broken() -> None:
    gone = make_resource("argument:thesis", url="https://a.example/gone")
    answers = {("GET", "https://a.example/robots.txt"): linkcheck.Response(404)}
    linkcheck.run(LinkCheckRun.objects.create(), checker=checker(answers))
    Resource.objects.filter(pk=gone.pk).update(
        last_checked=timezone.now().date() - timedelta(days=8),
        link_failing_since=timezone.now().date() - timedelta(days=8),
    )
    row = linkcheck.run(LinkCheckRun.objects.create(), checker=checker(answers))
    gone.refresh_from_db()
    assert gone.link_state == LinkState.BROKEN and row.broken == 1


def test_one_link_that_blows_up_doesnt_stop_the_others() -> None:
    bad = make_resource("argument:thesis", url="https://a.example/bad")
    good = make_resource("argument:thesis", url="https://b.example/good")

    class Boom(linkcheck.Checker):
        def check(self, url: str) -> linkcheck.Result:
            if "bad" in url:
                raise RuntimeError("boom")
            return linkcheck.Result(LinkState.OK, 200)

    row = linkcheck.run(LinkCheckRun.objects.create(), checker=Boom(Fake({})))
    good.refresh_from_db()
    bad.refresh_from_db()
    assert good.link_state == LinkState.OK and bad.link_state == LinkState.UNCHECKED
    assert row.skipped == 1 and row.checked == 1


# --- Scheduling the run --------------------------------------------------------------------------


@pytest.fixture
def fake_network(monkeypatch) -> Fake:
    fake = Fake({("GET", "https://a.example/robots.txt"): linkcheck.Response(404)})
    monkeypatch.setattr(linkcheck, "PublicFetcher", lambda *a, **k: fake)
    monkeypatch.setattr(linkcheck.time, "sleep", lambda s: None)
    return fake


def test_nothing_runs_unless_the_installation_turns_it_on(settings) -> None:
    make_resource("argument:thesis", url="https://a.example/x")
    assert settings.SYMPRAXI_LINK_CHECK is False
    assert linkcheck.enqueue_if_due() == 0
    assert not LinkCheckRun.objects.exists()


def test_a_run_starts_when_links_are_due_and_not_twice_in_a_day(settings, fake_network) -> None:
    settings.SYMPRAXI_LINK_CHECK = True
    resource = make_resource("argument:thesis", url="https://a.example/x")
    fake_network.answers[("HEAD", resource.url)] = ok()
    assert linkcheck.enqueue_if_due() == 1
    resource.refresh_from_db()
    assert resource.link_state == LinkState.OK  # the worker ran it (immediately, in tests)
    assert LinkCheckRun.objects.get().finished_at is not None
    assert linkcheck.enqueue_if_due() == 0


def test_nothing_starts_when_nothing_is_due(settings, fake_network) -> None:
    settings.SYMPRAXI_LINK_CHECK = True
    assert linkcheck.enqueue_if_due() == 0


def test_a_run_stuck_for_hours_doesnt_block_the_next(settings, fake_network) -> None:
    settings.SYMPRAXI_LINK_CHECK = True
    make_resource("argument:thesis", url="https://a.example/x")
    stuck = LinkCheckRun.objects.create()
    LinkCheckRun.objects.filter(pk=stuck.pk).update(started_at=timezone.now() - timedelta(hours=2))
    assert linkcheck.enqueue_if_due() == 0  # still within its time
    LinkCheckRun.objects.filter(pk=stuck.pk).update(started_at=timezone.now() - timedelta(hours=7))
    assert linkcheck.enqueue_if_due() == 1


# --- Tutors aren't offered broken links ----------------------------------------------


def test_broken_links_are_left_out_of_suggestions_and_search() -> None:
    center = CenterFactory()
    make_topics()
    fine = make_resource("argument:thesis", title="Thesis guide")
    gone = make_resource("argument:thesis", title="Thesis broken", link_state=LinkState.BROKEN)
    watch = make_resource("argument:thesis", title="Thesis watch", link_state=LinkState.UNREACHABLE)
    found = {s.resource for s in suggestions.suggest(center, topics("argument:thesis"))}
    assert fine in found and watch in found and gone not in found
    assert gone not in suggestions.search(center, "Thesis")
    assert watch in suggestions.search(center, "Thesis")


# --- The director's page -----------------------------------------------------------


@pytest.fixture
def director(center):
    return make_member(center, Role.DIRECTOR)


def links_page(center) -> str:
    return reverse("records:resource_links", kwargs={"slug": center.slug})


def test_the_page_lists_own_and_shared_links_that_need_attention(client, center, director) -> None:
    make_topics()
    mine = make_resource(
        "argument:thesis",
        center=center,
        title="Our handout",
        link_state=LinkState.BROKEN,
        link_detail="The page answered 404.",
    )
    shifted = make_resource(
        "argument:thesis",
        center=center,
        title="Our moved page",
        link_state=LinkState.MOVED,
        link_moved_to="https://new.example/page",
    )
    fine = make_resource(
        "argument:thesis", center=center, title="Our fine page", link_state=LinkState.OK
    )
    other = make_resource(
        "argument:thesis",
        center=CenterFactory(),
        title="Somebody else's page",
        link_state=LinkState.BROKEN,
    )
    shared = make_resource("argument:thesis", title="Shared guide", link_state=LinkState.BROKEN)
    client.force_login(director)
    page = client.get(links_page(center)).content.decode()
    assert mine.title in page and shifted.title in page and "https://new.example/page" in page
    assert fine.title not in page and other.title not in page
    assert shared.title in page and "can't edit them here" in page


def test_it_says_when_the_checker_is_off(client, center, director, settings) -> None:
    client.force_login(director)
    assert "turned off for this installation" in client.get(links_page(center)).content.decode()
    settings.SYMPRAXI_LINK_CHECK = True
    page = client.get(links_page(center)).content.decode()
    assert "turned off for this installation" not in page and "hasn't run yet" in page


def test_only_directors_see_it(client, center) -> None:
    for role in (Role.TUTOR, Role.FRONT_DESK, Role.STUDENT):
        client.force_login(make_member(center, role))
        assert client.get(links_page(center)).status_code == 403


def test_a_director_checks_one_of_their_links_now(
    client, center, director, settings, monkeypatch
) -> None:
    settings.SYMPRAXI_LINK_CHECK = True
    mine = make_resource("argument:thesis", center=center, link_state=LinkState.BROKEN)
    monkeypatch.setattr(
        linkcheck,
        "Checker",
        lambda *a, **k: checker(
            {
                ("GET", "https://example.org/robots.txt"): linkcheck.Response(404),
                ("HEAD", mine.url): ok(),
            }
        ),
    )
    url = reverse("records:resource_check", kwargs={"slug": center.slug, "resource_id": mine.pk})
    client.force_login(director)
    assert "Check it now" in client.get(url).content.decode()
    response = client.post(url, follow=True)
    mine.refresh_from_db()
    assert mine.link_state == LinkState.OK and "The link works." in response.content.decode()
    assert AuditEvent.objects.filter(action=AuditAction.LINK_CHECKED).exists()


def test_a_manual_check_that_fails_says_why(
    client, center, director, settings, monkeypatch
) -> None:
    settings.SYMPRAXI_LINK_CHECK = True
    mine = make_resource("argument:thesis", center=center)
    monkeypatch.setattr(
        linkcheck,
        "Checker",
        lambda *a, **k: checker(
            {("GET", "https://example.org/robots.txt"): linkcheck.Response(404)}
        ),
    )
    url = reverse("records:resource_check", kwargs={"slug": center.slug, "resource_id": mine.pk})
    client.force_login(director)
    response = client.post(url, follow=True)
    mine.refresh_from_db()
    assert mine.link_state == LinkState.BROKEN  # a director's check settles it
    assert "wasn&#x27;t confirmed" in response.content.decode()


def test_a_manual_check_does_nothing_while_the_checker_is_off(client, center, director) -> None:
    mine = make_resource("argument:thesis", center=center)
    url = reverse("records:resource_check", kwargs={"slug": center.slug, "resource_id": mine.pk})
    client.force_login(director)
    client.post(url)
    mine.refresh_from_db()
    assert mine.link_state == LinkState.UNCHECKED


def test_another_centers_link_cant_be_checked(client, center, director) -> None:
    theirs = make_resource("argument:thesis", center=CenterFactory())
    url = reverse("records:resource_check", kwargs={"slug": center.slug, "resource_id": theirs.pk})
    client.force_login(director)
    assert client.get(url).status_code == 404
