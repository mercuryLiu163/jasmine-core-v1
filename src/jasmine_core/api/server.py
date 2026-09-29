"""The HTTP adapter.

Standard library only, loopback only (ADR 0003 §1.1). Each request runs on its
own thread and gets its own SQLite connection, because a `sqlite3.Connection`
is not safe to share across threads; `check_same_thread` stays on rather than
being disabled.
"""

from __future__ import annotations

import select
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .. import db, errors, ids
from ..canonical import looks_like_credential
from ..migrations import check_version, migrate
from .dispatch import JSON_CONTENT_TYPE, dispatch
from .handlers import Core
from .request import MAX_BODY_BYTES, Request, split_target

Log = Callable[[str], None]

#: A hostile Content-Length must not pin a worker thread forever.
DRAIN_TIMEOUT_SECONDS = 10.0
#: CPython's default BufferedReader buffer; bounds the shortfall a drain may accept.
READER_BUFFER_BYTES = 65536
#: Never spend longer than this reading a body that has already been refused.
DRAIN_MAX_BYTES = 8 * 1024 * 1024


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "jasmine-core"
    sys_version = ""  # do not advertise the interpreter build

    @property
    def _app(self) -> "Application":
        return self.server.app  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._handle("OPTIONS")

    def do_TRACE(self) -> None:  # noqa: N802
        self._handle("TRACE")

    def _handle(self, method: str) -> None:
        path, query = split_target(self.path)
        request_id = self.headers.get("X-Request-Id") or ""
        if not request_id or len(request_id) > 128 or looks_like_credential(request_id):
            # A caller-supplied correlation id is stored in the audit log, so one
            # that contains a credential would put a plaintext secret on disk.
            # The caller still gets a usable X-Request-Id, just a generated one.
            request_id = ids.new_id("aud")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            # Drain before answering, otherwise the client is still writing when
            # the response lands and sees a broken pipe instead of the documented
            # 413. The drain is best effort: on a hostile Content-Length the
            # caller still gets the 413, and only a caller that never finishes
            # sending may see the connection close.
            self._drain(min(length, DRAIN_MAX_BYTES))
            self.close_connection = True
            self._respond(413, errors.PayloadTooLarge(
                f"request body exceeds {MAX_BODY_BYTES} bytes").to_payload(request_id),
                {"X-Request-Id": request_id})
            return
        body = self.rfile.read(length) if length else b""
        headers = {key.lower(): value for key, value in self.headers.items()}
        request = Request(
            method=method, path=path, query=query, headers=headers, body=body,
            request_id=request_id,
        )
        response = self._app.handle(request)
        self._respond(response.status, response.body, response.headers)

    def _drain(self, length: int) -> bool:
        """Discard an over-long body, with a deadline and a bounded shortfall.

        Read from the socket, not from `rfile`: the buffered reader has usually
        already absorbed the first chunk, so a `select` on the socket reports
        "not ready" while the data sits in Python's buffer. Those buffered bytes
        are discarded with the connection, which this path closes, so the only
        thing that matters is that the *kernel* buffer empties. A shortfall no
        larger than the reader's own buffer is therefore expected, not a
        failure; anything larger means the client is still sending, and waiting
        for it is bounded by the deadline so a hostile Content-Length cannot pin
        a worker thread forever.
        """
        deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        remaining = length
        while remaining > 0:
            # A shortfall within the reader's buffer is already accounted for:
            # those bytes are in Python's buffer and are discarded with the
            # connection. Stopping here is what keeps the drain instant.
            if remaining <= READER_BUFFER_BYTES:
                return True
            budget = deadline - time.monotonic()
            if budget > 0 and select.select([self.connection], [], [], budget)[0]:
                try:
                    chunk = self.connection.recv(min(remaining, 65536))
                except OSError:
                    return False
                if not chunk:
                    return remaining <= READER_BUFFER_BYTES
                remaining -= len(chunk)
                continue
            return remaining <= READER_BUFFER_BYTES
        return True
        headers = {key.lower(): value for key, value in self.headers.items()}
        request = Request(
            method=method, path=path, query=query, headers=headers, body=body,
            request_id=headers.get("x-request-id", ""),
        )
        response = self._app.handle(request)
        self._respond(response.status, response.body, response.headers)

    def _respond(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        import json

        payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._status = str(status)
        self.send_response(status)
        self.send_header("Content-Type", JSON_CONTENT_TYPE)
        self.send_header("Content-Length", str(len(payload)))
        if self.close_connection:
            self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            if key.lower() != "content-type":
                self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        # The default handler writes the raw request line to stderr, which can
        # include a token in a header line. P0 logs method, status and path only.
        self._app.log(f"{self.command} {self.path.split('?')[0]} -> {self._last_status()}")

    def _last_status(self) -> str:
        return getattr(self, "_status", "-")


class Application:
    """Owns the database path and hands each request its own connection."""

    def __init__(self, db_path: str | Path, *, log: Log | None = None) -> None:
        self.db_path = Path(db_path)
        self.log = log or (lambda message: print(f"jasmine-core: {message}", file=sys.stderr))
        self._local = threading.local()

    def _connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = db.connect(self.db_path)
            check_version(conn)
            self._local.conn = conn
        return conn

    def _core(self) -> Core:
        return Core(self._connection())

    def handle(self, request: Request) -> Any:
        from .request import Response

        return dispatch(self._core(), request)

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def serve(db_path: str | Path, *, host: str = "127.0.0.1", port: int = 8787,
          log: Log | None = None) -> None:
    """Run the API until interrupted. Blocks the calling thread."""
    if host not in ("127.0.0.1", "localhost", "::1"):
        # P0 has no transport security, so it must not be reachable off-host.
        raise SystemExit(
            f"refusing to bind {host}: P0 has no TLS or network access control; "
            "use 127.0.0.1 and reach the API through an SSH tunnel"
        )
    conn = db.connect(db_path)
    try:
        migrate(conn, log=log)
        version = check_version(conn)
        conn.close()
    except errors.CoreError as exc:
        log(f"refusing to start: {exc.code}: {exc.message}")
        raise SystemExit(3) from exc
    except Exception:
        conn.close()
        raise
    log(f"jasmine-core API on http://{host}:{port} (schema v{version}, db {db_path})")
    server = ThreadingHTTPServer((host, port), _Handler)
    server.app = Application(db_path, log=log)  # type: ignore[attr-defined]
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
    finally:
        server.server_close()
