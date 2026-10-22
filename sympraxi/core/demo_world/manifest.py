"""The list of recorded demo videos, read from the manifest the recorder writes.

`scripts/record_demo_videos.py` writes `video_manifest.json` beside this file: one entry
per video with its title, length, steps (the captions, with their start times) and where
to try the task. Before anything is recorded the manifest is missing, and the demo pages
simply show no videos.
"""

import json
from pathlib import Path
from typing import Any

from sympraxi.core.demo_world import faq, videos

MANIFEST = Path(__file__).with_name("video_manifest.json")


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def load() -> list[dict[str, Any]]:
    """Every recorded video, in the order of the scripts, with display fields added."""
    if not MANIFEST.exists():
        return []
    recorded = {row["slug"]: row for row in json.loads(MANIFEST.read_text(encoding="utf-8"))}
    audiences = dict(videos.AUDIENCES)
    found = []
    for scenario in videos.SCENARIOS:
        row = recorded.get(scenario.slug)
        if row is None:
            continue
        found.append(
            {
                **row,
                "audience_label": audiences[scenario.audience],
                "length": _clock(row["duration"]),
                "steps": [{**s, "at": _clock(s["start"])} for s in row["steps"]],
                "faq_entries": [faq.BY_ID[i] for i in scenario.faq if i in faq.BY_ID],
            }
        )
    return found


def by_slug(slug: str) -> dict[str, Any] | None:
    return next((v for v in load() if v["slug"] == slug), None)


def by_audience() -> list[tuple[str, list[dict[str, Any]]]]:
    """The videos grouped for the front page: (heading, videos), skipping empty groups."""
    everything = load()
    groups = []
    for key, label in videos.AUDIENCES:
        mine = [v for v in everything if v["audience"] == key]
        if mine:
            groups.append((label, mine))
    return groups
