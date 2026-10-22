"""Record the demo's how-to videos by driving a running demo site with a browser.

Each video is a scenario in `sympraxi/core/demo_world/videos.py`: a persona to sign in
as and a list of steps, each with a caption. The recorder signs in, performs the steps
with a visible cursor and the caption on screen, and writes for each scenario:

    <out>/<slug>.webm   the video
    <out>/<slug>.vtt    the captions, as a WebVTT track viewers can switch on and off
    <out>/<slug>.png    a poster frame

plus `sympraxi/core/demo_world/video_manifest.json`, which the demo pages read for the
video list, the transcripts and the FAQ. Run it against a local demo site, seeded fresh:

    python scripts/record_demo_videos.py --base http://localhost:8010
    python scripts/record_demo_videos.py --only book-an-appointment
    python scripts/record_demo_videos.py --check     # run every step, record nothing

Videos are 1280 by 720. A recording changes the demo's data (it books, cancels, writes),
so record against a throwaway copy and reset the demo afterward.
"""

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sympraxi.core.demo_world.videos import PERSONA_NAMES, SCENARIOS, Scenario, Step  # noqa: E402

OUT_DEFAULT = ROOT / "sympraxi" / "static" / "demo" / "videos"
MANIFEST = ROOT / "sympraxi" / "core" / "demo_world" / "video_manifest.json"
SIZE = {"width": 1280, "height": 720}
WORDS_PER_SECOND = 3.0

OVERLAY = r"""
(() => {
  if (window.__demoOverlay) return;
  window.__demoOverlay = true;
  const css = `
    #__cursor{position:fixed;z-index:2147483647;width:22px;height:22px;border-radius:50%;
      background:rgba(0,94,162,.35);border:2px solid #005ea2;pointer-events:none;
      transform:translate(-50%,-50%);left:-50px;top:-50px;transition:transform .08s}
    #__ripple{position:fixed;z-index:2147483646;width:22px;height:22px;border-radius:50%;
      border:3px solid #005ea2;pointer-events:none;transform:translate(-50%,-50%) scale(1);opacity:0}
    #__ripple.go{animation:__r .5s ease-out}
    @keyframes __r{from{opacity:.9;transform:translate(-50%,-50%) scale(1)}
      to{opacity:0;transform:translate(-50%,-50%) scale(3)}}
    #__caption{position:fixed;z-index:2147483645;left:50%;bottom:22px;transform:translateX(-50%);
      max-width:78%;background:rgba(20,24,31,.93);color:#fff;font:600 22px/1.35 "Source Sans 3",
      "Source Sans Pro",system-ui,sans-serif;padding:12px 20px;border-radius:6px;text-align:center;
      opacity:0;transition:opacity .25s;pointer-events:none}
    .__hl{outline:4px solid #e8a317 !important;outline-offset:3px !important}
    body{padding-bottom:150px !important}
  `;
  const build = () => {
    if (document.getElementById('__cursor')) return;
    const style = document.createElement('style'); style.textContent = css;
    document.documentElement.appendChild(style);
    for (const id of ['__ripple', '__cursor', '__caption']) {
      const el = document.createElement('div'); el.id = id; document.documentElement.appendChild(el);
    }
    const c = document.getElementById('__cursor');
    const saved = JSON.parse(sessionStorage.getItem('__cur') || 'null');
    if (saved) { c.style.left = saved.x + 'px'; c.style.top = saved.y + 'px'; }
    const text = sessionStorage.getItem('__cap') || '';
    const cap = document.getElementById('__caption');
    cap.textContent = text; cap.style.opacity = text ? 1 : 0;
  };
  if (document.documentElement) build();
  document.addEventListener('DOMContentLoaded', build);
  addEventListener('mousemove', e => {
    build();
    const c = document.getElementById('__cursor');
    c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px';
    sessionStorage.setItem('__cur', JSON.stringify({x: e.clientX, y: e.clientY}));
  }, true);
  addEventListener('mousedown', e => {
    build();
    const r = document.getElementById('__ripple');
    r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
    r.classList.remove('go'); void r.offsetWidth; r.classList.add('go');
  }, true);
  window.__setCaption = text => {
    build();
    sessionStorage.setItem('__cap', text || '');
    const cap = document.getElementById('__caption');
    cap.textContent = text || ''; cap.style.opacity = text ? 1 : 0;
  };
})();
"""


