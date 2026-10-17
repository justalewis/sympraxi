"""python -m tests.load: the first-Monday booking load test.

    run       make a fresh database, start Sympraxi, book, check, report
    prepare   only make the database and the center (for a server you start)
    check     check the database again and rewrite the report of an earlier run

See docs/development.md for how to run it and what it checks.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import sys
import tempfile
import time
from dataclasses import asdict, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

THRESHOLD_P95_MS = 1000.0
THRESHOLD_P99_MS = 2000.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.load", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Prepare, start Sympraxi, book, check and report.")
    prepare = commands.add_parser("prepare", help="Only make the database and the center.")
    check = commands.add_parser("check", help="Check an earlier run's database again.")
    for command in (run, prepare, check):
        command.add_argument(
            "--out",
            type=Path,
            default=Path(tempfile.gettempdir()) / "sympraxi-load",
            help="Folder for the logs, samples and report (default: %(default)s).",
        )
    for command in (run, prepare):
        command.add_argument("--students", type=int, default=500)
        command.add_argument("--front-desk", type=int, default=3)
    run.add_argument("--duration", type=float, default=600, help="Seconds students arrive over.")
    run.add_argument("--rush", type=float, default=0.3, help="Share arriving in the first tenth.")
    run.add_argument("--think", type=float, default=1.0, help="Scale for pauses; 0 for none.")
    run.add_argument("--double-click", type=float, default=0.05)
    run.add_argument("--sign-in", type=float, default=0.0, help="Share who sign in by password.")
    run.add_argument("--desk-pause", type=float, default=15.0)
    run.add_argument("--seed", type=int, default=1)
    run.add_argument("--workers", type=int, default=3, help="Web workers, as in the Dockerfile.")
    run.add_argument("--port", type=int, default=0, help="Port for the web workers (default: any).")
    run.add_argument("--no-scheduler", action="store_true", help="Don't run the scheduler.")
    run.add_argument("--url", help="Book against this running server instead (after prepare).")
    for command in (run, check):
        command.add_argument("--p95-ms", type=float, default=THRESHOLD_P95_MS)
        command.add_argument("--p99-ms", type=float, default=THRESHOLD_P99_MS)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.command == "check":
        configure(load_state(args.out))
        return finish(args, load_state(args.out))
    if args.command == "run" and args.url:
        state = load_state(args.out)
        configure(state)
    else:
        state = prepare_database(args)
        if args.command == "prepare":
            print(f"Prepared {state['database_url']} in {args.out}.")
            print("Start the server with the same DATABASE_URL and SYMPRAXI_SECRET_KEY:")
            print(f"  SYMPRAXI_SECRET_KEY={state['secret_key']}")
            return 0
    drive(args, state)
    return finish(args, state)


# --- Setup ----------------------------------------------------------------------


def configure(state: dict[str, Any]) -> None:
    """Point Django at the load-test database with production settings, minus HTTPS."""
    out = Path(state["out"])
    os.environ.update(
        {
            "DJANGO_SETTINGS_MODULE": "sympraxi.settings",
            # Ignore the developer's .env: everything the server needs is set here.
            "SYMPRAXI_ENV_FILE": str(out / "no-env-file"),
            "DATABASE_URL": state["database_url"],
            "SYMPRAXI_SECRET_KEY": state["secret_key"],
            "SYMPRAXI_DEBUG": "false",
            "SYMPRAXI_SSL_REDIRECT": "false",
            "SYMPRAXI_HSTS_SECONDS": "0",
            "SYMPRAXI_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "SYMPRAXI_STATIC_ROOT": str(out / "static"),
            "SYMPRAXI_SCHEDULER_HEARTBEAT": str(out / "scheduler-heartbeat"),
            "SYMPRAXI_BASE_URL": "http://127.0.0.1",
            "SYMPRAXI_QUEUE_EMAIL": "true",
            "EMAIL_URL": "dummymail://",
        }
    )
    import django

    django.setup()


def prepare_database(args: argparse.Namespace) -> dict[str, Any]:
    url = os.environ.get("DATABASE_URL", "")
    name = _database_name(url)
    if "load" not in name:
        sys.exit(
            "Set DATABASE_URL to a database whose name contains 'load', for example "
            "postgresql://postgres@127.0.0.1:5432/sympraxi_loadtest. The load test drops "
            "and recreates it."
        )
    state = {
        "out": str(args.out),
        "database_url": url,
        "secret_key": secrets.token_urlsafe(50),
        "password": secrets.token_urlsafe(16),
    }
    _recreate(url)
    configure(state)
    from django.contrib.auth.hashers import make_password
    from django.core.management import call_command
    from django.db import connections

    from tests.load import world

    started = time.monotonic()
    call_command("migrate", verbosity=0, interactive=False)
    call_command("createcachetable", verbosity=0)
    call_command("collectstatic", verbosity=0, interactive=False)
    manifest = world.build(
        students=args.students,
        front_desk=args.front_desk,
        password_hash=make_password(state["password"]),
    )
    connections.close_all()
    _write_json(args.out / "manifest.json", manifest)
    _write_json(args.out / "state.json", state)
    print(f"Database ready in {time.monotonic() - started:.0f} seconds.", flush=True)
    return state


def load_state(out: Path) -> dict[str, Any]:
    path = out / "state.json"
    if not path.is_file():
        sys.exit(f"No prepared run in {out}. Run `python -m tests.load prepare` first.")
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return state


def _database_name(url: str) -> str:
    import environ

    return str(environ.Env.db_url_config(url).get("NAME", "")) if url else ""


def _recreate(url: str) -> None:
    import environ
    import psycopg
    from psycopg import sql

    cfg = environ.Env.db_url_config(url)
    params = {
        "host": cfg.get("HOST") or None,
        "port": cfg.get("PORT") or None,
        "user": cfg.get("USER") or None,
        "password": cfg.get("PASSWORD") or None,
        "dbname": "postgres",
    }
    name = sql.Identifier(cfg["NAME"])
    with psycopg.connect(**params, autocommit=True) as conn:  # type: ignore[arg-type]
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(name))
        conn.execute(sql.SQL("CREATE DATABASE {}").format(name))


# --- The run --------------------------------------------------------------------


def drive(args: argparse.Namespace, state: dict[str, Any]) -> None:
    from django.db import connections

    from tests.load import server
    from tests.load.client import Recorder
    from tests.load.driver import DatabaseWatch, Options, Run, warm_up

    manifest = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
    options = Options(
        students=min(args.students, len(manifest["students"])),
        duration=args.duration,
        rush=args.rush,
        think=args.think,
        double_click=args.double_click,
        desk_pause=args.desk_pause,
        seed=args.seed,
        sign_in=args.sign_in,
        password=state["password"],
    )
    servers = None
    if not args.url:
        print(
            f"Starting {args.workers} web workers, the task worker and the scheduler.", flush=True
        )
        servers = server.start(
            port=args.port,
            workers=args.workers,
            log_dir=args.out,
            scheduler=not args.no_scheduler,
        )
    url = args.url or (servers.url if servers else "")
    warm_up(url, manifest, rounds=max(3, args.workers * 2))
    started = datetime.now(UTC)
    recorder = Recorder()
    watch = DatabaseWatch(state["database_url"], recorder)
    watch.start()
    run = Run(url, manifest, options, recorder)
    print(f"Booking against {url}: {options.students} students over {args.duration:.0f} s.")
    try:
        run.go()
        _wait_for_mail()
    finally:
        watch.stop.set()
        watch.join(timeout=30)
        if servers is not None:
            servers.stop()
        connections.close_all()

    _write_csv(args.out / "samples.csv", recorder.samples)
    _write_json(args.out / "attempts.json", [asdict(a) for a in run.attempts])
    _write_json(
        args.out / "run.json",
        {
            "students": options.students,
            "front_desk": len(manifest["front_desk"]),
            "duration": args.duration,
            "workers": args.workers if not args.url else None,
            "url": url,
            "started": started.isoformat(timespec="seconds"),
            "seconds": recorder.now(),
            "think": args.think,
            "seed": args.seed,
            "sign_ins": dict(_count(status for _, status in run.sign_ins)),
            "database": {
                "deadlocks": watch.after.get("deadlocks", 0) - watch.before.get("deadlocks", 0),
                "rollbacks": watch.after.get("rollbacks", 0) - watch.before.get("rollbacks", 0),
                "max_active": max((a for _, a, _ in watch.samples), default=0),
                "max_lock_waits": max((w for _, _, w in watch.samples), default=0),
                "samples": len(watch.samples),
                "samples_with_lock_waits": sum(1 for _, _, w in watch.samples if w),
                "waiting_queries": sorted(watch.waiting.items(), key=lambda kv: -kv[1]),
            },
        },
    )


def _wait_for_mail(seconds: float = 180, stalled: float = 20) -> None:
    """Let the task worker send the queued emails, so a failure shows up in the report.

    Stops early if the queue stops shrinking, as it does when a server started
    separately has no task worker.
    """
    from django_tasks_db.models import DBTaskResult

    deadline = time.monotonic() + seconds
    last, since = -1, time.monotonic()
    while time.monotonic() < deadline:
        waiting = DBTaskResult.objects.filter(status__in=["READY", "RUNNING"]).count()
        if not waiting:
            return
        if waiting != last:
            last, since = waiting, time.monotonic()
        elif time.monotonic() - since > stalled:
            return
        time.sleep(1)


def finish(args: argparse.Namespace, state: dict[str, Any]) -> int:
    from django.db.models import Count
    from django_tasks_db.models import DBTaskResult

    from tests.load import check, report
    from tests.load.client import Sample
    from tests.load.driver import Attempt

    manifest = json.loads((args.out / "manifest.json").read_text(encoding="utf-8"))
    run_info = json.loads((args.out / "run.json").read_text(encoding="utf-8"))
    attempts = [
        Attempt(**a) for a in json.loads((args.out / "attempts.json").read_text(encoding="utf-8"))
    ]
    samples = _read_csv(args.out / "samples.csv", Sample)
    world = check.load(manifest["center"]["id"])
    told = {(a.student, a.appointment) for a in attempts if a.outcome == "booked"}
    checks = check.run_all(world, told)
    timing = report.timings(samples)
    outcome = report.outcomes(attempts)
    seats = check.active_seats(world)
    groups = [a for a in check.active(world) if a.capacity > 1]
    summary = {
        "settings": run_info,
        "timings": timing,
        "attempts": outcome,
        "students": {
            "total": run_info["students"],
            "with_appointments": len({s.student for s in seats}),
            "appointments": len(check.active(world)),
            "seats": len(seats),
            "group_sessions": len(groups),
            "group_seats": sum(1 for s in seats if world.appointments[s.appointment].capacity > 1),
        },
        "sign_ins": run_info.get("sign_ins", {}),
        "database": run_info["database"],
        "email": {
            row["status"]: row["n"]
            for row in DBTaskResult.objects.values("status").annotate(n=Count("id"))
        },
        "checks": checks,
    }
    summary["failures"] = report.failures(
        timing, outcome, checks, p95_ms=args.p95_ms, p99_ms=args.p99_ms
    )
    if summary["email"].get("FAILED"):
        summary["failures"].append(f"emails that failed to send: {summary['email']['FAILED']}")
    text = report.markdown(summary)
    (args.out / "report.md").write_text(text, encoding="utf-8")
    _write_json(args.out / "report.json", summary)
    print(text)
    print(f"Logs, samples and the report are in {args.out}.")
    return 1 if summary["failures"] else 0


# --- Files ----------------------------------------------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def _write_csv(path: Path, rows: list[Any]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([fl.name for fl in fields(rows[0])] if rows else [])
        for row in rows:
            writer.writerow(asdict(row).values())


def _read_csv(path: Path, cls: Any) -> list[Any]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    types = {fl.name: fl.type for fl in fields(cls)}
    convert = {"int": int, "float": float, "str": str}
    return [cls(**{k: convert[str(types[k])](v) for k, v in row.items()}) for row in rows]


def _count(items: Any) -> dict[Any, int]:
    counts: dict[Any, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


if __name__ == "__main__":
    sys.exit(main())
