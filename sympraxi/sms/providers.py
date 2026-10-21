"""Text message providers.

A provider sends one text and checks that an incoming webhook really came from it.
`console` only logs, so a center can try the whole flow without a provider account.
`twilio` posts to Twilio's REST API using the standard library, so it adds no package.
"""

import base64
import hashlib
import hmac
import http.client
import json
import logging
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest

logger = logging.getLogger("sympraxi.sms")


class SendError(Exception):
    """The provider didn't send it. `blocked` is true when the number can't be texted."""

    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked


@dataclass
class Sent:
    id: str


class ConsoleProvider:
    name = "console"

    def send(self, to: str, body: str) -> Sent:
        logger.info("Text to %s: %s", "***" + to[-4:], body)
        return Sent(id="console")

    def verify_inbound(self, request: HttpRequest) -> bool:
        # There is no provider to answer; a test can post with this header.
        return request.META.get("HTTP_X_SYMPRAXI_CONSOLE") == "inbound"


class TwilioProvider:
    name = "twilio"
    host = "api.twilio.com"

    def __init__(self, sid: str, token: str, sender: str) -> None:
        self.sid, self.token, self.sender = sid, token, sender

    def send(self, to: str, body: str) -> Sent:
        fields = {"To": to, "Body": body}
        fields["MessagingServiceSid" if self.sender.startswith("MG") else "From"] = self.sender
        auth = base64.b64encode(f"{self.sid}:{self.token}".encode()).decode()
        connection = http.client.HTTPSConnection(
            self.host, timeout=10, context=ssl.create_default_context()
        )
        try:
            connection.request(
                "POST",
                f"/2010-04-01/Accounts/{self.sid}/Messages.json",
                body=urlencode(fields),
                headers={
                    "Authorization": f"Basic {auth}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
            )
            answer = connection.getresponse()
            data = json.loads(answer.read(5000) or b"{}")
        except (OSError, http.client.HTTPException, ValueError) as error:
            raise SendError(f"couldn't reach Twilio: {error}") from error
        finally:
            connection.close()
        if 200 <= answer.status < 300 and data.get("sid"):
            return Sent(id=str(data["sid"]))
        code = data.get("code")
        # 21610: the person replied STOP to this sender. 21211 and 21614: not a mobile number.
        raise SendError(
            f"Twilio said {answer.status} {code}: {str(data.get('message', ''))[:120]}",
            blocked=code in (21610, 21211, 21614),
        )

    def verify_inbound(self, request: HttpRequest) -> bool:
        """Twilio signs the exact public URL plus the sorted form fields with the auth token."""
        given = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        base = str(settings.SYMPRAXI_BASE_URL).rstrip("/")
        signed = base + request.path
        fields: dict[str, Any] = request.POST
        for key in sorted(fields):
            signed += key + fields[key]
        mac = hmac.new(self.token.encode(), signed.encode(), hashlib.sha1).digest()
        expected = base64.b64encode(mac).decode()
        return bool(given) and hmac.compare_digest(given, expected)


def configured() -> ConsoleProvider | TwilioProvider | None:
    """The provider the host set up, or None when texts are off."""
    name = settings.SYMPRAXI_SMS_PROVIDER
    if name == "console":
        return ConsoleProvider()
    if name == "twilio":
        sid, token = settings.SYMPRAXI_TWILIO_ACCOUNT_SID, settings.SYMPRAXI_TWILIO_AUTH_TOKEN
        sender = settings.SYMPRAXI_TWILIO_FROM
        if sid and token and sender:
            return TwilioProvider(sid, token, sender)
    return None
