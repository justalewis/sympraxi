"""Gunicorn's access log with survey link tokens hidden (see sympraxi.core.logs).

The Docker image runs Gunicorn with `--logger-class sympraxi.core.access_log.AccessLog`.
Gunicorn imports this before Django starts, so it imports nothing from Django.
"""

from typing import Any

from gunicorn.glogging import Logger

from sympraxi.core.logs import scrub


class AccessLog(Logger):
    def atoms(self, resp: Any, req: Any, environ: Any, request_time: Any) -> dict[str, Any]:
        atoms = super().atoms(resp, req, environ, request_time)
        return {
            key: scrub(value) if isinstance(value, str) else value for key, value in atoms.items()
        }
