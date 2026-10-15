"""An email backend that hands messages to the task worker instead of sending inline.

Pages that send email (sign-up, password reset) return right away even when the
SMTP relay is slow or down; the worker sends and can be retried.
"""

from collections.abc import Sequence

from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend


class QueuedEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        from sympraxi.core.tasks import deliver_email

        count = 0
        for message in email_messages:
            if message.attachments:
                raise ValueError("QueuedEmailBackend does not support attachments yet.")
            deliver_email.enqueue(
                {
                    "subject": str(message.subject),
                    "body": str(message.body),
                    "from_email": message.from_email,
                    "to": list(message.to),
                    "cc": list(message.cc),
                    "bcc": list(message.bcc),
                    "reply_to": list(message.reply_to),
                    "headers": dict(message.extra_headers),
                    "alternatives": [
                        [str(content), mimetype]
                        for content, mimetype in getattr(message, "alternatives", [])
                    ],
                }
            )
            count += 1
        return count
