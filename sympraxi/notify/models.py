import hashlib

from django.conf import settings
from django.db import models


def feed_token_hash(token: str) -> str:
    """How a calendar feed's token is stored. Its own prefix keeps it from ever
    matching a survey link's hash (`sympraxi.forms.models.token_hash`)."""
    return hashlib.sha256(b"sympraxi-calendar:" + token.encode()).hexdigest()


class CalendarFeed(models.Model):
    """A person's private calendar feed: their appointments, at a secret address.

    Only a hash of the address's token is kept, so the address can't be read back
    from the database or a backup. Replacing the address makes a new token and
    the old one stops working; turning the feed off deletes the row.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="calendar_feed"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # When a calendar app last fetched it, to the hour, so people can tell
    # whether a subscription is still in use.
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Calendar feed for {self.user}"
