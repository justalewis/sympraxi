"""Sending webhooks, run by the task worker (`manage.py db_worker`)."""

from django.tasks import task

from sympraxi.integrations import webhooks


@task()
def deliver_webhook(delivery_id: str) -> None:
    webhooks.deliver(delivery_id)
