"""Keep secret link tokens out of the logs: survey links and calendar feeds.

A survey link's token leads to the invitation, and so to the student. With the
token and the time a link was used, someone reading the logs and the database
could tie an anonymous response to its student. A calendar feed's token is a
standing key to someone's appointments. So Gunicorn's access log
(sympraxi.core.access_log) and Django's own log both write `/survey/[link]/`
and `/calendar/[link].ics` in place of the tokens, in the request line and in
the Referer header alike.

This module imports nothing from Django: Gunicorn loads it before the app.
"""

import logging
import re

# A path segment after /survey/, up to the next slash, query, quote or space.
_SURVEY_LINK = re.compile(r"(/survey/)([^/?#\s\"']+)")
# Pages under /survey/ that aren't links.
_NOT_LINKS = frozenset({"thanks"})
# A calendar feed's token, before its ".ics".
_FEED_LINK = re.compile(r"(/calendar/)([^/?#\s\"'.]+)")


def _hide(match: re.Match[str]) -> str:
    if match.group(2) in _NOT_LINKS:
        return match.group(0)
    return match.group(1) + "[link]"


def scrub(text: str) -> str:
    """The text with every survey link's and calendar feed's token replaced by [link]."""
    return _FEED_LINK.sub(r"\1[link]", _SURVEY_LINK.sub(_hide, text))


class ScrubLinks(logging.Filter):
    """A handler filter that scrubs each record's message (see settings.LOGGING)."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        scrubbed = scrub(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        return True
