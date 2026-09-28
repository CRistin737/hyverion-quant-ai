"""Run a provider's official subscription login (browser OAuth) and report progress.

The operator clicks "Iniciar sesión" in the app; this module launches the exact
official CLI login command (fixed argv, no user input), extracts the sign-in link
it prints, opens it in the default browser, waits for the CLI to finish and then
verifies the connection with the same status check the router uses.

It never reads cookies, tokens or credential files: authentication stays inside
the provider-owned CLI. The sign-in URL is returned to the local authenticated UI
only and is never persisted or logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

from trading_bot.providers.access import (
    SubscriptionHealthResult,
    check_subscription,
    subscription_login_action,
)
from trading_bot.providers.subscription_cli import sanitized_environment

LoginState = Literal[
    "idle", "starting", "waiting_browser", "verifying", "connected", "failed", "manual"
]

LOGIN_TIMEOUT_SECONDS = 300.0
_URL = re.compile(r"https://[^\s\"'<>]+")
# Only official identity hosts may be opened automatically.
ALLOWED_LOGIN_HOSTS = (
    "claude.ai",
    "anthropic.com",
    "openai.com",
    "chatgpt.com",
    "x.ai",
    "grok.com",
    "accounts.google.com",
    "google.com",
)
# CLIs whose login needs an interactive terminal (TUI); they cannot run headless.
MANUAL_ONLY = {"gemini": "gemini"}
# Official sign-out commands, used by "Cambiar de cuenta". Grok and Gemini have
# no documented non-interactive sign-out; for them only a new login is started.
LOGOUT_COMMANDS: dict[str, tuple[str, ...]] = {
    "anthropic": ("claude", "auth", "logout"),
    "openai": ("codex", "logout"),
}
LOGOUT_TIMEOUT_SECONDS = 30.0


async def sign_out(provider_id: str) -> bool:
    """Run the provider's own sign-out command. True when it succeeded."""

    command = LOGOUT_COMMANDS.get(provider_id)
    if command is None or shutil.which(command[0]) is None:
        return False
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=sanitized_environment(),
        )
    except OSError:
        return False
    try:
        await asyncio.wait_for(process.wait(), LOGOUT_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return False
    return process.returncode == 0

Opener = Callable[[str], Awaitable[None]]
StatusCheck = Callable[[str], Awaitable[SubscriptionHealthResult]]


def is_allowed_login_url(url: str) -> bool:
    # Browsers treat "\\" as "/" and honour "user@host" differently from urlparse,
    # so "https://evil.com\\@claude.ai" would open evil.com. Reject anything a
    # browser could parse differently: backslashes, userinfo, non-ASCII, controls.
    if not url.isascii() or "\\" in url or any(ord(char) < 33 for char in url):
        return False
    parsed = urlparse(url)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_LOGIN_HOSTS
    )


async def open_in_browser(url: str) -> None:
    """Open a vetted URL with the OS default browser (macOS ``open``)."""

    if not is_allowed_login_url(url):
        raise ValueError("refusing to open a non-official login URL")
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    if shutil.which(opener) is None:
        return
    process = await asyncio.create_subprocess_exec(
        opener,
        url,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await process.wait()


@dataclass(slots=True)
class LoginSession:
    provider_id: str
    state: LoginState = "starting"
    url: str | None = None
    detail: str = ""
    manual_command: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "state": self.state,
            "url": self.url,
            "detail": self.detail,
            "manual_command": self.manual_command,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "timeout_seconds": int(LOGIN_TIMEOUT_SECONDS),
        }


