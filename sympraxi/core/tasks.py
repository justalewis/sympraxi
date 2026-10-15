import base64
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
    # Messages queued before attachments were supported have no "attachments" key.
    for item in message.get("attachments", []):
        content: str | bytes = (
            base64.b64decode(item["base64"]) if "base64" in item else item["text"]
        )
        email.attach(item["filename"] or None, content, item["mimetype"] or None)
    email.send()
