"""Mass email, queued by the task worker one copy per person (`news.queue_copies`)."""

from django.tasks import task

from sympraxi.notify import news


@task()
def queue_mass_email(mass_email_id: str, url_base: str) -> None:
    news.queue_copies(mass_email_id, url_base)
