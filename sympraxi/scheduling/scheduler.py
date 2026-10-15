"""The scheduler: one long-running process (`manage.py scheduler`) that runs the
minute-by-minute jobs.

Each minute it sends the reminders that are due (`reminders`) and flags possible
no-shows (`attendance.flag_possible_no_shows`). Both are safe to run twice at
once, so a second scheduler, or one started during an upgrade while the old one
is stopping, doesn't send anything twice. After a sweep that finishes without an
error it writes the heartbeat that `python -m sympraxi.healthcheck scheduler`
reads.

It runs as its own Compose service rather than inside the task worker: the
worker runs jobs it is handed, and something still has to decide each minute
which jobs are due.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from django.db import close_old_connections, connection
from django.utils import timezone

from sympraxi import healthcheck
from sympraxi.scheduling import attendance, reminders

logger = logging.getLogger("sympraxi.scheduler")


@dataclass
class Sweep:
    reminders: int
    flagged: int


def _refresh_connections() -> None:
    """Drop a database connection that is broken or past its age, as a request would.

    Never inside a transaction: there it would close the connection in use.
    """
    if not connection.in_atomic_block:
        close_old_connections()


def sweep(*, base_url: str, now: datetime | None = None) -> Sweep:
    """Run every job once. Raises if the database can't be reached."""
    _refresh_connections()
    try:
        now = now or timezone.now()
        return Sweep(
            reminders=reminders.send_due_reminders(base_url=base_url, now=now),
            flagged=attendance.flag_possible_no_shows(now),
        )
    finally:
        _refresh_connections()


def seconds_to_next_minute(now: float | None = None) -> float:
    now = time.time() if now is None else now
    return 60 - (now % 60)


def run(*, base_url: str, stop: threading.Event, once: bool = False) -> None:
    """Sweep at the start of every minute until `stop` is set."""
    logger.info("Scheduler started")
    while not stop.is_set():
        try:
            result = sweep(base_url=base_url)
        except Exception:
            logger.exception("Scheduler sweep failed; trying again next minute")
        else:
            healthcheck.write_heartbeat()
            if result.reminders or result.flagged:
                logger.info(
                    "Queued %s reminder emails; flagged %s possible no-shows",
                    result.reminders,
                    result.flagged,
                )
        if once:
            break
        stop.wait(seconds_to_next_minute())
    logger.info("Scheduler stopped")