def locate(page: Page, spec: str) -> Locator:
    """A step's target: `link:Text`, `button:Text`, `label:Text`, `text:Text`, `css:selector`."""
    kind, _, value = spec.partition(":")
    pattern = re.compile(re.escape(value), re.I)
    match kind:
        case "link" | "button" | "heading" | "checkbox" | "radio" | "tab" | "row":
            return page.get_by_role(kind, name=pattern).first  # type: ignore[arg-type]
        case "label":
            return page.get_by_label(pattern).first
        case "placeholder":
            return page.get_by_placeholder(pattern).first
        case "text":
            return page.get_by_text(pattern).first
        case "css":
            return page.locator(value).first
    raise ValueError(f"Unknown target kind in {spec!r}")


class Recorder:
    def __init__(self, page: Page, base: str, *, fast: bool) -> None:
        self.page, self.base, self.fast = page, base, fast
        self.t0 = time.monotonic()
        self.cues: list[dict[str, Any]] = []
        self.caption = ""
        day = dt.date.today() + dt.timedelta(days=1)
        while day.weekday() > 3:  # Monday to Thursday: every tutor in the demo works those days
            day += dt.timedelta(days=1)
        self.next_weekday = day.isoformat()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def set_caption(self, text: str) -> None:
        self.caption = text
        self.page.evaluate("t => window.__setCaption && window.__setCaption(t)", text)

    def pause(self, seconds: float) -> None:
        if not self.fast:
            self.page.wait_for_timeout(int(seconds * 1000))

    def settle(self) -> None:
        self.page.wait_for_load_state("domcontentloaded")
        # Re-draw the caption on the new page, in case the overlay raced the load.
        if self.caption:
            self.page.evaluate("t => window.__setCaption && window.__setCaption(t)", self.caption)

    def glide(self, target: Locator) -> None:
        target.scroll_into_view_if_needed()
        box = target.bounding_box()
        if box is None:
            return
        x, y = box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 24)
        self.page.mouse.move(x, y, steps=1 if self.fast else 28)
        self.pause(0.25)

    def perform(self, step: Step) -> None:
        page = self.page
        match step.do:
            case "goto":
                page.goto(self.base + step.target.replace("{next_weekday}", self.next_weekday))
                self.settle()
            case "click":
                element = locate(page, step.target)
                self.glide(element)
                element.click()
                self.settle()
            case "fill":
                element = locate(page, step.target)
                self.glide(element)
                element.click()
                typed = step.value.replace("{next_weekday}", self.next_weekday)
                if element.get_attribute("type") in ("date", "time"):
                    element.fill(typed)
                else:
                    element.fill("")
                    element.press_sequentially(typed, delay=0 if self.fast else 55)
            case "select":
                element = locate(page, step.target)
                self.glide(element)
                element.select_option(label=step.value)
            case "check":
                element = locate(page, step.target)
                # USWDS draws its own checkbox; the visible thing to click is the label.
                field_id = element.get_attribute("id")
                label = page.locator(f"label[for='{field_id}']").first if field_id else element
                self.glide(label)
                label.click()
            case "highlight":
                element = locate(page, step.target)
                self.glide(element)
                element.evaluate("e => e.classList.add('__hl')")
            case "scroll":
                element = locate(page, step.target)
                element.evaluate("e => e.scrollIntoView({behavior: 'smooth', block: 'center'})")
            case "wait" | "caption":
                pass
            case _:
                raise ValueError(f"Unknown step action {step.do!r}")

    def run(self, step: Step) -> None:
        started = self.now()
        if step.optional and step.target and step.do != "goto":
            try:
                locate(self.page, step.target).wait_for(timeout=2500)
            except Exception:
                return  # Not on this page with this data; the video carries on without it.
        if step.caption:
            self.set_caption(step.caption)
        for attempt in range(3):
            try:
                self.perform(step)
                break
            except Exception as error:
                # A page that re-renders under the cursor detaches the element; try once more.
                if attempt == 2 or "not attached" not in str(error):
                    raise
                self.page.wait_for_timeout(400)
        words = len(step.caption.split())
        hold = max(step.hold, words / WORDS_PER_SECOND) if step.caption else step.hold
        self.pause(hold)
        if step.caption:
            self.cues.append({"start": started, "text": step.caption, "end": 0.0})
        if step.do == "highlight":
            self.page.evaluate(
                "document.querySelectorAll('.__hl').forEach(e => e.classList.remove('__hl'))"
            )


def find_ffmpeg() -> str | None:
    """Playwright's own ffmpeg (it ships one for its recordings), or one on the PATH."""
    roots = [
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""),
        os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.expanduser("~/Library/Caches/ms-playwright"),
    ]
    for root in filter(None, roots):
        found = sorted(glob.glob(os.path.join(root, "ffmpeg-*", "ffmpeg*")))
        found = [f for f in found if os.path.isfile(f) and not f.endswith((".txt", ".md"))]
        if found:
            return found[-1]
    return shutil.which("ffmpeg")


