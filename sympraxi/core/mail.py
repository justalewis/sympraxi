"""An email backend that hands messages to the task worker instead of sending inline.

Pages that send email (sign-up, password reset, booking) return right away even
when the SMTP relay is slow or down; the worker sends and can be retried.

Task arguments must be JSON, so attachments travel as text (calendar files) or
base64 (anything binary). Only (filename, content, mimetype) attachments are
supported, not hand-built MIME parts.
"""

import base64
from collections.abc import Sequence

from django.core.mail import EmailMessage
from django.core.mail.backends.base import BaseEmailBackend


def serialize_attachments(message: EmailMessage) -> list[dict[str, str]]:
    result = []
    for attachment in message.attachments:
        if not isinstance(attachment, tuple):
            raise ValueError("QueuedEmailBackend only supports (filename, content, mimetype).")
        filename, content, mimetype = attachment
        item = {"filename": filename or "", "mimetype": mimetype or ""}
        if isinstance(content, bytes):
            item["base64"] = base64.b64encode(content).decode("ascii")
        else:
            item["text"] = str(content)
        result.append(item)
    return result


class QueuedEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages: Sequence[EmailMessage]) -> int:
        from sympraxi.core.tasks import deliver_email

        count = 0
        for message in email_messages:
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
                    "attachments": serialize_attachments(message),
                }
            )
            count += 1
        return count
