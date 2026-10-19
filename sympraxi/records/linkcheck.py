"""The link checker: is each resource's page still where the catalog says it is?

Once a week the scheduler asks the task worker to go over every resource whose link
is set to be checked automatically (the shared catalog's entries, which come with
that setting from `catalog/publishers.yaml`, and a center's own links). It is off
unless the installation sets `SYMPRAXI_LINK_CHECK`, because it makes requests from
this server to other people's websites.

Rules it keeps (docs/03-resource-library.md):

- It reads each site's robots.txt and does not visit a page the file disallows for
  it. The site's crawl delay is honored, and a second is the least it waits between
  two requests to one site.
- Its user agent begins `Mozilla/5.0 (compatible; ` and names the checker, which some
  sites (Pressbooks, for one) need before they will answer.
- It asks with HEAD first and falls back to GET, reading no more than the headers
  and the first few bytes.
- It never connects to an address inside a private network. A director can add a
  link to any address, and the checker runs inside the college's network, so without
  this a link could be used to make the server fetch an internal page. The name is
  resolved once, every address it gives must be public, and the connection goes to
  the address that was checked (not to the name again), which also stops a name that
  changes its answer between the check and the connection. Redirects are followed by
  hand, five at most, and each one is vetted the same way.

What it concludes. A page that answers is `ok`, even after a temporary redirect. A
permanent redirect to somewhere else is `moved`, and says where. A 404, a 410, an
error or no answer is `unreachable` the first time and `broken` once a later check
fails too, so one bad night doesn't flag a good link. A site that refuses the
checker (401, 403, 429) or asks not to be visited is `blocked`: that says nothing
about the page. Tutors are not offered links that are broken.
"""

import http.client
import ipaddress
import logging
import socket
import ssl
import time
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlsplit, urlunsplit

from django.conf import settings
from django.db.models import Q, QuerySet
from django.utils import timezone

from sympraxi.privacy.audit import AuditAction, record
from sympraxi.records.models import LinkCheckRun, LinkState, Resource

logger = logging.getLogger("sympraxi.linkcheck")

USER_AGENT = (
    "Mozilla/5.0 (compatible; SympraxiLinkChecker/1.0; +https://github.com/justalewis/sympraxi)"
)
ROBOTS_NAME = "SympraxiLinkChecker"
TIMEOUT = 10
MAX_REDIRECTS = 5
# The least wait between two requests to one site, and the longest crawl delay obeyed.
MIN_DELAY = 1.0
MAX_DELAY = 30.0
# A link is checked again once its last check is this old.
RECHECK_AFTER = timedelta(days=7)
# The most one run checks, so a very large catalog can't run for a day.
MAX_PER_RUN = 1500


class Unreachable(Exception):
    """The address couldn't be used: not public, not resolvable, or no connection."""


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


@dataclass
class Result:
    state: str
    status: int | None = None
    moved_to: str = ""
    detail: str = ""


# --- Fetching, with the address guard -----------------------------------------------------


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def public_address(host: str, port: int, *, allow_private: bool = False) -> str:
    """The one address to connect to for a name, if every address it has is public."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        raise Unreachable(f"couldn't find {host}") from error
    addresses = [str(item[4][0]) for item in found]
    if not addresses:
        raise Unreachable(f"{host} has no address")
    if not allow_private and not all(is_public(a) for a in addresses):
        raise Unreachable(f"{host} is not a public address")
    return str(addresses[0])


class PublicFetcher:
    """Makes one request to a vetted address and reads only the start of the answer."""

    def __init__(self, *, allow_private: bool = False, timeout: float = TIMEOUT) -> None:
        self.allow_private = allow_private
        self.timeout = timeout

    def request(self, method: str, url: str, *, max_body: int = 4096) -> Response:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise Unreachable("not a web address")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        address = public_address(parts.hostname, port, allow_private=self.allow_private)
        try:
            sock = socket.create_connection((address, port), timeout=self.timeout)
            if parts.scheme == "https":
                sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=parts.hostname
                )
        except (OSError, ssl.SSLError) as error:
            raise Unreachable(f"couldn't connect to {parts.hostname}: {error}") from error
        connection: http.client.HTTPConnection
        if parts.scheme == "https":
            connection = http.client.HTTPSConnection(parts.hostname, port, timeout=self.timeout)
        else:
            connection = http.client.HTTPConnection(parts.hostname, port, timeout=self.timeout)
        connection.sock = sock
        path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            connection.request(
                method,
                path,
                headers={"User-Agent": USER_AGENT, "Accept": "*/*", "Connection": "close"},
            )
            answer = connection.getresponse()
            body = answer.read(max_body) if method != "HEAD" else b""
            headers = {k.lower(): v for k, v in answer.getheaders()}
            return Response(answer.status, headers, body)
        except (OSError, http.client.HTTPException) as error:
            raise Unreachable(f"no answer from {parts.hostname}: {error}") from error
        finally:
            connection.close()


# --- Checking one address ------------------------------------------------------------------


Fetch = Callable[..., Response]


@dataclass
class Robots:
    """What a site's robots.txt allows the checker, kept for the run."""

    parser: urllib.robotparser.RobotFileParser | None
    # None: couldn't be read, so don't visit the site now.
    usable: bool = True

    def allows(self, url: str) -> bool:
        return self.parser is None or self.parser.can_fetch(ROBOTS_NAME, url)

    def delay(self) -> float:
        wanted = self.parser.crawl_delay(ROBOTS_NAME) if self.parser else None
        return min(MAX_DELAY, max(MIN_DELAY, float(wanted or 0)))


