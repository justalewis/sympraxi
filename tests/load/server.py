"""Run Sympraxi locally the way production runs it, on Windows too.

Production runs `gunicorn --workers 3` behind Caddy, with the task worker and
the scheduler beside it. Gunicorn's sync workers are separate processes that
take turns accepting connections on one listening socket, handle one request
at a time each, and close the connection after every response. Gunicorn
doesn't run on Windows, so `start_web()` does the same with Django's own WSGI
request handler: this process opens the socket and each worker process
accepts from it in a loop. Requests that arrive while every worker is busy
wait in the socket's backlog, as they would in front of Gunicorn.
"""

from __future__ import annotations

import multiprocessing
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import IO, Any

REPO = Path(__file__).resolve().parents[2]


@dataclass
class Servers:
    url: str
    listener: socket.socket | None = None
    web: list[BaseProcess] = field(default_factory=list)
    background: list[subprocess.Popen[bytes]] = field(default_factory=list)
    logs: list[IO[Any]] = field(default_factory=list)

    def stop(self) -> None:
        for proc in self.background:
            proc.terminate()
        for worker in self.web:
            worker.terminate()
        for proc in self.background:
            proc.wait(timeout=30)
        for worker in self.web:
            worker.join(timeout=30)
        if self.listener is not None:
            self.listener.close()
        for log in self.logs:
            log.close()


def start(*, port: int, workers: int, log_dir: Path, scheduler: bool = True) -> Servers:
    """Start the web workers, the task worker and (optionally) the scheduler."""
    listener = socket.create_server(("127.0.0.1", port), backlog=1024)
    port = listener.getsockname()[1]
    servers = Servers(url=f"http://127.0.0.1:{port}", listener=listener)
    # Links in emails sent outside a request, such as reminders, point here.
    os.environ["SYMPRAXI_BASE_URL"] = servers.url
    spawn = multiprocessing.get_context("spawn")
    for number in range(workers):
        worker = spawn.Process(
            target=_web_worker, args=(listener, number, str(log_dir)), daemon=True
        )
        worker.start()
        servers.web.append(worker)
    commands = [["db_worker", "--interval", "0.5"]]
    if scheduler:
        commands.append(["scheduler"])
    for command in commands:
        log = open(log_dir / f"{command[0]}.log", "ab")  # noqa: SIM115 (closed in stop())
        servers.logs.append(log)
        servers.background.append(
            subprocess.Popen(  # noqa: S603 (our own interpreter and manage.py)
                [sys.executable, "manage.py", *command], cwd=REPO, stdout=log, stderr=log
            )
        )
    _wait_until_up(servers.url)
    return servers


def _wait_until_up(url: str, seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while True:
        try:
            with urllib.request.urlopen(f"{url}/healthz", timeout=5) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"The web workers didn't answer at {url}/healthz.")
        time.sleep(0.5)


def _web_worker(listener: socket.socket, number: int, log_dir: str) -> None:
    """One sync worker: accept a connection, answer one request, close, repeat."""
    log = open(Path(log_dir) / f"web-{number}.log", "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    sys.stdout = sys.stderr = log

    import django

    django.setup()
    from django.core.servers.basehttp import WSGIRequestHandler, WSGIServer
    from django.core.wsgi import get_wsgi_application

    host, port = listener.getsockname()[:2]
    server = WSGIServer((host, port), WSGIRequestHandler, bind_and_activate=False)
    server.socket.close()
    server.socket = listener
    server.server_name, server.server_port = host, port
    server.setup_environ()
    server.set_app(get_wsgi_application())
    while True:
        conn, address = listener.accept()
        try:
            # Not a threading server, so Django's handler answers with
            # "Connection: close", like a Gunicorn sync worker.
            server.finish_request(conn, address)
        except Exception:
            server.handle_error(conn, address)
        finally:
            server.shutdown_request(conn)
