"""Sending texts, run by the task worker (`manage.py db_worker`)."""

from django.tasks import task

from sympraxi.sms import service


@task()
def send_text(message_id: str) -> None:
    service.deliver(message_id)
