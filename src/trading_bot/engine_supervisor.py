"""Supervise the PAPER engine as a child process of the control API.

The desktop app only launches ``hyverion-core api``. This module lets the
operator start and stop the PAPER loop (``run``) from the UI without giving the
API any trading authority: the child is the ordinary CLI engine with the same
DATA -> AGENTS -> PROPOSAL -> CRITIC -> RISK -> EXECUTION chain, forced to PAPER.

Invariants:
- A single engine per data directory, enforced by an exclusive ``flock`` that
  ``run`` takes at startup (an engine started from a terminal is detected as
  ``external`` and is never started twice).
- The child stops itself when this API process disappears (stdin pipe EOF plus
  PID watchdog), so no orphaned engine keeps trading on paper after a crash.
- LIVE is never started from here.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal

from trading_bot.config.models import Settings
from trading_bot.security.credentials import alpaca_paper_credentials

EngineState = Literal["stopped", "running", "external", "exited"]

MIN_INTERVAL_SECONDS = 5
DEFAULT_INTERVAL_SECONDS = 60
STOP_GRACE_SECONDS = 8.0
# Environment the engine must not inherit: it never serves the control API.
_STRIPPED_ENV = ("CONTROL_API_TOKEN", "HYVERION_SHELL_PID", "HYVERION_SHELL_STDIN_WATCH")


class EngineStartError(RuntimeError):
    """Raised with a stable machine code when the engine cannot start."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def engine_data_dir(settings: Settings) -> Path:
    """Directory that owns the engine lock: next to the SQLite database."""

    url = settings.public.database.url
    marker = ":///"
    if url.startswith("sqlite") and marker in url:
        return Path(url.split(marker, 1)[1]).expanduser().parent
    return Path("data")


def engine_lock_path(settings: Settings) -> Path:
    return engine_data_dir(settings) / "engine.lock"


@contextmanager
def hold_engine_lock(settings: Settings) -> Iterator[None]:
    """Hold the single-engine lock for the lifetime of a ``run`` process.

    Raises :class:`EngineStartError` (``engine_already_running``) when another
    engine owns the data directory.
    """

    path = engine_lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EngineStartError(
                "engine_already_running", "another PAPER engine is already running"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        handle.close()  # closing the descriptor releases the flock


def flatten_request_path(settings: Settings) -> Path:
    return engine_data_dir(settings) / "flatten.request"


def request_flatten(settings: Settings, *, now: datetime | None = None) -> None:
    """Operator asks the engine to close every open position (no new entries).

    The UI/API never places orders: this only leaves a flag that the engine's
    deterministic cycle honors through the normal exit path (RiskEngine ->
    ExecutionEngine).
    """

    path = flatten_request_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text((now or datetime.now(UTC)).isoformat(), encoding="utf-8")
    temporary.replace(path)


def flatten_pending(settings: Settings) -> bool:
    return flatten_request_path(settings).exists()


def clear_flatten(settings: Settings) -> None:
    flatten_request_path(settings).unlink(missing_ok=True)


def engine_lock_held(settings: Settings) -> bool:
    path = engine_lock_path(settings)
    if not path.exists():
        return False
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def engine_command(interval_seconds: int) -> list[str]:
    """Command line for the PAPER loop in source and frozen (sidecar) layouts."""

    args = ["run", "--interval-seconds", str(interval_seconds), "--poll"]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]
    return [sys.executable, "-m", "trading_bot", *args]


@dataclass(frozen=True)
class EngineStatus:
    state: EngineState
    pid: int | None
    started_at: datetime | None
    stopped_at: datetime | None
    exit_code: int | None
    interval_seconds: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "pid": self.pid,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
            "exit_code": self.exit_code,
            "interval_seconds": self.interval_seconds,
            "mode": "PAPER",
        }