class Checker:
    def __init__(
        self,
        fetcher: PublicFetcher | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.fetcher = fetcher or PublicFetcher()
        self.sleep = sleep
        self.clock = clock
        self.robots: dict[str, Robots] = {}
        self.last_request: dict[str, float] = {}

    # robots.txt

    def _robots_for(self, url: str) -> Robots:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robots:
            return self.robots[origin]
        robots = Robots(None)
        try:
            answer = self._get_throttled(
                "GET", f"{origin}/robots.txt", origin=origin, max_body=500_000, delay=MIN_DELAY
            )
        except Unreachable:
            robots = Robots(None, usable=False)
        else:
            if answer.status == 200:
                parser = urllib.robotparser.RobotFileParser()
                parser.parse(answer.body.decode("utf-8", "replace").splitlines())
                robots = Robots(parser)
            elif answer.status >= 500:
                robots = Robots(None, usable=False)
            # 4xx means the site has no robots.txt: everything is allowed.
        self.robots[origin] = robots
        return robots

    def _get_throttled(
        self, method: str, url: str, *, origin: str, max_body: int = 4096, delay: float
    ) -> Response:
        waited = self.clock() - self.last_request.get(origin, -1e9)
        if waited < delay:
            self.sleep(delay - waited)
        self.last_request[origin] = self.clock()
        return self.fetcher.request(method, url, max_body=max_body)

    # one address

    def check(self, url: str) -> Result:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return Result(LinkState.BROKEN, detail="Not a web address.")
        origin = f"{parts.scheme}://{parts.netloc}"
        robots = self._robots_for(url)
        if not robots.usable:
            return Result(LinkState.UNCHECKED, detail="The site's robots.txt couldn't be read.")
        if not robots.allows(url):
            return Result(LinkState.BLOCKED, detail="The site's robots.txt asks not to be checked.")
        current, moved, hops = url, False, 0
        while True:
            try:
                answer = self._ask(current, robots, origin)
            except Unreachable as error:
                return Result(LinkState.UNREACHABLE, detail=str(error)[:200])
            status = answer.status
            if status in (301, 302, 303, 307, 308) and answer.headers.get("location"):
                hops += 1
                if hops > MAX_REDIRECTS:
                    return Result(LinkState.UNREACHABLE, status, detail="Too many redirects.")
                target = urljoin(current, answer.headers["location"])
                if urlsplit(target).scheme not in ("http", "https"):
                    return Result(LinkState.UNREACHABLE, status, detail="Redirected off the web.")
                moved = moved or status in (301, 308)
                current = target
                origin = _origin(current)
                robots = self._robots_for(current)
                if not robots.usable:
                    return Result(LinkState.UNCHECKED, detail="A redirect's robots.txt failed.")
                if not robots.allows(current):
                    return Result(
                        LinkState.BLOCKED,
                        status,
                        detail="The redirect's site asks not to be checked.",
                    )
                continue
            break
        if 200 <= status < 300:
            if moved and _differs(url, current):
                return Result(LinkState.MOVED, status, moved_to=current[:500])
            return Result(LinkState.OK, status)
        if status in (401, 403, 429):
            return Result(LinkState.BLOCKED, status, detail=f"The site answered {status}.")
        return Result(LinkState.UNREACHABLE, status, detail=f"The page answered {status}.")

    def _ask(self, url: str, robots: Robots, origin: str) -> Response:
        delay = robots.delay()
        answer = self._get_throttled("HEAD", url, origin=origin, delay=delay)
        if answer.status in (403, 405, 501) or answer.status >= 500:
            # Some servers refuse HEAD but answer GET.
            answer = self._get_throttled("GET", url, origin=origin, delay=delay)
        return answer


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _normal(url: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{path}?{parts.query}"


def _differs(original: str, final: str) -> bool:
    """Whether a redirect led somewhere new, not only from http to https or a trailing slash."""
    return _normal(original) != _normal(final)


# --- The weekly run --------------------------------------------------------------------------


def due(now: datetime | None = None) -> QuerySet[Resource]:
    today = (now or timezone.now()).date()
    return (
        Resource.objects.filter(is_active=True, link_check="auto")
        .filter(Q(last_checked__isnull=True) | Q(last_checked__lte=today - RECHECK_AFTER))
        .order_by("last_checked", "pk")
    )


def apply(resource: Resource, result: Result, today: date) -> None:
    """Write what the check found. A failure turns into 'broken' on the next failed check."""
    state = result.state
    if state == LinkState.UNREACHABLE:
        if resource.link_failing_since is None:
            resource.link_failing_since = today
        elif resource.link_failing_since < today:
            state = LinkState.BROKEN
    elif state in (LinkState.OK, LinkState.MOVED):
        resource.link_failing_since = None
    if state != LinkState.UNCHECKED:
        resource.last_checked = today
        resource.last_status = result.status
        resource.link_state = state
        resource.link_moved_to = result.moved_to if state == LinkState.MOVED else ""
        resource.link_detail = result.detail
    resource.save(
        update_fields=[
            "last_checked",
            "last_status",
            "link_state",
            "link_moved_to",
            "link_detail",
            "link_failing_since",
        ]
    )


def run(
    run_row: LinkCheckRun,
    *,
    checker: Checker | None = None,
    now: datetime | None = None,
    limit: int = MAX_PER_RUN,
) -> LinkCheckRun:
    """Check the links that are due, and count what was found."""
    checker = checker or Checker()
    today = (now or timezone.now()).date()
    for resource in list(due(now)[:limit]):
        try:
            result = checker.check(resource.url)
        except Exception:  # one bad link must not stop the rest
            logger.exception("Checking %s failed", resource.pk)
            run_row.skipped += 1
            continue
        if result.state == LinkState.UNCHECKED:
            run_row.skipped += 1
            continue
        apply(resource, result, today)
        run_row.checked += 1
        if resource.link_state == LinkState.OK:
            run_row.ok += 1
        elif resource.link_state == LinkState.MOVED:
            run_row.moved += 1
        elif resource.link_state == LinkState.BROKEN:
            run_row.broken += 1
    run_row.finished_at = timezone.now()
    run_row.save()
    record(
        AuditAction.LINK_CHECK_RUN,
        actor_label="link checker",
        checked=run_row.checked,
        moved=run_row.moved,
        broken=run_row.broken,
        skipped=run_row.skipped,
    )
    return run_row


def check_one(resource: Resource, *, checker: Checker | None = None) -> Result:
    """Check a single resource now (a director fixed its address) and record the answer."""
    checker = checker or Checker()
    result = checker.check(resource.url)
    if result.state == LinkState.UNCHECKED:
        return result
    # A director asking by hand starts a new count, so one answer settles it.
    if result.state == LinkState.UNREACHABLE:
        resource.link_failing_since = timezone.now().date() - timedelta(days=1)
    apply(resource, result, timezone.now().date())
    return result


# How long a run may have been going before another is allowed to start.
STALE_AFTER = timedelta(hours=6)
# A run starts at most this often.
EVERY = timedelta(hours=20)


def enqueue_if_due(now: datetime | None = None) -> int:
    """Called by the scheduler each minute. Starts a run if one is due. Returns 1 if it did."""
    if not settings.SYMPRAXI_LINK_CHECK:
        return 0
    now = now or timezone.now()
    last = LinkCheckRun.objects.order_by("-started_at").first()
    if last is not None:
        if last.finished_at is None:
            # One still going holds the next back, unless it has been stuck for hours.
            if now - last.started_at < STALE_AFTER:
                return 0
        elif now - last.started_at < EVERY:
            return 0
    if not due(now).exists():
        return 0
    started = LinkCheckRun.objects.create()
    from sympraxi.records.tasks import check_links

    check_links.enqueue(str(started.pk))
    return 1
