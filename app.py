#!/usr/bin/env python3
import json
import os
import threading
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional
from urllib.parse import urlparse

ENFORCER_TICK_SECONDS = 1.0


class ControllerState:
    def __init__(self):
        self.lock = threading.RLock()
        self.timer_off_at: Optional[datetime] = None
        self.last_action: Optional[str] = None
        self.last_action_at: Optional[datetime] = None

    def now(self) -> datetime:
        return datetime.now()

    def to_json(self) -> Dict[str, Any]:
        with self.lock:
            now = self.now()
            return {
                "now": now.isoformat(),
                "timer_off_at": self.timer_off_at.isoformat() if self.timer_off_at else None,
                "last_action": self.last_action,
                "last_action_at": self.last_action_at.isoformat() if self.last_action_at else None,
                "next_shutdown_at": self.timer_off_at.isoformat() if self.timer_off_at else None,
                "next_shutdown_reason": "timer" if self.timer_off_at else None,
            }


class PowerController:
    def __init__(self):
        self.cmd = os.getenv("POWER_OFF_CMD", "")
        if self.cmd:
            print(f"aegis startup: power_off_cmd configured cmd={self.cmd}", flush=True)
        else:
            print("aegis startup: power_off_cmd not set (no-op mode)", flush=True)

    def power_off(self) -> bool:
        if not self.cmd:
            print("aegis action: power_off no-op (POWER_OFF_CMD unset)", flush=True)
            return True
        rc = os.system(self.cmd)
        print(f"aegis action: power_off command_exit={rc}", flush=True)
        return rc == 0


class Enforcer:
    def __init__(self, state: ControllerState, power: PowerController):
        self.state = state
        self.power = power
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            now = self.state.now()
            with self.state.lock:
                self._handle_timer(now)
            self.stop_event.wait(ENFORCER_TICK_SECONDS)

    def _handle_timer(self, now: datetime) -> None:
        if not self.state.timer_off_at or now < self.state.timer_off_at:
            return
        if self._execute_power_off("timer_expired", now):
            self.state.timer_off_at = None

    def _execute_power_off(self, reason: str, now: datetime) -> bool:
        print(f"aegis action: power_off reason={reason}", flush=True)
        success = self.power.power_off()
        print(f"aegis action: power_off result={'ok' if success else 'failed'}", flush=True)
        if success:
            self.state.last_action = reason
            self.state.last_action_at = now
        return success


class AppHandler(BaseHTTPRequestHandler):
    state: ControllerState = None  # type: ignore
    static_base: str = ""

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        raw_len = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_len)
        except ValueError as exc:
            raise ValueError("invalid content-length") from exc
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _canonical_path(self) -> str:
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return path[4:]
        return path

    def _serve_static(self, rel_path: str) -> bool:
        base = self.static_base
        normalized = os.path.normpath("/" + rel_path).lstrip("/")
        full = os.path.realpath(os.path.join(base, normalized))
        if os.path.commonpath([base, full]) != base:
            return False
        if not os.path.isfile(full):
            return False

        ext = os.path.splitext(full)[1].lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def do_GET(self) -> None:
        path = self._canonical_path()
        if path == "/status":
            self._send_json(HTTPStatus.OK, self.state.to_json())
            return
        if path == "/" and self._serve_static("index.html"):
            return
        if path.startswith("/static/") and self._serve_static(path.removeprefix("/static/")):
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = self._canonical_path()
        print(f"aegis api: post path={path}", flush=True)
        try:
            payload = self._read_json()
        except (json.JSONDecodeError, ValueError):
            print(f"aegis api: invalid_json path={path}", flush=True)
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        handlers = {
            "/timer": lambda: self._post_timer(payload),
            "/timer/cancel": self._post_timer_cancel,
        }
        handler = handlers.get(path)
        if not handler:
            print(f"aegis api: not_found path={path}", flush=True)
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return

        try:
            handler()
            print(f"aegis api: ok path={path}", flush=True)
        except ValueError as exc:
            print(f"aegis api: validation_error path={path} error={exc}", flush=True)
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def _post_timer(self, payload: Dict[str, Any]) -> None:
        minutes = int(payload.get("minutes", 0))
        if minutes <= 0:
            raise ValueError("minutes must be > 0")

        with self.state.lock:
            self.state.timer_off_at = self.state.now() + timedelta(minutes=minutes)
        self._send_json(HTTPStatus.OK, {"ok": True, "timer_off_at": self.state.timer_off_at.isoformat()})

    def _post_timer_cancel(self) -> None:
        with self.state.lock:
            self.state.timer_off_at = None
        self._send_json(HTTPStatus.OK, {"ok": True, "timer_off_at": None})

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        return


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8787"))

    state = ControllerState()
    power = PowerController()
    enforcer = Enforcer(state, power)

    AppHandler.state = state
    AppHandler.static_base = os.path.realpath(os.path.join(os.getcwd(), "static"))
    server = ThreadingHTTPServer((host, port), AppHandler)

    enforcer.start()
    print(f"aegis listening on http://{host}:{port}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        enforcer.stop()
        server.server_close()


if __name__ == "__main__":
    main()
