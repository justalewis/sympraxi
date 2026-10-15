"""Send appointment reminders and flag possible no-shows, every minute, until stopped."""

import signal
import threading
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sympraxi.scheduling import scheduler


class Command(BaseCommand):
    help = (
        "Send appointment reminders and flag possible no-shows every minute, until stopped. "
        "Run one copy alongside the web server and the task worker (db_worker)."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--once", action="store_true", help="Run one sweep and exit, for cron or testing."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        base_url = str(settings.SYMPRAXI_BASE_URL).rstrip("/")
        if not base_url:
            raise CommandError(
                "Set SYMPRAXI_BASE_URL (for example https://writing.example.edu) so links in "
                "reminder emails work."
            )
        stop = threading.Event()
        if not options["once"]:
            # Docker stops a container with SIGTERM; finish the current sweep, then exit.
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
        scheduler.run(base_url=base_url, stop=stop, once=options["once"])
