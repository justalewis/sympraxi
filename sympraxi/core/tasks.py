from typing import Any

from django.core.mail import EmailMultiAlternatives, mailers
from django.tasks import task


@task()
def deliver_email(message: dict[str, Any]) -> None:
    """Send one email through the real delivery backend (usually the campus SMTP relay)."""
    email = EmailMultiAlternatives(
        subject=message["subject"],
        body=message["body"],
        from_email=message["from_email"],
        to=message["to"],
        cc=message["cc"],
        bcc=message["bcc"],
        reply_to=message["reply_to"],
        headers=message["headers"],
        alternatives=[tuple(a) for a in message["alternatives"]],
        connection=mailers["delivery"],
    )
    email.send()