class EngineSupervisor:
    """Start/stop one PAPER engine child process. Thread-safe."""

    def __init__(
        self,
        settings: Settings,
        *,
        command_factory: Callable[[int], list[str]] = engine_command,
        log_path: Path | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        settings_loader: Callable[[], Settings] | None = None,
    ) -> None:
        self._settings = settings
        # Onboarding may connect the broker after the API started; re-read at start.
        self._settings_loader = settings_loader or (lambda: settings)
        self._command_factory = command_factory
        self._log_path = log_path or engine_data_dir(settings) / "engine.log"
        self._clock = clock
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle: IO[bytes] | None = None
        self._started_at: datetime | None = None
        self._stopped_at: datetime | None = None
        self._exit_code: int | None = None
        self._interval: int | None = None

    def status(self) -> EngineStatus:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> EngineStatus:
        process = self._process
        if process is not None:
            code = process.poll()
            if code is None:
                return EngineStatus(
                    "running", process.pid, self._started_at, None, None, self._interval
                )
            self._reap_locked(code)
        if engine_lock_held(self._settings):
            return EngineStatus("external", None, None, None, None, None)
        crashed = self._exit_code not in (None, 0, -signal.SIGTERM, -signal.SIGKILL)
        state: EngineState = "exited" if crashed else "stopped"
        return EngineStatus(
            state, None, self._started_at, self._stopped_at, self._exit_code, self._interval
        )

    def _reap_locked(self, code: int) -> None:
        self._exit_code = code
        self._stopped_at = self._clock()
        self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def start(self, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> EngineStatus:
        if interval_seconds < MIN_INTERVAL_SECONDS:
            raise EngineStartError("invalid_interval", "interval must be at least 5 seconds")
        current_settings = self._settings_loader()
        trading = current_settings.public.trading
        if trading.mode != "paper" or trading.live_trading:
            raise EngineStartError("live_not_allowed", "the app only starts the PAPER engine")
        if current_settings.public.broker.provider != "simulator" and (
            alpaca_paper_credentials(current_settings.secrets) is None
        ):
            # No broker, no equity, no engine: sizing never runs on a made-up number.
            raise EngineStartError(
                "broker_not_connected", "connect the Alpaca PAPER account before starting"
            )
        with self._lock:
            current = self._status_locked()
            if current.state == "running":
                return current
            if current.state == "external":
                raise EngineStartError(
                    "engine_already_running", "another PAPER engine is already running"
                )
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = self._log_path.open("ab")
            env = {key: value for key, value in os.environ.items() if key not in _STRIPPED_ENV}
            env["HYVERION_SHELL_PID"] = str(os.getpid())
            env["HYVERION_SHELL_STDIN_WATCH"] = "1"
            env["TRADING_MODE"] = "paper"
            try:
                process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
                    self._command_factory(interval_seconds),
                    stdin=subprocess.PIPE,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    env=env,
                    close_fds=True,
                    # Own process group: a hard stop can take the AI CLI
                    # children down with the engine instead of orphaning them.
                    start_new_session=True,
                )
            except OSError as exc:
                log_handle.close()
                raise EngineStartError("spawn_failed", type(exc).__name__) from exc
            self._process = process
            self._log_handle = log_handle
            self._started_at = self._clock()
            self._stopped_at = None
            self._exit_code = None
            self._interval = interval_seconds
            return self._status_locked()

    def stop(self, grace_seconds: float = STOP_GRACE_SECONDS) -> EngineStatus:
        with self._lock:
            process = self._process
            if process is None:
                return self._status_locked()
            if process.poll() is None:
                # SIGTERM first: the engine cancels its work, kills its AI CLI
                # calls and closes its agent runs.
                process.terminate()
                try:
                    process.wait(timeout=grace_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=grace_seconds)
            # Whatever is left in the engine's group (a CLI that ignored the
            # cancel) goes with it.
            _kill_group(process.pid)
            if process.stdin is not None:
                process.stdin.close()
            self._reap_locked(process.returncode)
            return self._status_locked()


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return

