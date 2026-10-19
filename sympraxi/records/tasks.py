"""The weekly link check, run by the task worker (`manage.py db_worker`)."""

from django.tasks import task

from sympraxi.records import linkcheck
from sympraxi.records.models import LinkCheckRun


@task()
def check_links(run_id: str) -> None:
    started = LinkCheckRun.objects.filter(pk=run_id, finished_at__isnull=True).first()
    if started is not None:
        linkcheck.run(started)
