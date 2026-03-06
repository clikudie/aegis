#!/usr/bin/env python3
import json
import os
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

ENFORCER_TICK_SECONDS = 1.0
TV_PROBE_TIMEOUT_SECONDS = 1.5
TV_PROBE_PORTS = (3001, 3000)


def _parse_hhmm(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time must be HH:MM")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time must be HH:MM")
    return hour * 60 + minute


def _day_to_int(name: str) -> int:
    mapping = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    day = name.strip().lower()
    if day not in mapping:
        raise ValueError("day must be one of monday..sunday")
    return mapping[day]


def _int_to_day(day: int) -> str:
    reverse = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    return reverse[day]


@dataclass
class Window:
    day: int
    start_minute: int
    end_minute: int

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "Window":
        day = _day_to_int(str(payload["day"]))
        start = _parse_hhmm(str(payload["start"]))
        end = _parse_hhmm(str(payload["end"]))
        if start == end:
            raise ValueError("start and end cannot be the same")
        return cls(day=day, start_minute=start, end_minute=end)

    def contains(self, dt: datetime) -> bool:
        current_day = dt.weekday()
        current_min = dt.hour * 60 + dt.minute

        if self.start_minute < self.end_minute:
            return current_day == self.day and self.start_minute <= current_min < self.end_minute

        # Overnight window: e.g. 22:00 -> 02:00
        if current_day == self.day and current_min >= self.start_minute:
            return True
        prev_day = (current_day - 1) % 7
        return prev_day == self.day and current_min < self.end_minute

    def to_json(self) -> Dict[str, Any]:
        return {
            "day": _int_to_day(self.day),
            "start": f"{self.start_minute // 60:02d}:{self.start_minute % 60:02d}",
            "end": f"{self.end_minute // 60:02d}:{self.end_minute % 60:02d}",
        }


@dataclass
class ScheduleConfig:
    enabled: bool = True
    windows: List[Window] = field(default_factory=list)
    mode: str = "strict"  # strict or graceful
    grace_minutes: int = 0

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ScheduleConfig":
        enabled = bool(payload.get("enabled", True))
        mode = str(payload.get("mode", "strict")).lower()
        if mode not in {"strict", "graceful"}:
            raise ValueError("mode must be strict or graceful")
        grace_minutes = int(payload.get("grace_minutes", 0))
        if grace_minutes < 0:
            raise ValueError("grace_minutes must be >= 0")

        windows_payload = payload.get("windows", [])
        if not isinstance(windows_payload, list):
            raise ValueError("windows must be an array")
        windows = [Window.from_payload(item) for item in windows_payload]

        return cls(enabled=enabled, windows=windows, mode=mode, grace_minutes=grace_minutes)

    def to_json(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "grace_minutes": self.grace_minutes,
            "windows": [w.to_json() for w in self.windows],
        }


