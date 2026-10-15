"""Container health check, run as `python -m sympraxi.healthcheck`.

Calls /healthz on the local web server with the site's own host name, so the
request passes ALLOWED_HOSTS. Exits 0 when healthy.
"""

import os
import sys
import urllib.request


def main() -> int:
    host = os.environ.get("SYMPRAXI_ALLOWED_HOSTS", "localhost").split(",")[0].strip().lstrip(".")
    request = urllib.request.Request(
        "http://127.0.0.1:8000/healthz", headers={"Host": host or "localhost"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 (fixed local URL)
            return 0 if response.status == 200 else 1
    except Exception:
        return 1


if __name__ == "__main__":
    sys.exit(main())
