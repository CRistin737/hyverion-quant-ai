"""The PAPER engine as a per-user launchd agent, so it runs without the app open.

The agent runs the same ``run --poll`` command the app's supervisor uses, so it
takes the same single-engine lock: the app then sees it as ``external`` and
never starts a second engine. It restarts only after a crash (``KeepAlive`` on
unsuccessful exit, throttled), starts at login, and logs to Application Support.
Nothing here touches LIVE or credentials; keys stay in the Keychain.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from trading_bot.config.loader import app_support_root
from trading_bot.engine_supervisor import DEFAULT_INTERVAL_SECONDS, engine_command
from trading_bot.providers.cli_path import ensure_cli_path

LABEL = "com.hyverion.engine"
RESTART_THROTTLE_SECONDS = 60
LAUNCHCTL_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    installed: bool
    loaded: bool
    pid: int | None
    plist_path: str
    log_path: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return app_support_root() / "logs" / "engine.log"


def build_plist(
    *,
    program: list[str],
    working_directory: Path,
    log_file: Path,
    environment: dict[str, str] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "Label": LABEL,
        "ProgramArguments": program,
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        # Restart after a crash, not after a clean stop (e.g. no broker keys).
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": RESTART_THROTTLE_SECONDS,
        # "Background" lets macOS throttle CPU and timers; a trading loop is not.
        "ProcessType": "Standard",
        "StandardOutPath": str(log_file),
        "StandardErrorPath": str(log_file),
        "EnvironmentVariables": {
            # Always the desktop app's config, keys and ledger.
            "HYVERION_SHARE_APP_STATE": "1",
            "PYTHONUNBUFFERED": "1",
            # launchd starts with a bare PATH; the AI CLIs (claude, codex, …)
            # live in the user's own directories.
            "PATH": service_path(),
            **(environment or {}),
        },
    }
    return plistlib.dumps(payload)


def service_path() -> str:
    """PATH for the agent: the system dirs plus where the AI CLIs are installed."""

    base = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    return ensure_cli_path(base)


def _working_directory() -> Path:
    if getattr(sys, "frozen", False):
        return app_support_root()
    # Source checkout: the repository root (config/ and src/ live there).
    return Path(__file__).resolve().parents[3]


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["/bin/launchctl", *args],
        capture_output=True,
        text=True,
        timeout=LAUNCHCTL_TIMEOUT_SECONDS,
        check=False,
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def status() -> ServiceStatus:
    path = plist_path()
    result = _launchctl("print", f"{_domain()}/{LABEL}") if path.exists() else None
    pid: int | None = None
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("pid = "):
                try:
                    pid = int(stripped.removeprefix("pid = "))
                except ValueError:
                    pid = None
    return ServiceStatus(
        installed=path.exists(),
        loaded=bool(result is not None and result.returncode == 0),
        pid=pid,
        plist_path=str(path),
        log_path=str(log_path()),
    )


def install(interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> ServiceStatus:
    """Write the agent and load it now. Idempotent (reloads an existing one)."""

    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        build_plist(
            program=engine_command(interval_seconds),
            working_directory=_working_directory(),
            log_file=log_file,
        )
    )
    os.chmod(path, 0o644)
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    result = _launchctl("bootstrap", _domain(), str(path))
    current = status()
    if result.returncode != 0 and not current.loaded:
        return ServiceStatus(**{**current.as_dict(), "detail": result.stderr.strip()[-300:]})
    return current


def uninstall() -> ServiceStatus:
    """Stop and remove the agent. Open positions keep their broker-side stops."""

    _launchctl("bootout", f"{_domain()}/{LABEL}")
    plist_path().unlink(missing_ok=True)
    return status()