@dataclass
class OverrideState:
    mode: str = "none"  # none, temporary, permanent
    expires_at: Optional[datetime] = None

    def is_active(self, now: datetime) -> bool:
        if self.mode == "permanent":
            return True
        if self.mode == "temporary":
            return bool(self.expires_at and now < self.expires_at)
        return False

    def normalize(self, now: datetime) -> None:
        if self.mode == "temporary" and not self.is_active(now):
            self.mode = "none"
            self.expires_at = None

    def to_json(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class ControllerState:
    def __init__(self, tz_name: str):
        self.lock = threading.RLock()
        self.tz_name = tz_name
        self.tz = ZoneInfo(tz_name) if ZoneInfo else None
        self.schedule = ScheduleConfig()
        self.override = OverrideState()
        self.timer_off_at: Optional[datetime] = None
        self.tv_is_on = False
        self.last_action: Optional[str] = None
        self.last_action_at: Optional[datetime] = None
        self.outside_since: Optional[datetime] = None

    def now(self) -> datetime:
        if self.tz:
            return datetime.now(self.tz)
        return datetime.now()

    def to_json(self) -> Dict[str, Any]:
        with self.lock:
            now = self.now()
            self.override.normalize(now)
            override_active = self.override.is_active(now)
            next_shutdown_at, next_shutdown_reason = self._next_shutdown(now)
            return {
                "now": now.isoformat(),
                "timezone": self.tz_name,
                "tv_is_on": self.tv_is_on,
                "timer_off_at": self.timer_off_at.isoformat() if self.timer_off_at else None,
                "schedule": self.schedule.to_json(),
                "override": self.override.to_json(),
                "override_active": override_active,
                "last_action": self.last_action,
                "last_action_at": self.last_action_at.isoformat() if self.last_action_at else None,
                "inside_allowed_window": self._inside_allowed_window(now),
                "next_shutdown_at": next_shutdown_at.isoformat() if next_shutdown_at else None,
                "next_shutdown_reason": next_shutdown_reason,
            }

    def load_persistent(self, payload: Dict[str, Any]) -> None:
        with self.lock:
            if "schedule" in payload and isinstance(payload["schedule"], dict):
                self.schedule = ScheduleConfig.from_payload(payload["schedule"])
            if "override" in payload and isinstance(payload["override"], dict):
                raw = payload["override"]
                mode = str(raw.get("mode", "none")).lower()
                if mode not in {"none", "temporary", "permanent"}:
                    mode = "none"
                expires_at = None
                raw_expires = raw.get("expires_at")
                if mode == "temporary" and isinstance(raw_expires, str):
                    try:
                        expires_at = datetime.fromisoformat(raw_expires)
                    except ValueError:
                        expires_at = None
                        mode = "none"
                self.override = OverrideState(mode=mode, expires_at=expires_at)

    def to_persistent_json(self) -> Dict[str, Any]:
        with self.lock:
            return {
                "schedule": self.schedule.to_json(),
                "override": self.override.to_json(),
            }

    def _inside_allowed_window(self, now: datetime) -> bool:
        if not self.schedule.enabled:
            return True
        if not self.schedule.windows:
            return True
        return any(w.contains(now) for w in self.schedule.windows)

    def _next_shutdown(self, now: datetime) -> tuple[Optional[datetime], Optional[str]]:
        # Timer should be visible as upcoming shutdown even before TV state flips to "on".
        timer_candidate: Optional[tuple[datetime, str]] = None
        if self.timer_off_at:
            timer_candidate = (self.timer_off_at, "timer")

        if not self.tv_is_on:
            return timer_candidate if timer_candidate else (None, None)

        if self.override.is_active(now):
            return timer_candidate if timer_candidate else (None, None)

        candidates: list[tuple[datetime, str]] = []
        if timer_candidate:
            candidates.append(timer_candidate)

        if not self._inside_allowed_window(now):
            if self.schedule.mode == "strict":
                candidates.append((now, "schedule_strict"))
            else:
                base = self.outside_since if self.outside_since else now
                at = base + timedelta(minutes=self.schedule.grace_minutes)
                candidates.append((at, "schedule_graceful"))

        if not candidates:
            return None, None
        at, reason = min(candidates, key=lambda c: c[0])
        return at, reason


class PowerController:
    def __init__(self):
        self.cmd = os.getenv("POWER_OFF_CMD", "")
 
    def power_off(self) -> bool:
        if not self.cmd:
            # No device integration configured yet; treated as success for MVP testing.
            return True
        return os.system(self.cmd) == 0


class PersistentStore:
    def __init__(self, path: str):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not self.path:
            return {}
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self, payload: Dict[str, Any]) -> None:
        if not self.path:
            return
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, self.path)


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
                self.state.override.normalize(now)
                override_active = self.state.override.is_active(now)
                self._handle_timer(now)
                self._handle_schedule(now, override_active)
            self.stop_event.wait(ENFORCER_TICK_SECONDS)

    def _handle_timer(self, now: datetime) -> None:
        if not self.state.timer_off_at or now < self.state.timer_off_at:
            return
        if self._execute_power_off("timer_expired", now):
            self.state.timer_off_at = None

    def _handle_schedule(self, now: datetime, override_active: bool) -> None:
        enforce = self.state.tv_is_on and (not override_active) and (not self.state._inside_allowed_window(now))
        if not enforce:
            self.state.outside_since = None
            return

        if self.state.schedule.mode == "strict":
            self._execute_power_off("outside_schedule", now)
            return

        if self.state.outside_since is None:
            self.state.outside_since = now
        elapsed = (now - self.state.outside_since).total_seconds() / 60.0
        if elapsed >= self.state.schedule.grace_minutes:
            self._execute_power_off("outside_schedule_grace_expired", now)

    def _execute_power_off(self, reason: str, now: datetime) -> bool:
        success = self.power.power_off()
        if success:
            self.state.tv_is_on = False
            self.state.last_action = reason
            self.state.last_action_at = now
        return success


