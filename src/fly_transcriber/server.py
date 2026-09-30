"""Local web UI for status, filing and settings.

Built on the standard library rather than a web framework: the whole surface is
a handful of JSON endpoints plus three static files, and the app is otherwise
dependency-light.

Bound to 127.0.0.1 only. There is no authentication, so anything already running
as this user can reach it -- the same trust boundary as the meeting transcripts
sitting in the home directory.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs

STATIC_DIR = Path(__file__).parent / "static"

#: Fixed so the menubar link and any bookmark stay valid across restarts.
DEFAULT_PORT = 8756

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Api:
    """What the UI is allowed to ask of the app.

    A plain object rather than a protocol class so the server can be exercised
    in tests with a stub.
    """

    def __init__(
        self,
        snapshot: Callable[[], dict],
        file_meeting: Callable[[str, str, dict], dict],
        save_settings: Callable[[dict], dict],
        forget: Callable[[str], dict],
        meeting_detail: Callable[[str], dict] | None = None,
        reveal: Callable[[str], dict] | None = None,
        dismiss: Callable[[str], dict] | None = None,
        record: Callable[[], dict] | None = None,
        show: Callable[[], dict] | None = None,
        delete: Callable[[str], dict] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.file_meeting = file_meeting
        self.save_settings = save_settings
        self.forget = forget
        self.meeting_detail = meeting_detail or _unsupported
        self.reveal = reveal or _unsupported
        self.dismiss = dismiss or _unsupported
        self.record = record or _unsupported
        self.show = show or _unsupported
        self.delete = delete or _unsupported


def _unsupported(*_args) -> dict:
    raise ValueError("not supported")


class _Handler(BaseHTTPRequestHandler):
    api: Api  # injected by make_server

    # -- plumbing ------------------------------------------------------------

    def log_message(self, *_args) -> None:  # noqa: D102 - silence stderr spam
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            # The browser navigated away mid-poll; not worth logging.
            pass

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path, _, query = self.path.partition("?")
        if path == "/api/state":
            self._json(self.api.snapshot())
            return
        if path == "/api/meeting":
            name = parse_qs(query).get("name", [""])[0]
            try:
                self._json(self.api.meeting_detail(name))
            except Exception as exc:
                self._json({"error": str(exc)}, 404)
            return
        self._serve_static("index.html" if path == "/" else path.lstrip("/"))

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        try:
            if path == "/api/file":
                result = self.api.file_meeting(
                    payload.get("meeting", ""),
                    payload.get("project", ""),
                    payload,
                )
            elif path == "/api/settings":
                result = self.api.save_settings(payload)
            elif path == "/api/forget":
                result = self.api.forget(payload.get("meeting", ""))
            elif path == "/api/dismiss":
                result = self.api.dismiss(payload.get("meeting", ""))
            elif path == "/api/record":
                result = self.api.record()
            elif path == "/api/delete":
                result = self.api.delete(payload.get("meeting", ""))
            elif path == "/api/show":
                result = self.api.show()
            elif path == "/api/reveal":
                result = self.api.reveal(payload.get("meeting", ""))
            else:
                self._json({"error": "not found"}, 404)
                return
        except Exception as exc:  # surface the reason instead of a blank 500
            self._json({"error": str(exc)}, 400)
            return
        self._json(result)

    def _serve_static(self, name: str) -> None:
        # Resolve inside STATIC_DIR only: the path comes from the URL.
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self._json({"error": "not found"}, 404)
            return
        content_type = _CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), content_type)


def make_server(api: Api, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    handler = type("Handler", (_Handler,), {"api": api})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve_in_background(api: Api, port: int = DEFAULT_PORT) -> tuple[ThreadingHTTPServer, str]:
    """Start the UI server on a daemon thread. Returns the server and its URL."""
    server = make_server(api, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


#: What /api/show answers, so a launcher knows it reached FLY and not some
#: other program that happens to hold the port.
APP_ID = "fly-transcriber"


def show_running_instance(port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """Ask an already-running FLY to come forward. True if one answered."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/show", data=b"{}", method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response).get("app") == APP_ID
    except (OSError, ValueError):  # refused, timed out, 404, not JSON...
        return False
