#!/usr/bin/env python3
"""Check Sympraxi's writing-resource catalog and topic vocabulary.

    python catalog/validate.py            structure, licenses, topics, cached text
    python catalog/validate.py --fetch    also fetch source pages for the text check

What it checks
  vocabulary.yaml   slug format, unique slugs, parents exist, one-sentence
                    definitions, deprecated terms point to a replacement
  publishers.yaml   required fields and known license codes
  resources/*.yaml  required fields, license codes that agree with
                    publishers.yaml, topic slugs that exist in vocabulary.yaml,
                    unique ids and URLs, and summaries that do not closely
                    match the source page's own text or meta description

The text check reads page text from a local cache (catalog/.cache/, which must
never be committed: it holds third-party text). --fetch fills the cache. It
obeys robots.txt and crawl delays, identifies itself, and never fetches
entries marked link_check: manual or off. Entries with no cached text are
reported as unchecked, and --require-text turns that into an error.

Needs Python 3.9+ and PyYAML.
"""

import argparse
import datetime as dt
import difflib
import hashlib
import html.parser
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

try:
    import yaml
except ImportError:
    sys.exit("validate.py needs PyYAML: pip install pyyaml")

HERE = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = (
    "Mozilla/5.0 (compatible; sympraxi-catalog-check/1.0; Sympraxi writing resource catalog validator)"
)
DEFAULT_DELAY = 2.0

# License codes. True means the material may be copied into the content pack
# (CC BY and CC0 only; see docs/03-resource-library.md).
LICENSES = {
    "CC0-1.0": True,
    "CC-BY-4.0": True,
    "CC-BY-3.0": True,
    "CC-BY": True,  # CC BY, version not confirmed
    "CC-BY-SA-4.0": False,
    "CC-BY-NC": False,  # CC BY-NC, version not confirmed
    "CC-BY-NC-4.0": False,
    "CC-BY-NC-SA-3.0": False,
    "CC-BY-NC-SA-4.0": False,
    "CC-BY-NC-ND-3.0": False,
    "CC-BY-NC-ND-4.0": False,
    "all-rights-reserved": False,
}
LINK_CHECK = {"auto", "manual", "off"}
AUDIENCES = {"undergraduate", "graduate", "tutor"}
AXES = ("concern", "stage", "action")
REQUIRED = (
    "id",
    "title",
    "url",
    "publisher",
    "license",
    "can_vendor",
    "link_check",
    "summary",
    "topics",
    "audience",
    "language",
    "last_checked",
    "last_status",
)
OPTIONAL = ("vendor_source", "license_note", "notes")

PART = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SLUG = {
    "concern": re.compile(rf"^{PART}(?::{PART})?$"),
    "stage": re.compile(rf"^{PART}$"),
    "action": re.compile(rf"^{PART}$"),
}
ID_RE = re.compile(rf"^{PART}$")
SENTENCE_BREAK = re.compile(r"[.!?][\"')\]]?\s+[A-Z]")

# Text-similarity thresholds for summaries.
RUN_LIMIT = 8  # longest run of words shared with the page
COVERAGE_LIMIT = 0.30  # share of the summary's 5-word sequences found on the page
META_LIMIT = 0.50  # similarity ratio against a meta description


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")


def load_yaml(path, report):
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        report.error(os.path.relpath(path, HERE), f"cannot read: {e}")
        return None


# ---------------------------------------------------------------- vocabulary


