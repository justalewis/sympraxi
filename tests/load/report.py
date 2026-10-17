"""Turn a run's samples, attempts and checks into a verdict and a short report."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from tests.load.client import Sample
from tests.load.driver import Attempt

# Pages in the order a person meets them. Static files are reported but have no target.
KINDS = [
    "sign_in_form",
    "sign_in_post",
    "schedule_list",
    "open_times",
    "open_times_htmx",
    "book_form",
    "book_post",
    "appointment",
    "my_appointments",
    "staff_open_times",
    "staff_book_form",
    "staff_book_post",
    "day_sheet",
    "static",
]
UNTIMED = {"static"}


def percentile(ordered: list[float], p: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not ordered:
        return 0.0
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def timings(samples: Iterable[Sample]) -> dict[str, dict[str, Any]]:
    by_kind: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        by_kind[s.kind].append(s)
    order = {k: i for i, k in enumerate(KINDS)}
    result = {}
    for kind in sorted(by_kind, key=lambda k: order.get(k, len(KINDS))):
        rows = by_kind[kind]
        ms = sorted(r.ms for r in rows if r.status)
        result[kind] = {
            "requests": len(rows),
            "p50": round(percentile(ms, 50)),
            "p95": round(percentile(ms, 95)),
            "p99": round(percentile(ms, 99)),
            "max": round(ms[-1]) if ms else 0,
            "server_errors": sum(1 for r in rows if r.status >= 500),
            "no_response": sum(1 for r in rows if r.status == 0),
            "statuses": dict(sorted(Counter(r.status for r in rows).items())),
        }
    return result


def outcomes(attempts: list[Attempt]) -> dict[str, Any]:
    refused = [a for a in attempts if a.outcome == "refused"]
    booked = [a for a in attempts if a.outcome == "booked"]
    return {
        "attempts": len(attempts),
        "booked": len(booked),
        "booked_by_staff": sum(1 for a in booked if a.by_staff),
        "refused": len(refused),
        "errors": [f"{a.who}: {a.message}" for a in attempts if a.outcome == "error"],
        "double_clicks": sum(1 for a in attempts if a.double) // 2,
        "refusals_by_reason": dict(Counter(a.reason for a in refused).most_common()),
        "refusal_messages": dict(Counter(a.message for a in refused).most_common()),
    }


def failures(
    timing: dict[str, dict[str, Any]],
    attempts: dict[str, Any],
    checks: dict[str, list[str]],
    *,
    p95_ms: float,
    p99_ms: float,
) -> list[str]:
    """Everything that keeps the run from passing. Empty means it passed."""
    found = [f"{name}: {len(problems)}" for name, problems in checks.items() if problems]
    for kind, t in timing.items():
        if t["server_errors"] or t["no_response"]:
            found.append(
                f"{kind}: {t['server_errors']} server errors, {t['no_response']} without a response"
            )
        if kind in UNTIMED or kind.startswith("sign_in"):
            continue
        if t["p95"] > p95_ms:
            found.append(f"{kind}: p95 {t['p95']} ms is over {p95_ms:.0f} ms")
        if t["p99"] > p99_ms:
            found.append(f"{kind}: p99 {t['p99']} ms is over {p99_ms:.0f} ms")
    if attempts["errors"]:
        found.append(f"booking attempts that ended in an error: {len(attempts['errors'])}")
    return found


def markdown(summary: dict[str, Any]) -> str:
    s = summary
    lines = [
        "# Load test report",
        "",
        f"{s['settings']['students']} students arriving over {s['settings']['duration']:.0f} "
        f"seconds, {s['settings']['front_desk']} front desk workers, "
        f"{_server(s['settings']['workers'])}. Run started {s['settings']['started']} and "
        f"took {s['settings']['seconds']:.0f} seconds.",
        "",
        "**Result: " + ("passed**" if not s["failures"] else "failed**"),
        "",
    ]
    lines += [f"- {f}" for f in s["failures"]] + ([""] if s["failures"] else [])
    lines += [
        "## Response times (milliseconds)",
        "",
        "| Page | Requests | p50 | p95 | p99 | Max | 5xx | No answer |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for kind, t in s["timings"].items():
        lines.append(
            f"| {kind} | {t['requests']} | {t['p50']} | {t['p95']} | {t['p99']} | {t['max']} "
            f"| {t['server_errors']} | {t['no_response']} |"
        )
    a = s["attempts"]
    lines += [
        "",
        "## Booking attempts",
        "",
        f"{a['attempts']} attempts: {a['booked']} booked ({a['booked_by_staff']} by the front "
        f"desk), {a['refused']} refused with a message, {len(a['errors'])} errors. "
        f"{a['double_clicks']} forms were sent twice at once.",
        "",
        "| Refusal | Count |",
        "|---|---:|",
    ]
    lines += [f"| {msg} | {n} |" for msg, n in a["refusal_messages"].items()]
    if a["errors"]:
        lines += ["", "Errors:", ""] + [f"- {e}" for e in a["errors"][:20]]
    st = s["students"]
    lines += [
        "",
        "## Students",
        "",
        f"{st['with_appointments']} of {st['total']} students have at least one appointment "
        f"({st['appointments']} appointments, {st['seats']} seats). "
        f"{st['group_sessions']} group sessions hold {st['group_seats']} students.",
    ]
    if s.get("sign_ins"):
        lines += ["", "Password sign-ins by response status: " + _counts(s["sign_ins"])]
    db = s["database"]
    lines += [
        "",
        "## Database",
        "",
        f"Deadlocks during the run: {db['deadlocks']}. Rolled-back transactions: "
        f"{db['rollbacks']}. At most {db['max_active']} queries ran at once and "
        f"{db['max_lock_waits']} waited on a lock at once; a lock wait was seen in "
        f"{db['samples_with_lock_waits']} of {db['samples']} samples.",
    ]
    if db["waiting_queries"]:
        lines += ["", "Queries seen waiting on a lock (samples):", ""]
        lines += [f"- {n}: `{q}`" for q, n in db["waiting_queries"][:5]]
    lines += ["", f"Queued emails by status: {_counts(s['email'])}.", "", "## Checks", ""]
    for name, problems in s["checks"].items():
        lines.append(f"- {name}: {'none' if not problems else len(problems)}")
        lines += [f"  - {p}" for p in problems[:5]]
    return "\n".join(lines) + "\n"


def _server(workers: int | None) -> str:
    return f"{workers} web workers" if workers else "a server started separately"


def _counts(counts: dict[Any, int]) -> str:
    return ", ".join(f"{k}: {n}" for k, n in counts.items()) or "none"
