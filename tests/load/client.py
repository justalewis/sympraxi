"""A small browser stand-in for virtual users, using only the standard library.

It keeps cookies, reads the parts of a page a person would act on (links,
forms, alert messages, the files a browser would fetch) and times every
request. Redirects are not followed automatically, so each request is timed
on its own and the driver sees where a refusal sent the person.
"""

from __future__ import annotations

import http.client
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit


@dataclass
class Sample:
    """One request, as the load test records it."""

    kind: str
    method: str
    status: int  # 0 when the request never got a response
    ms: float
    at: float  # seconds since the run started
    who: str
    error: str = ""


class Recorder:
    """Collects samples from every virtual user's thread."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.samples: list[Sample] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        return time.perf_counter() - self.started

    def add(self, sample: Sample) -> None:
        with self._lock:
            self.samples.append(sample)


@dataclass
class Form:
    action: str
    method: str
    fields: dict[str, str] = field(default_factory=dict)
    choices: dict[str, list[str]] = field(default_factory=dict)


class Page(HTMLParser):
    """The links, forms, alerts and page assets of one HTML page."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.forms: list[Form] = []
        self.alerts: list[tuple[str, str]] = []  # (error/warning/success/info/field, text)
        self.assets: list[str] = []
        self.title = ""
        self._alert_level = ""
        self._text: list[str] | None = None
        self._text_level = ""
        self._in_title = False
        self._select: str | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        classes = a.get("class", "").split()
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "div" and "usa-alert" in classes:
            levels = [c.removeprefix("usa-alert--") for c in classes if c.startswith("usa-alert--")]
            self._alert_level = next((lv for lv in levels if lv != "slim"), "info")
        elif tag == "p" and "usa-alert__text" in classes:
            self._text, self._text_level = [], self._alert_level
        elif tag == "span" and "usa-error-message" in classes:
            self._text, self._text_level = [], "field"
        elif tag == "title":
            self._in_title = True
        elif tag == "form":
            self.forms.append(Form(action=a.get("action", ""), method=a.get("method", "get")))
        elif tag == "input" and self.forms and a.get("name"):
            self._input(self.forms[-1], a)
        elif tag == "select" and self.forms:
            self._select = a.get("name")
            if self._select:
                self.forms[-1].choices.setdefault(self._select, [])
        elif tag == "option" and self.forms and self._select:
            form = self.forms[-1]
            value = a.get("value", "")
            form.choices[self._select].append(value)
            if "selected" in a or self._select not in form.fields:
                form.fields[self._select] = value
        elif tag == "link" and a.get("rel") in ("stylesheet", "icon"):
            self.assets.append(a.get("href", ""))
        elif tag in ("script", "img") and a.get("src"):
            self.assets.append(a["src"])

    def _input(self, form: Form, a: dict[str, str]) -> None:
        name, kind, value = a["name"], a.get("type", "text"), a.get("value", "")
        if kind in ("radio", "checkbox"):
            form.choices.setdefault(name, []).append(value or "on")
            if "checked" in a or (kind == "radio" and name not in form.fields):
                form.fields[name] = value or "on"
        elif kind not in ("submit", "button"):
            form.fields[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "span") and self._text is not None:
            text = " ".join("".join(self._text).split())
            if text:
                self.alerts.append((self._text_level, text))
            self._text = None
        elif tag == "title":
            self._in_title = False
        elif tag == "select":
            self._select = None

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)
        if self._in_title:
            self.title += data

    def messages(self, *levels: str) -> list[str]:
        return [text for level, text in self.alerts if not levels or level in levels]


@dataclass
class Response:
    status: int
    location: str
    body: str
    ms: float
    error: str = ""

    def page(self) -> Page:
        return Page(self.body)


class Client:
    """One person's browser: their cookies, with a new connection per request.

    Gunicorn's sync workers close the connection after every response, so
    there is nothing to keep alive.
    """

    def __init__(
        self, base_url: str, recorder: Recorder, who: str, *, timeout: float = 60.0
    ) -> None:
        parts = urlsplit(base_url)
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or 80
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.recorder = recorder
        self.who = who
        self.timeout = timeout
        self.cookies: dict[str, str] = {}
        self.seen_assets: set[str] = set()

    def clone(self) -> Client:
        """A second tab: same cookies, its own requests."""
        twin = Client(
            f"http://{self.host}:{self.port}", self.recorder, self.who, timeout=self.timeout
        )
        twin.cookies = dict(self.cookies)
        twin.seen_assets = self.seen_assets
        return twin

    def get(self, path: str, kind: str, *, htmx: bool = False) -> Response:
        headers = {"HX-Request": "true"} if htmx else {}
        return self.request("GET", path, kind, headers=headers)

    def post(self, path: str, data: dict[str, str], kind: str) -> Response:
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Origin": self.origin}
        return self.request("POST", path, kind, body=urlencode(data), headers=headers)

    def fetch_assets(self, page: Page) -> None:
        """Fetch the page's styles, scripts and images once, like a browser's cold cache."""
        for url in page.assets:
            if url.startswith("/static/") and url not in self.seen_assets:
                self.seen_assets.add(url)
                self.request("GET", url, "static", headers={"Accept-Encoding": "gzip"})

    def request(
        self,
        method: str,
        path: str,
        kind: str,
        *,
        body: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        sent = {
            "Host": f"{self.host}:{self.port}",
            "User-Agent": "sympraxi-load-test",
            "Accept": "text/html",
            **(headers or {}),
        }
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        at = self.recorder.now()
        start = time.perf_counter()
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body=body, headers=sent)
            raw = conn.getresponse()
            data = raw.read()
            ms = (time.perf_counter() - start) * 1000
            for header in raw.headers.get_all("Set-Cookie") or []:
                self._remember(header)
            response = Response(
                status=raw.status,
                location=raw.headers.get("Location", ""),
                body=data.decode("utf-8", errors="replace") if kind != "static" else "",
                ms=ms,
            )
        except (OSError, http.client.HTTPException) as exc:
            ms = (time.perf_counter() - start) * 1000
            response = Response(status=0, location="", body="", ms=ms, error=repr(exc))
        finally:
            conn.close()
        self.recorder.add(
            Sample(kind, method, response.status, response.ms, at, self.who, response.error)
        )
        return response

    def _remember(self, header: str) -> None:
        cookie: SimpleCookie = SimpleCookie()
        cookie.load(header)
        for name, morsel in cookie.items():
            if morsel["max-age"] == "0" or "1970" in morsel["expires"]:
                self.cookies.pop(name, None)
            else:
                # Send back exactly what the server set, quotes included.
                self.cookies[name] = morsel.coded_value