def check_vocabulary(vocab, report):
    """Return {axis: {slug: term}} for the topic check."""
    where = "vocabulary.yaml"
    terms_by_axis = {a: {} for a in AXES}
    if not isinstance(vocab, dict):
        report.error(where, "top level must be a mapping")
        return terms_by_axis
    for key in ("version", "license", "axes"):
        if key not in vocab:
            report.error(where, f"missing '{key}'")
    axes = vocab.get("axes") or {}
    seen = {}
    for axis in AXES:
        spec = axes.get(axis)
        if not isinstance(spec, dict):
            report.error(where, f"missing axis '{axis}'")
            continue
        for key in ("label", "definition", "terms"):
            if not spec.get(key):
                report.error(f"{where} [{axis}]", f"missing '{key}'")
        labels = set()
        for term in spec.get("terms") or []:
            slug = term.get("slug")
            tw = f"{where} [{axis}] {slug}"
            if not isinstance(slug, str) or not SLUG[axis].match(slug):
                report.error(
                    tw,
                    "slug must be lowercase words joined by hyphens"
                    + (", with at most one colon" if axis == "concern" else ""),
                )
                continue
            if slug in seen:
                report.error(tw, f"slug already used on the {seen[slug]} axis")
            seen[slug] = axis
            terms_by_axis[axis][slug] = term
            for key in ("label", "definition"):
                if not isinstance(term.get(key), str) or not term[key].strip():
                    report.error(tw, f"missing '{key}'")
            label = (term.get("label") or "").lower()
            if label in labels:
                report.error(tw, f"label '{term.get('label')}' repeats another term's")
            labels.add(label)
            d = (term.get("definition") or "").strip()
            if d and (not d.endswith(".") or SENTENCE_BREAK.search(d)):
                report.error(tw, "definition must be one sentence ending in a period")
            unknown = set(term) - {"slug", "label", "definition", "deprecated", "replaced_by"}
            if unknown:
                report.error(tw, f"unknown fields: {', '.join(sorted(unknown))}")
    for axis, terms in terms_by_axis.items():
        for slug, term in terms.items():
            tw = f"{where} [{axis}] {slug}"
            if ":" in slug and slug.split(":")[0] not in terms:
                report.error(tw, f"parent '{slug.split(':')[0]}' is not defined")
            if term.get("deprecated"):
                repl = term.get("replaced_by")
                if not repl or repl not in terms or terms[repl].get("deprecated"):
                    report.error(tw, "deprecated term needs replaced_by naming an active term")
    return terms_by_axis


# ---------------------------------------------------------------- publishers


def check_publishers(pubs, report):
    where = "publishers.yaml"
    out = {}
    if not isinstance(pubs, list):
        report.error(where, "must be a list")
        return out
    for p in pubs:
        name = p.get("name")
        pw = f"{where} [{name}]"
        for key in (
            "name",
            "hosts",
            "license",
            "license_as_documented",
            "can_vendor",
            "link_check",
        ):
            if key not in p:
                report.error(pw, f"missing '{key}'")
        if p.get("license") not in LICENSES:
            report.error(pw, f"unknown license code '{p.get('license')}'")
        for code in p.get("also_allowed") or []:
            if code not in LICENSES:
                report.error(pw, f"unknown license code '{code}' in also_allowed")
        if p.get("link_check") not in LINK_CHECK:
            report.error(pw, "link_check must be auto, manual or off")
        vs = p.get("vendor_source")
        if vs and LICENSES.get(vs.get("license")) is not True:
            report.error(pw, "vendor_source license must be CC BY or CC0")
        if p.get("can_vendor") and not (LICENSES.get(p.get("license")) or vs):
            report.error(pw, "can_vendor is true but nothing from this publisher may be copied")
        if name in out:
            report.error(pw, "listed twice")
        out[name] = p
    return out


# ---------------------------------------------------------------- entries


