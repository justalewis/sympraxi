"""Load or update the open-content pack from content-pack/."""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from sympraxi.records import contentpack


class Command(BaseCommand):
    help = (
        "Load the openly licensed items in content-pack/cc-by/. Items are matched by their "
        "permanent ids; one that has left the pack is marked inactive, not deleted. Material "
        "with a NonCommercial term is loaded only with --include-noncommercial."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--path", type=Path, help="The pack's folder (default: the repository's content-pack/)."
        )
        parser.add_argument(
            "--include-noncommercial",
            action="store_true",
            help="Also load content-pack/noncommercial/. Not for a host that sells Sympraxi.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            report = contentpack.load(
                options["path"], include_noncommercial=options["include_noncommercial"]
            )
        except contentpack.PackError as exc:
            raise CommandError(f"{exc}. Nothing was loaded.") from exc
        for line in report.lines():
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS("Content pack loaded."))
