"""Virus scans and PDF copies, run by the task worker (`manage.py db_worker`)."""

from django.tasks import task

from sympraxi.files import services


@task()
def scan_file(file_id: str) -> None:
    services.scan(file_id)


@task()
def convert_file(file_id: str) -> None:
    services.convert(file_id)
