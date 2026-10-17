"""Virtual students and front desk workers, booking through the real pages.

Each student arrives at a set time, opens the schedules page, opens a
schedule's open times for the day they want, picks a time, opens the booking
page and submits it, pausing between pages the way a person reads. Most want
one appointment; some want two, and a few ask for more than the limits allow,
so the limits get tested too. Everyone likes the same hours and the first few
weekdays, so they compete for the same times. A few double-click the booking
button, sending the form twice at once. When a time is gone, they try another,
first on the same day and then on a later one. Front desk workers book for
students by phone and check the day sheet the whole time.

Every booking attempt is kept with how it ended and the message the person
saw, so the checker can confirm that each refusal was readable and that the
database holds exactly the appointments people were told they had.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import psycopg

from tests.load.client import Client, Form, Recorder, Response
from tests.load.world import first_weekdays, local_today

# How much students like each starting hour (local time). Other hours count 1.
POPULAR_HOURS = {10: 3, 11: 5, 12: 6, 13: 6, 14: 5, 15: 3, 16: 2}
# How much they like each of the next five weekdays.
DAY_WEIGHTS = [45, 25, 15, 10, 5]
SCHEDULE_WEIGHTS = {"main": 60, "online": 25, "groups": 15}
# Share of students who go straight for the best time on the list.
HERD = 0.35
TRIES = 4

# What each refusal message means, for the report. Matched as substrings.
REASONS = [
    ("Someone else just booked that time", "slot_taken"),
    ("isn't open anymore", "gone"),
    ("group appointment is full", "full"),
    ("can't take this many students", "full"),
    ("would go over the limit", "limit"),
    ("already has an appointment then", "student_busy"),
    ("is already in that appointment", "already_in"),
    ("fully booked that", "tutor_cap"),
    ("Online booking is paused", "blocked"),
    ("too soon to book online", "notice"),
    ("You can book up to", "horizon"),
]
# Refusals after which trying another time can't help.
FINAL = {"limit", "student_busy", "already_in", "blocked"}


@dataclass
class Options:
    students: int = 500
    duration: float = 600.0  # seconds over which students arrive
    rush: float = 0.3  # share of students who arrive in the first tenth of it
    think: float = 1.0  # scale for pauses between pages; 0 for none
    double_click: float = 0.05  # share of booking forms submitted twice at once
    desk_pause: float = 15.0  # seconds between front desk tasks, on average
    seed: int = 1
    sign_in: float = 0.0  # share of students who sign in with a password first
    password: str = ""


@dataclass
class Want:
    schedule: str
    minutes: int
    day: int  # index into the next five weekdays


@dataclass
class Attempt:
    """One submitted booking form (or a time that was gone before it), and how it ended."""

    who: str
    student: str
    schedule: str
    start: str
    minutes: int
    by_staff: bool
    outcome: str  # booked, refused, error
    reason: str = ""
    message: str = ""
    appointment: str = ""
    status: int = 0
    double: bool = False
    at: float = 0.0


def reason_for(message: str) -> str:
    for needle, code in REASONS:
        if needle in message:
            return code
    return "other"


class Run:
    def __init__(
        self, base_url: str, manifest: dict[str, Any], options: Options, recorder: Recorder
    ) -> None:
        self.base = base_url.rstrip("/")
        self.manifest = manifest
        self.options = options
        self.recorder = recorder
        self.tz = ZoneInfo(manifest["center"]["time_zone"])
        self.slug = manifest["center"]["slug"]
        self.schedules = {s["key"]: s for s in manifest["schedules"]}
        self.days = first_weekdays(local_today(manifest), len(DAY_WEIGHTS))
        self.attempts: list[Attempt] = []
        self.sign_ins: list[tuple[str, int]] = []
        self.stop = threading.Event()
        self._lock = threading.Lock()

    # --- The run ------------------------------------------------------------

    def go(self) -> None:
        rng = _rng(self.options.seed)
        people = self.manifest["students"][: self.options.students]
        arrivals = self.arrivals(rng, len(people))
        desk = [
            threading.Thread(target=self.front_desk, args=(p, _rng(rng.random())))
            for p in self.manifest["front_desk"]
        ]
        for t in desk:
            t.start()
        students = []
        for when, person in zip(arrivals, people, strict=True):
            delay = when - self.recorder.now()
            if delay > 0:
                time.sleep(delay)
            thread = threading.Thread(
                target=self.student, args=(person, _rng(rng.random())), daemon=True
            )
            thread.start()
            students.append(thread)
        for thread in students:
            thread.join(timeout=900)
        self.stop.set()
        for t in desk:
            t.join(timeout=120)

    def arrivals(self, rng: random.Random, n: int) -> list[float]:
        """When each student arrives: a rush when booking opens, then tapering off."""
        span = self.options.duration
        rush = int(n * self.options.rush)
        times = [rng.uniform(0, span / 10) for _ in range(rush)]
        while len(times) < n:
            t = rng.expovariate(3 / span) if span > 0 else 0.0
            if t <= span:
                times.append(t)
        return sorted(times)

    def pause(self, rng: random.Random, low: float, high: float) -> None:
        seconds = rng.uniform(low, high) * self.options.think
        if seconds > 0:
            time.sleep(seconds)

    def keep(self, attempt: Attempt) -> None:
        attempt.at = self.recorder.now()
        with self._lock:
            self.attempts.append(attempt)

    # --- Students -----------------------------------------------------------

    def student(self, person: dict[str, str], rng: random.Random) -> None:
        client = Client(self.base, self.recorder, person["email"])
        if rng.random() < self.options.sign_in:
            self.sign_in(client, person)
        else:
            client.cookies["sessionid"] = person["session"]
        home = client.get(f"/centers/{self.slug}/schedules/", "schedule_list")
        if home.status != 200:
            return
        client.fetch_assets(home.page())
        for want in self.wants(rng):
            self.pause(rng, 3, 8)
            self.book(client, want, rng, student=person["id"])
        if rng.random() < 0.5:
            self.pause(rng, 2, 5)
            client.get(f"/centers/{self.slug}/appointments/", "my_appointments")

    def sign_in(self, client: Client, person: dict[str, str]) -> None:
        """Sign in with a password. If that's refused, carry on as if through SSO."""
        login = client.get("/accounts/login/", "sign_in_form")
        form = _post_form(login, "login")
        status = login.status
        if form is not None:
            data = {**form.fields, "login": person["email"], "password": self.options.password}
            done = client.post("/accounts/login/", data, "sign_in_post")
            status = done.status
        with self._lock:
            self.sign_ins.append((person["email"], status))
        if status != 302:
            client.cookies = {"sessionid": person["session"]}

    def wants(self, rng: random.Random) -> list[Want]:
        key = _weighted(rng, SCHEDULE_WEIGHTS)
        first = Want(key, self.length(key, rng), _weighted_index(rng, DAY_WEIGHTS))
        roll = rng.random()
        if roll < 0.70:
            return [first]
        if roll < 0.90:
            other = _weighted(rng, SCHEDULE_WEIGHTS)
            later = min(first.day + rng.randint(1, 3), len(self.days) - 1)
            return [first, Want(other, self.length(other, rng), later)]
        if roll < 0.95:
            # A second on the same day: the in-person schedule allows one a day.
            return [first, Want(first.schedule, first.minutes, first.day)]
        # Three in person in one week: the limit is two.
        return [Want("main", 30, 0), Want("main", 30, 1), Want("main", 30, 2)]

    def length(self, key: str, rng: random.Random) -> int:
        lengths = self.schedules[key]["lengths"]
        return lengths[0] if len(lengths) == 1 or rng.random() < 0.7 else lengths[-1]

    # --- Front desk ---------------------------------------------------------

    def front_desk(self, person: dict[str, str], rng: random.Random) -> None:
        client = Client(self.base, self.recorder, person["email"])
        client.cookies["sessionid"] = person["session"]
        home = client.get(f"/centers/{self.slug}/schedules/", "schedule_list")
        if home.status == 200:
            client.fetch_assets(home.page())
        main = self.schedules["main"]["id"]
        mean = self.options.desk_pause
        while not self.stop.wait(rng.uniform(0.5 * mean, 1.5 * mean)):
            if rng.random() < 0.4:
                day = self.days[_weighted_index(rng, DAY_WEIGHTS)]
                client.get(
                    f"/centers/{self.slug}/schedules/{main}/day/?date={day.isoformat()}",
                    "day_sheet",
                )
            else:
                student = rng.choice(self.manifest["students"][: self.options.students])
                want = Want("main", self.length("main", rng), _weighted_index(rng, DAY_WEIGHTS))
                self.book(client, want, rng, student=student["id"], staff=True)

    # --- Booking ------------------------------------------------------------

    def book(
        self,
        client: Client,
        want: Want,
        rng: random.Random,
        *,
        student: str,
        staff: bool = False,
    ) -> None:
        """Try up to TRIES times to book what the person wants, as a person would."""
        prefix = "staff_" if staff else ""
        marker = "/book-for/?" if staff else "/book/?"
        day = want.day
        for tries in range(TRIES):
            if day >= len(self.days):
                return
            sid = self.schedules[want.schedule]["id"]
            query = f"?date={self.days[day].isoformat()}&minutes={want.minutes}"
            url = f"/centers/{self.slug}/schedules/{sid}/{query}"
            # After the first look, a browser with JavaScript fetches only the list.
            htmx = tries > 0 and not staff
            listing = client.get(url, f"{prefix}open_times" + ("_htmx" if htmx else ""), htmx=htmx)
            if listing.status != 200:
                return
            links = [link for link in listing.page().links if marker in link]
            if not links:
                day += 1
                continue
            link = self.choose(links, rng)
            self.pause(rng, 2, 6)
            attempt = Attempt(
                who=client.who,
                student=student,
                schedule=want.schedule,
                start=_query(link, "start"),
                minutes=want.minutes,
                by_staff=staff,
                outcome="error",
            )
            page = client.get(link, f"{prefix}book_form")
            if page.status != 200:
                self.settle(client, page, attempt)
                if attempt.outcome == "error":
                    return
                if rng.random() < 0.5:
                    day += 1
                continue
            form = _post_form(page, "assignment")
            if form is None:
                attempt.message = "no booking form on the page"
                self.keep(attempt)
                return
            data = dict(form.fields)
            if staff:
                data["student"] = student
            self.pause(rng, 1, 4)
            double = not staff and rng.random() < self.options.double_click
            results = self.submit(client, link, data, attempt, double, f"{prefix}book_post")
            if any(a.outcome == "booked" for a in results):
                return
            if any(a.outcome == "error" or a.reason in FINAL for a in results):
                return
            if rng.random() < 0.5:
                day += 1

    def choose(self, links: list[str], rng: random.Random) -> str:
        """Pick a time the way a crowd does: most go for the popular hours, many for the best."""
        weights = [POPULAR_HOURS.get(self.local_hour(link), 1) for link in links]
        if rng.random() < HERD:
            return links[weights.index(max(weights))]
        return rng.choices(links, weights=weights)[0]

    def local_hour(self, link: str) -> int:
        start = _query(link, "start").replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(start).astimezone(self.tz).hour
        except ValueError:
            return -1

    def submit(
        self,
        client: Client,
        link: str,
        data: dict[str, str],
        attempt: Attempt,
        double: bool,
        kind: str,
    ) -> list[Attempt]:
        """Send the booking form, or send it twice at once like a double-click."""
        if not double:
            self.settle(client, client.post(link, data, kind), attempt)
            return [attempt]
        twins = [client.clone(), client.clone()]
        tries = [Attempt(**{**asdict(attempt), "double": True}) for _ in twins]
        threads = [
            threading.Thread(target=lambda c=c, a=a: self.settle(c, c.post(link, data, kind), a))
            for c, a in zip(twins, tries, strict=True)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        client.cookies = dict(twins[-1].cookies)
        return tries

    def settle(self, client: Client, response: Response, attempt: Attempt) -> None:
        """Read how the request ended, following a redirect the way a browser would."""
        attempt.status = response.status
        if response.status == 302 and "/appointments/" in response.location:
            # The redirect is the booking; the appointment page's own timing and
            # status are kept as a sample like any other page.
            attempt.outcome = "booked"
            attempt.appointment = response.location.rstrip("/").split("/")[-1]
            shown = client.get(response.location, "appointment")
            attempt.message = " ".join(shown.page().messages("success"))
        elif response.status == 302 and response.location:
            shown = client.get(response.location, "open_times")
            self.refusal(attempt, shown.page().messages("error", "warning"), shown.status)
        elif response.status == 200:
            self.refusal(attempt, response.page().messages("error", "field"), 200)
        else:
            attempt.outcome = "error"
            attempt.message = response.error or f"HTTP {response.status}"
        self.keep(attempt)

    def refusal(self, attempt: Attempt, messages: list[str], status: int) -> None:
        if status == 200 and messages:
            attempt.outcome = "refused"
            attempt.message = " ".join(messages)
            attempt.reason = reason_for(attempt.message)
        else:
            attempt.outcome = "error"
            attempt.message = f"refused without a message (HTTP {status})"


def warm_up(base_url: str, manifest: dict[str, Any], rounds: int) -> None:
    """Open every page a few times, untimed and without booking.

    On a first Monday the server has been up for days, so the time each worker
    spends loading templates and connecting on its first requests isn't part
    of the rush.
    """
    slug = manifest["center"]["slug"]
    day = first_weekdays(local_today(manifest), 1)[0].isoformat()

    def visit(person: dict[str, str], staff: bool) -> None:
        client = Client(base_url, Recorder(), "warm-up")
        client.cookies["sessionid"] = person["session"]
        client.get(f"/centers/{slug}/schedules/", "warm_up")
        marker = "/book-for/?" if staff else "/book/?"
        for schedule in manifest["schedules"]:
            listing = client.get(f"/centers/{slug}/schedules/{schedule['id']}/?date={day}", "-")
            links = [link for link in listing.page().links if marker in link]
            if links:
                client.get(links[0], "warm_up")
            if staff:
                client.get(f"/centers/{slug}/schedules/{schedule['id']}/day/?date={day}", "-")
        if not staff:
            client.get(f"/centers/{slug}/appointments/", "warm_up")

    threads = [
        threading.Thread(target=visit, args=(person, staff))
        for _ in range(rounds)
        for person, staff in (
            (manifest["students"][-1], False),
            (manifest["front_desk"][0], True),
        )
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)


class DatabaseWatch(threading.Thread):
    """Samples PostgreSQL during the run: busy connections, lock waits and which queries wait."""

    def __init__(self, url: str, recorder: Recorder, interval: float = 0.2) -> None:
        super().__init__(daemon=True)
        self.url = url
        self.recorder = recorder
        self.interval = interval
        self.stop = threading.Event()
        self.samples: list[tuple[float, int, int]] = []  # (at, active, waiting on a lock)
        self.waiting: dict[str, int] = {}
        self.before: dict[str, int] = {}
        self.after: dict[str, int] = {}

    def counters(self, conn: psycopg.Connection[Any]) -> dict[str, int]:
        row = conn.execute(
            "SELECT deadlocks, xact_commit, xact_rollback FROM pg_stat_database"
            " WHERE datname = current_database()"
        ).fetchone()
        assert row is not None
        return {"deadlocks": row[0], "commits": row[1], "rollbacks": row[2]}

    def run(self) -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            self.before = self.counters(conn)
            while not self.stop.wait(self.interval):
                rows = conn.execute(
                    "SELECT state, wait_event_type, left(regexp_replace(query, '\\s+', ' ', 'g'),"
                    " 160) FROM pg_stat_activity WHERE datname = current_database()"
                    " AND pid <> pg_backend_pid()"
                ).fetchall()
                active = sum(1 for state, _, _ in rows if state == "active")
                waits = [query for _, wait, query in rows if wait == "Lock"]
                self.samples.append((self.recorder.now(), active, len(waits)))
                for query in waits:
                    self.waiting[query] = self.waiting.get(query, 0) + 1
            self.after = self.counters(conn)


def _rng(seed: float) -> random.Random:
    return random.Random(seed)  # noqa: S311 (who books what, not a secret)


def _post_form(response: Response, field: str) -> Form | None:
    """The page's form that posts `field` (pages also carry a sign-out form)."""
    if response.status != 200:
        return None
    forms = response.page().forms
    return next((f for f in forms if f.method.lower() == "post" and field in f.fields), None)


def _query(link: str, name: str) -> str:
    return parse_qs(urlsplit(link).query).get(name, [""])[0]


def _weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _weighted_index(rng: random.Random, weights: list[int]) -> int:
    return rng.choices(range(len(weights)), weights=weights)[0]
