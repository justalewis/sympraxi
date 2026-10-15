"""Load or update the topic vocabulary and shared resource catalog from catalog/."""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from sympraxi.records import catalog


class Command(BaseCommand):
    help = (
        "Load catalog/vocabulary.yaml and catalog/resources/*.yaml. Safe to run again: "
        "entries are matched by their permanent slugs and IDs, and nothing is deleted."
    )

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--path",
            type=Path,
            help="Folder with vocabulary.yaml and resources/ (default: the repository's catalog/).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            report = catalog.load(options["path"])
        except catalog.CatalogError as exc:
            raise CommandError(f"{exc}. Nothing was loaded.") from exc
        for line in report.lines():
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS("Catalog loaded."))
