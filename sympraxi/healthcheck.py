"""Container health checks.

`python -m sympraxi.healthcheck` calls /healthz on the local web server with
the site's own host name, so the request passes ALLOWED_HOSTS.

`python -m sympraxi.healthcheck scheduler` checks that the scheduler
(`manage.py scheduler`) finished a sweep in the last three minutes. After each
sweep that runs without an error, the scheduler writes the time to a heartbeat
file (`SYMPRAXI_SCHEDULER_HEARTBEAT`, by default `sympraxi-scheduler-heartbeat`
in the temporary directory). A sweep that can't reach the database writes
nothing, so the check fails while the scheduler is stuck, not only when its
process has died.

Both exit 0 when healthy. Neither loads Django, so they stay cheap to run often.
"""

import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

# Sweeps run every minute; three minutes allows for one slow sweep.
SCHEDULER_MAX_AGE = 180


def heartbeat_path() -> Path:
    configured = os.environ.get("SYMPRAXI_SCHEDULER_HEARTBEAT", "")
    return Path(configured or Path(tempfile.gettempdir()) / "sympraxi-scheduler-heartbeat")


def write_heartbeat(now: float | None = None) -> None:
    """Record that a sweep just finished. Written to a temporary file and renamed into place."""
    path = heartbeat_path()
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(f"{now if now is not None else time.time():.0f}\n")
    os.replace(partial, path)


def scheduler_is_healthy(now: float | None = None, max_age: int = SCHEDULER_MAX_AGE) -> bool:
    try:
        last = float(heartbeat_path().read_text().strip())
    except (OSError, ValueError):
        return False
    now = time.time() if now is None else now
    return now - max_age <= last <= now + 60


def web_is_healthy() -> bool:
    host = os.environ.get("SYMPRAXI_ALLOWED_HOSTS", "localhost").split(",")[0].strip().lstrip(".")
    request = urllib.request.Request(
        "http://127.0.0.1:8000/healthz", headers={"Host": host or "localhost"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 (fixed local URL)
            return bool(response.status == 200)
    except Exception:
        return False


def main(argv: list[str]) -> int:
    if argv[1:] == ["scheduler"]:
        return 0 if scheduler_is_healthy() else 1
    return 0 if web_is_healthy() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