class LoginFlowManager:
    """At most one login per provider; supervised, bounded and cancellable."""

    def __init__(
        self,
        *,
        opener: Opener = open_in_browser,
        status_check: StatusCheck = check_subscription,
        timeout_seconds: float = LOGIN_TIMEOUT_SECONDS,
        environment: dict[str, str] | None = None,
    ) -> None:
        self._opener = opener
        self._status_check = status_check
        self._timeout = timeout_seconds
        self._environment = environment
        self._sessions: dict[str, LoginSession] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def status(self, provider_id: str) -> dict[str, Any]:
        session = self._sessions.get(provider_id)
        if session is None:
            return LoginSession(provider_id, state="idle").as_dict()
        return session.as_dict()

    async def start(self, provider_id: str) -> dict[str, Any]:
        running = self._tasks.get(provider_id)
        if running is not None and not running.done():
            return self.status(provider_id)
        action = subscription_login_action(provider_id)
        if not action.supported:
            session = LoginSession(provider_id, state="failed", detail="cli_unavailable")
            session.finished_at = datetime.now(UTC)
            self._sessions[provider_id] = session
            return session.as_dict()
        if provider_id in MANUAL_ONLY:
            session = LoginSession(
                provider_id,
                state="manual",
                detail="interactive_terminal_required",
                manual_command=MANUAL_ONLY[provider_id],
            )
            self._sessions[provider_id] = session
            return session.as_dict()
        session = LoginSession(provider_id)
        self._sessions[provider_id] = session
        self._tasks[provider_id] = asyncio.create_task(self._run(session, action.command))
        return session.as_dict()

    async def cancel(self, provider_id: str) -> dict[str, Any]:
        # Mark first: the supervising task must see "cancelled", not the kill's exit code.
        session = self._sessions.get(provider_id)
        if session is not None and session.state in {"starting", "waiting_browser", "verifying"}:
            session.state = "failed"
            session.detail = "cancelled"
            session.finished_at = datetime.now(UTC)
        await self._terminate(provider_id)
        return self.status(provider_id)

    async def shutdown(self) -> None:
        for provider_id in list(self._processes):
            await self._terminate(provider_id)
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _terminate(self, provider_id: str) -> None:
        process = self._processes.pop(provider_id, None)
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await process.wait()

    async def _run(self, session: LoginSession, command: tuple[str, ...]) -> None:
        provider_id = session.provider_id
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._login_environment(),
            )
        except OSError:
            self._finish(session, "failed", "cli_launch_failed")
            return
        self._processes[provider_id] = process
        try:
            await asyncio.wait_for(self._pump(session, process), self._timeout)
            await process.wait()
        except TimeoutError:
            await self._terminate(provider_id)
            self._finish(session, "failed", "login_timeout")
            return
        finally:
            self._processes.pop(provider_id, None)
        if session.state == "failed":
            return
        if process.returncode not in (0, None):
            self._finish(session, "failed", "login_command_failed")
            return
        session.state = "verifying"
        result = await self._status_check(provider_id)
        if result.status == "CONNECTED":
            self._finish(session, "connected", "")
        else:
            self._finish(session, "failed", result.code or "not_connected")

    async def _pump(self, session: LoginSession, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        session.state = "waiting_browser"
        async for raw in process.stdout:
            if session.url is not None:
                continue
            match = _URL.search(raw.decode(errors="replace"))
            if match is None:
                continue
            url = match.group(0).rstrip(".,)")
            if not is_allowed_login_url(url):
                continue
            session.url = url
            # The CLI usually opens the browser itself; opening again is harmless
            # and covers CLIs that only print the link.
            with contextlib.suppress(Exception):
                await self._opener(url)

    @staticmethod
    def _finish(session: LoginSession, state: LoginState, detail: str) -> None:
        session.state = state
        session.detail = detail
        session.finished_at = datetime.now(UTC)
        if state == "connected":
            session.url = None  # single-use OAuth link; do not keep it around

    def _login_environment(self) -> dict[str, str]:
        if self._environment is not None:
            return self._environment
        from trading_bot.providers.subscription_cli import sanitized_environment

        # Subscription login must never inherit API keys that could switch billing.
        return {
            key: value
            for key, value in sanitized_environment().items()
            if key != "CONTROL_API_TOKEN" and not key.startswith("HYVERION_")
        }