def check_entry(e, where, pubs, terms_by_axis, report):
    eid = e.get("id")
    ew = f"{where} {eid}"
    for key in REQUIRED:
        if key not in e:
            report.error(ew, f"missing '{key}'")
    unknown = set(e) - set(REQUIRED) - set(OPTIONAL)
    if unknown:
        report.error(ew, f"unknown fields: {', '.join(sorted(unknown))}")
    if not isinstance(eid, str) or not ID_RE.match(eid):
        report.error(ew, "id must be lowercase words joined by hyphens")
    if not isinstance(e.get("title"), str) or not e["title"].strip():
        report.error(ew, "title is empty")

    pub = pubs.get(e.get("publisher"))
    if pub is None:
        report.error(ew, f"publisher '{e.get('publisher')}' is not in publishers.yaml")

    url = e.get("url") or ""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        report.error(ew, "url must be an https URL")
    elif pub and parts.hostname not in pub.get("hosts", []):
        report.error(ew, f"url host {parts.hostname} does not belong to {pub['name']}")

    lic = e.get("license")
    if lic not in LICENSES:
        report.error(ew, f"unknown license code '{lic}'")
    elif pub and lic != pub.get("license") and lic not in (pub.get("also_allowed") or []):
        report.error(
            ew,
            f"license {lic} does not match publishers.yaml ({pub.get('license')}); "
            "record the license exactly as docs/03-resource-library.md gives it",
        )

    vs = e.get("vendor_source")
    if pub and pub.get("vendor_source"):
        pvs = pub["vendor_source"]
        if not isinstance(vs, dict):
            report.error(ew, "this publisher needs a vendor_source pointing at the pinned commit")
        else:
            if not str(vs.get("url", "")).startswith(f"{pvs['repository']}/blob/{pvs['commit']}/"):
                report.error(
                    ew,
                    f"vendor_source url must be in {pvs['repository']} at commit {pvs['commit'][:7]}",
                )
            if vs.get("license") != pvs.get("license"):
                report.error(ew, f"vendor_source license must be {pvs.get('license')}")
    elif vs is not None:
        report.error(ew, "vendor_source is only allowed for publishers that define one")

    cv = e.get("can_vendor")
    if not isinstance(cv, bool):
        report.error(ew, "can_vendor must be true or false")
    elif cv:
        ok = LICENSES.get(lic) is True or (
            isinstance(vs, dict) and LICENSES.get(vs.get("license")) is True
        )
        if not ok:
            report.error(ew, "can_vendor is true but the material is not CC BY or CC0")
        if pub and not pub.get("can_vendor"):
            report.error(
                ew, "can_vendor is true but publishers.yaml says this publisher can't be vendored"
            )

    lc = e.get("link_check")
    if lc not in LINK_CHECK:
        report.error(ew, "link_check must be auto, manual or off")
    elif pub and pub.get("link_check") == "manual" and lc != "manual":
        report.error(ew, f"{pub['name']} bans robots, so link_check must be manual")

    summary = e.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        report.error(ew, "summary is empty")
    else:
        n = len(summary.split())
        if n < 12:
            report.error(ew, f"summary is too short to be useful ({n} words)")
        elif n > 80:
            report.warn(ew, f"summary is long ({n} words); aim for 20 to 60")
        if e.get("title") and summary.strip().lower().startswith(str(e["title"]).lower()):
            report.warn(ew, "summary opens by repeating the title")

    topics = e.get("topics")
    if not isinstance(topics, dict) or not topics.get("concern"):
        report.error(ew, "topics needs at least one concern")
    else:
        for axis, slugs in topics.items():
            if axis not in AXES:
                report.error(ew, f"unknown topic axis '{axis}'")
                continue
            if not isinstance(slugs, list):
                report.error(ew, f"topics.{axis} must be a list")
                continue
            if len(set(slugs)) != len(slugs):
                report.error(ew, f"topics.{axis} repeats a slug")
            for s in slugs:
                term = terms_by_axis[axis].get(s)
                if term is None:
                    report.error(ew, f"topic '{s}' is not a {axis} term in vocabulary.yaml")
                elif term.get("deprecated"):
                    report.error(ew, f"topic '{s}' is deprecated; use '{term.get('replaced_by')}'")

    aud = e.get("audience")
    if not isinstance(aud, list) or not aud or set(aud) - AUDIENCES:
        report.error(ew, f"audience must be a list drawn from {sorted(AUDIENCES)}")
    if not re.match(r"^[a-z]{2}(-[A-Z]{2})?$", str(e.get("language", ""))):
        report.error(ew, "language must be a two-letter code such as en or es")

    checked, status = e.get("last_checked"), e.get("last_status")
    if checked is None or status is None:
        if lc in ("manual", "off"):
            report.warn(ew, "not yet checked by hand (last_checked is empty)")
        else:
            report.error(ew, "last_checked and last_status are required for link_check: auto")
    if checked is not None:
        if not isinstance(checked, dt.date):
            report.error(ew, "last_checked must be a date (YYYY-MM-DD)")
        elif checked > dt.date.today():
            report.error(ew, "last_checked is in the future")
    if status is not None and not isinstance(status, int):
        report.error(ew, "last_status must be an HTTP status code")
    elif isinstance(status, int) and status != 200:
        report.warn(ew, f"last_status is {status}")


# ---------------------------------------------------------------- source text


class TextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.chunks = []
        self.meta = []

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            a = dict(attrs)
            key = (a.get("name") or a.get("property") or "").lower()
            if key in ("description", "og:description", "twitter:description") and a.get("content"):
                self.meta.append(a["content"])
        elif tag in self.SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth:
            self.chunks.append(data)


def html_to_text(raw):
    p = TextExtractor()
    p.feed(raw)
    return " ".join(p.chunks), p.meta


class Fetcher:
    def __init__(self, cache_dir, refresh=False):
        self.cache_dir = cache_dir
        self.refresh = refresh
        self.robots = {}
        self.last = {}
        os.makedirs(cache_dir, exist_ok=True)

    def path(self, url):
        return os.path.join(self.cache_dir, hashlib.sha1(url.encode()).hexdigest() + ".json")

    def cached(self, url):
        try:
            with open(self.path(url), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _robots(self, host):
        if host not in self.robots:
            rp = urllib.robotparser.RobotFileParser()
            req = urllib.request.Request(
                f"https://{host}/robots.txt", headers={"User-Agent": USER_AGENT}
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    rp.parse(r.read().decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.allow_all = True
            except OSError:
                rp.disallow_all = True
            self.robots[host] = rp
        return self.robots[host]

    def get(self, url):
        hit = self.cached(url)
        if hit and not self.refresh and hit.get("status") is not None:
            return hit
        host = urllib.parse.urlsplit(url).hostname
        rp = self._robots(host)
        if not rp.can_fetch(USER_AGENT, url):
            return {"url": url, "status": None, "error": "robots.txt disallows this URL"}
        delay = float(rp.crawl_delay(USER_AGENT) or DEFAULT_DELAY)
        wait = self.last.get(host, 0) + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        rec = {"url": url, "fetched": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read(4_000_000).decode(
                    r.headers.get_content_charset() or "utf-8", "replace"
                )
                text, meta = html_to_text(raw)
                rec |= {"status": r.status, "final_url": r.geturl(), "text": text, "meta": meta}
        except urllib.error.HTTPError as e:
            rec |= {"status": e.code, "error": str(e)}
        except OSError as e:
            rec |= {"status": None, "error": repr(e)}
        self.last[host] = time.time()
        with open(self.path(url), "w", encoding="utf-8") as f:
            json.dump(rec, f)
        return rec


def snapshot_text(vendor_url, checkouts):
    """Text of a vendor_source file read from a local git checkout, or None."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/([0-9a-f]+)/(.+)$", vendor_url or "")
    if not m:
        return None
    owner, repo, sha, path = m.groups()
    for d in checkouts:
        try:
            origin = subprocess.run(
                ["git", "-C", d, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if not re.search(rf"[/:]{re.escape(owner)}/{re.escape(repo)}(\.git)?$", origin):
                continue
            raw = subprocess.run(
                ["git", "-C", d, "show", f"{sha}:{path}"], capture_output=True, check=True
            ).stdout.decode("utf-8", "replace")
            return re.sub(r"<[^>]+>", " ", raw)
        except (OSError, subprocess.CalledProcessError):
            continue
    return None


def words(s):
    s = unicodedata.normalize("NFKD", s).replace("’", "'").replace("‘", "'").lower()
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", s)


def ngrams(ws, n):
    return {tuple(ws[i : i + n]) for i in range(len(ws) - n + 1)}


def compare(summary, text, metas):
    """Return a list of problems if the summary tracks the source too closely."""
    sw, tw = words(summary), words(text)
    problems = []
    if sw and tw:
        m = difflib.SequenceMatcher(None, sw, tw, autojunk=False).find_longest_match(
            0, len(sw), 0, len(tw)
        )
        if m.size >= RUN_LIMIT:
            problems.append(
                f'shares a run of {m.size} words with the page: "{" ".join(sw[m.a : m.a + m.size])}"'
            )
        grams = ngrams(sw, 5)
        if grams:
            cover = len(grams & ngrams(tw, 5)) / len(grams)
            if cover >= COVERAGE_LIMIT:
                problems.append(f"has {cover:.0%} of its five-word sequences on the page")
    for meta in metas:
        ratio = difflib.SequenceMatcher(None, sw, words(meta), autojunk=False).ratio()
        if ratio >= META_LIMIT:
            problems.append(f"is {ratio:.0%} similar to the page's meta description")
    return problems


def check_text(entries, fetcher, fetch, checkouts, report):
    counts = {"checked": 0, "manual": 0, "unchecked": 0}
    for where, e in entries:
        ew = f"{where} {e.get('id')}"
        if e.get("link_check") in ("manual", "off"):
            counts["manual"] += 1
            continue
        url = e.get("url")
        rec = fetcher.get(url) if fetch else fetcher.cached(url)
        if not rec or rec.get("status") != 200:
            counts["unchecked"] += 1
            if rec and fetch:
                report.warn(
                    ew, f"could not read source page ({rec.get('status')}: {rec.get('error', '')})"
                )
            continue
        if fetch and e.get("last_status") not in (None, rec["status"]):
            report.warn(ew, f"page now returns {rec['status']}, entry says {e.get('last_status')}")
        sources = [("page", rec.get("text", ""), rec.get("meta", []))]
        vs = e.get("vendor_source")
        if isinstance(vs, dict):
            snap = snapshot_text(vs.get("url"), checkouts)
            if snap:
                sources.append(("vendor_source", snap, []))
        for label, text, metas in sources:
            for problem in compare(e.get("summary", ""), text, metas):
                report.error(ew, f"summary {problem} ({label}); rewrite it in fresh words")
        counts["checked"] += 1
    return counts


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fetch", action="store_true", help="fetch source pages that are not cached")
    ap.add_argument("--refresh", action="store_true", help="refetch every source page")
    ap.add_argument(
        "--cache", default=os.path.join(HERE, ".cache", "pages"), help="page text cache"
    )
    ap.add_argument(
        "--checkout",
        action="append",
        default=[],
        help="local git checkout for vendor_source text (repeatable)",
    )
    ap.add_argument(
        "--require-text",
        action="store_true",
        help="fail when an auto entry's source text is unavailable",
    )
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    report = Report()
    terms = check_vocabulary(load_yaml(os.path.join(HERE, "vocabulary.yaml"), report), report)
    pubs = check_publishers(load_yaml(os.path.join(HERE, "publishers.yaml"), report), report)

    entries, ids, urls = [], {}, {}
    res_dir = os.path.join(HERE, "resources")
    files = sorted(f for f in os.listdir(res_dir) if f.endswith((".yaml", ".yml")))
    for fn in files:
        where = f"resources/{fn}"
        data = load_yaml(os.path.join(res_dir, fn), report)
        if data is None:
            continue
        if not isinstance(data, list):
            report.error(where, "must be a list of entries")
            continue
        for e in data:
            if not isinstance(e, dict):
                report.error(where, "every entry must be a mapping")
                continue
            check_entry(e, where, pubs, terms, report)
            eid, url = e.get("id"), e.get("url")
            if eid in ids:
                report.error(f"{where} {eid}", f"id also used in {ids[eid]}")
            ids[eid] = where
            if url in urls:
                report.error(f"{where} {eid}", f"url also used by {urls[url]}")
            urls[url] = eid
            entries.append((where, e))

    fetcher = Fetcher(args.cache, refresh=args.refresh)
    counts = check_text(entries, fetcher, args.fetch or args.refresh, args.checkout, report)
    if args.require_text and counts["unchecked"]:
        report.error(
            "text check", f"{counts['unchecked']} entries have no source text; run with --fetch"
        )

    n_terms = sum(len(t) for t in terms.values())
    print(f"vocabulary: {n_terms} terms ({', '.join(f'{a} {len(terms[a])}' for a in AXES)})")
    print(f"publishers: {len(pubs)}")
    print(f"entries: {len(entries)} in {len(files)} files")
    print(
        f"text check: {counts['checked']} compared with their source, "
        f"{counts['manual']} manual entries skipped, {counts['unchecked']} without source text"
    )
    for w in report.warnings:
        print(f"  warning  {w}")
    for e in report.errors:
        print(f"  ERROR    {e}")
    ok = not report.errors
    print(
        f"{'PASS' if ok else 'FAIL'}: {len(report.errors)} errors, {len(report.warnings)} warnings"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
