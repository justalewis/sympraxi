from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from sympraxi.centers.models import Center
from sympraxi.scheduling import history


class Command(BaseCommand):
    help = "Import a center's past appointments from a CSV (see sympraxi/scheduling/history.py)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("center", help="The center's slug.")
        parser.add_argument("path", type=Path, help="The CSV file.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Check the file and change nothing."
        )

    def handle(self, *args, **options) -> None:
        center = Center.objects.filter(slug=options["center"]).first()
        if center is None:
            raise CommandError(f"No center with the slug {options['center']!r}.")
        path: Path = options["path"]
        if not path.is_file():
            raise CommandError(f"{path} isn't a file.")
        result = history.run(center, path.read_bytes(), dry_run=options["dry_run"])
        if result.file_problem:
            raise CommandError(result.file_problem)
        for problem in result.problems:
            self.stderr.write(problem)
        verb = "Would import" if options["dry_run"] else "Imported"
        self.stdout.write(
            f"{verb} {result.imported} appointments; {result.already} were already there; "
            f"{len(result.problems)} rows skipped."
        )
