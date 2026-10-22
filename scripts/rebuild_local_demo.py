"""Drop and rebuild a local demo database, so a recording starts from a known state.

    DATABASE_URL=postgresql://user@127.0.0.1:5432/sympraxi_demo python scripts/rebuild_local_demo.py

It reads DATABASE_URL for the database to rebuild, which it DROPS if it exists, then runs
the migrations and `seed_demo` with demo mode on. Use a throwaway database name.
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("Set DATABASE_URL to the demo database to rebuild.", file=sys.stderr)
        return 2
    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if "demo" not in name:
        print(f"Refusing to drop {name!r}: its name should contain 'demo'.", file=sys.stderr)
        return 2
    admin = url.replace(f"/{name}", "/postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (name,)
        )
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}"')
    env = {
        **os.environ,
        "SYMPRAXI_DEBUG": "true",
        "SYMPRAXI_DEMO": "true",
        "SYMPRAXI_SECRET_KEY": os.environ.get("SYMPRAXI_SECRET_KEY", "x" * 50),
    }
    for command in (
        ["migrate", "-v", "0"],
        ["createcachetable"],
        ["load_catalog"],
        ["load_content_pack"],
        ["seed_demo"],
    ):
        done = subprocess.run(  # noqa: S603
            [sys.executable, "manage.py", *command], cwd=ROOT, env=env, check=False
        )
        if done.returncode:
            return done.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