class TVStateMonitor:
    def __init__(self, state: ControllerState, host: str, interval_seconds: float = 5.0):
        self.state = state
        self.host = host
        self.interval_seconds = max(1.0, interval_seconds)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _probe_tv_on(self) -> bool:
        # webOS TVs typically expose ws ports 3000/3001 when powered on.
        for port in TV_PROBE_PORTS:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TV_PROBE_TIMEOUT_SECONDS)
            try:
                if sock.connect_ex((self.host, port)) == 0:
                    return True
            finally:
                sock.close()
        return False

    def _run(self) -> None:
        while not self.stop_event.is_set():
            is_on = self._probe_tv_on()
            with self.state.lock:
                self.state.tv_is_on = is_on
            self.stop_event.wait(self.interval_seconds)


class AppHandler(BaseHTTPRequestHandler):
    state: ControllerState = None  # type: ignore
    store: PersistentStore = None  # type: ignore
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
        if path == "/diagnostics" and self._serve_static("diagnostics.html"):
            return
        if path.startswith("/static/") and self._serve_static(path.removeprefix("/static/")):
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = self._canonical_path()
        try:
            payload = self._read_json()
        except (json.JSONDecodeError, ValueError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return

        handlers = {
            "/timer": lambda: self._post_timer(payload),
            "/timer/cancel": self._post_timer_cancel,
            "/schedule": lambda: self._post_schedule(payload),
            "/override": lambda: self._post_override(payload),
            "/tv-state": lambda: self._post_tv_state(payload),
        }
        handler = handlers.get(path)
        if not handler:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            handler()
        except ValueError as exc:
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

    def _post_schedule(self, payload: Dict[str, Any]) -> None:
        schedule = ScheduleConfig.from_payload(payload)
        with self.state.lock:
            self.state.schedule = schedule
            self.state.outside_since = None
        self.store.save(self.state.to_persistent_json())
        self._send_json(HTTPStatus.OK, {"ok": True, "schedule": schedule.to_json()})

    def _post_override(self, payload: Dict[str, Any]) -> None:
        mode = str(payload.get("mode", "none")).lower()
        if mode not in {"none", "temporary", "permanent"}:
            raise ValueError("mode must be none, temporary, or permanent")

        with self.state.lock:
            if mode == "none":
                self.state.override = OverrideState(mode="none", expires_at=None)
            elif mode == "permanent":
                self.state.override = OverrideState(mode="permanent", expires_at=None)
                self.state.outside_since = None
            else:
                minutes = int(payload.get("minutes", 0))
                if minutes <= 0:
                    raise ValueError("minutes must be > 0 for temporary override")
                expires = self.state.now() + timedelta(minutes=minutes)
                self.state.override = OverrideState(mode="temporary", expires_at=expires)
                self.state.outside_since = None

        self.store.save(self.state.to_persistent_json())
        self._send_json(HTTPStatus.OK, {"ok": True, "override": self.state.override.to_json()})

    def _post_tv_state(self, payload: Dict[str, Any]) -> None:
        is_on = bool(payload.get("is_on", False))
        with self.state.lock:
            self.state.tv_is_on = is_on
        self._send_json(HTTPStatus.OK, {"ok": True, "tv_is_on": is_on})

    def log_message(self, format: str, *args: Any) -> None:  # pragma: no cover
        return


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8787"))
    timezone = os.getenv("TZ_NAME", "America/Los_Angeles")
    state_file = os.getenv("STATE_FILE", "state.json")
    tv_host = os.getenv("LG_TV_HOST", "").strip()
    try:
        tv_poll_seconds = float(os.getenv("TV_STATE_POLL_SECONDS", "5"))
    except ValueError:
        tv_poll_seconds = 5.0

    state = ControllerState(timezone)
    power = PowerController()
    store = PersistentStore(state_file)
    try:
        state.load_persistent(store.load())
    except Exception:
        pass
    enforcer = Enforcer(state, power)
    monitor = TVStateMonitor(state, tv_host, tv_poll_seconds) if tv_host else None

    AppHandler.state = state
    AppHandler.store = store
    AppHandler.static_base = os.path.realpath(os.path.join(os.getcwd(), "static"))
    server = ThreadingHTTPServer((host, port), AppHandler)

    enforcer.start()
    if monitor:
        monitor.start()
    print(
        f"aegis listening on http://{host}:{port} "
        f"(timezone={timezone}, state_file={state_file}, tv_host={tv_host or 'disabled'})"
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if monitor:
            monitor.stop()
        enforcer.stop()
        server.server_close()


if __name__ == "__main__":
    main()