def compress(path: Path) -> None:
    """Re-encode a recording as VP9 at a quality a screen recording doesn't need more than:
    about a fifth of the size, so the videos stay small enough to keep in the repository."""
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        print(f"  (no ffmpeg found; keeping {path.name} as recorded)", file=sys.stderr)
        return
    small = path.with_suffix(".small.webm")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(path),
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "38",
        "-b:v",
        "0",
        "-deadline",
        "good",
        "-cpu-used",
        "4",
        "-row-mt",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-an",
        str(small),
    ]
    done = subprocess.run(command, check=False)  # noqa: S603
    if done.returncode == 0 and small.exists() and small.stat().st_size < path.stat().st_size:
        small.replace(path)
    else:
        small.unlink(missing_ok=True)


def vtt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def write_vtt(path: Path, cues: list[dict[str, Any]]) -> None:
    lines = ["WEBVTT", ""]
    for n, cue in enumerate(cues, 1):
        lines += [str(n), f"{vtt_time(cue['start'])} --> {vtt_time(cue['end'])}", cue["text"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def sign_in(browser: Any, base: str, name: str, state_file: Path) -> None:
    context = browser.new_context(viewport=SIZE)
    page = context.new_page()
    page.goto(base + "/demo/")
    page.get_by_role("button", name=re.compile(f"Sign in as {re.escape(name)}")).first.click()
    page.wait_for_url(re.compile(r"/centers/demo/"))
    context.storage_state(path=str(state_file))
    context.close()


def record(
    browser: Any, scenario: Scenario, base: str, out: Path, *, fast: bool, compressing: bool = True
) -> dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="demo-video-"))
    state = tmp / "state.json"
    sign_in(browser, base, PERSONA_NAMES[scenario.persona], state)
    context = browser.new_context(
        storage_state=str(state),
        viewport=SIZE,
        record_video_dir=None if fast else str(tmp),
        record_video_size=SIZE,
        bypass_csp=True,
        locale="en-US",
        timezone_id="America/Los_Angeles",
    )
    context.add_init_script(OVERLAY)
    page = context.new_page()
    page.set_default_timeout(15000)
    rec = Recorder(page, base, fast=fast)
    try:
        for index, step in enumerate(scenario.steps):
            try:
                rec.run(step)
            except Exception as error:
                shot = out / f"FAILED-{scenario.slug}.png"
                page.screenshot(path=str(shot))
                raise RuntimeError(
                    f"{scenario.slug}: step {index + 1} ({step.do} {step.target!r}) failed: {error}"
                ) from error
            if index == 1 and not fast:
                page.screenshot(path=str(out / f"{scenario.slug}.png"))
        duration = rec.now()
    finally:
        video = page.video
        context.close()
    for position, cue in enumerate(rec.cues):
        cue["end"] = rec.cues[position + 1]["start"] if position + 1 < len(rec.cues) else duration
    result: dict[str, Any] = {
        "slug": scenario.slug,
        "title": scenario.title,
        "audience": scenario.audience,
        "persona": scenario.persona,
        "summary": scenario.summary,
        "duration": round(duration, 1),
        "try_url": scenario.try_url,
        "faq": list(scenario.faq),
        "steps": [{"text": cue["text"], "start": round(cue["start"], 1)} for cue in rec.cues],
    }
    if not fast and video is not None:
        shutil.move(video.path(), out / f"{scenario.slug}.webm")
        if compressing:
            compress(out / f"{scenario.slug}.webm")
        write_vtt(out / f"{scenario.slug}.vtt", rec.cues)
        result["bytes"] = (out / f"{scenario.slug}.webm").stat().st_size
    shutil.rmtree(tmp, ignore_errors=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--base", default="http://localhost:8010")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--only", action="append", default=[], help="a scenario slug; repeatable")
    parser.add_argument("--check", action="store_true", help="run the steps without recording")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-compress", action="store_true", help="keep recordings as captured")
    args = parser.parse_args()
    chosen = [s for s in SCENARIOS if not args.only or s.slug in args.only]
    if not chosen:
        print("No scenario matches.", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    if MANIFEST.exists() and not args.check:
        results = {r["slug"]: r for r in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        failures = 0
        for scenario in chosen:
            try:
                results[scenario.slug] = record(
                    browser,
                    scenario,
                    args.base,
                    args.out,
                    fast=args.check,
                    compressing=not args.no_compress,
                )
                print(f"ok  {scenario.slug}  {results[scenario.slug]['duration']}s")
            except Exception as error:
                failures += 1
                print(f"FAIL {error}", file=sys.stderr)
        browser.close()
    if not args.check:
        ordered = [results[s.slug] for s in SCENARIOS if s.slug in results]
        MANIFEST.write_text(json.dumps(ordered, indent=1) + "\n", encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
