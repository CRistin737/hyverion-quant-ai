"""Subscription limits: the 5-hour window and the weekly limit, per provider.

Subscriptions have no per-call price; the plan's usage limits are what matters.
Only official, local sources are read, and only the numbers:

* Claude Code: the documented ``claude -p /usage`` command
  (``Current session`` = 5-hour window, ``Current week (all models)``).
* Codex: the ``rate_limits`` block Codex writes into its own session logs
  (``primary`` = 5-hour window, ``secondary`` = weekly). Only that block is
  parsed; prompts and outputs in those files are never read into memory beyond
  the line that carries it.
* Grok and Gemini expose no limit data: ``available=False``, never a guess.

Results are cached for a few minutes so the UI can poll cheaply.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_bot.providers.access import _check_claude_usage

CACHE_TTL = timedelta(minutes=5)
# Codex keeps one folder per day; the newest few days are enough to find the
# latest rate-limit snapshot without walking months of history.
CODEX_DAYS_SCANNED = 7
# A Codex snapshot older than its own window says nothing about "now".
CODEX_MAX_SNAPSHOT_AGE = timedelta(days=7)


@dataclass(slots=True)
class SubscriptionUsage:
    provider_id: str
    available: bool
    session_used_percent: Decimal | None = None
    session_resets_at: str | None = None
    weekly_used_percent: Decimal | None = None
    weekly_resets_at: str | None = None
    source: str = "unavailable"
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def exhausted(self) -> bool:
        return any(
            value is not None and value >= 100
            for value in (self.session_used_percent, self.weekly_used_percent)
        )

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("session_used_percent", "weekly_used_percent"):
            value = payload[key]
            payload[key] = None if value is None else str(value)
        payload["checked_at"] = self.checked_at.isoformat()
        payload["exhausted"] = self.exhausted
        return payload


async def claude_usage() -> SubscriptionUsage:
    if shutil.which("claude") is None:
        return SubscriptionUsage("anthropic", False, detail="cli_missing")
    raw = await _check_claude_usage(45.0)
    if raw is None:
        return SubscriptionUsage("anthropic", False, detail="usage_unreadable")

    def used(remaining: Decimal | None) -> Decimal | None:
        return None if remaining is None else max(Decimal("0"), Decimal("100") - remaining)

    return SubscriptionUsage(
        "anthropic",
        True,
        session_used_percent=used(raw.get("session_remaining")),
        session_resets_at=raw.get("session_reset_label"),
        weekly_used_percent=used(raw.get("weekly_remaining")),
        weekly_resets_at=raw.get("weekly_reset_label"),
        source="claude /usage",
    )


def codex_usage(home: Path | None = None, *, now: datetime | None = None) -> SubscriptionUsage:
    root = (home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")) / "sessions"
    now = now or datetime.now(UTC)
    if not root.is_dir():
        return SubscriptionUsage("openai", False, detail="no_codex_sessions")
    day_dirs = sorted((path for path in root.glob("*/*/*") if path.is_dir()), reverse=True)
    files: list[Path] = []
    for directory in day_dirs[:CODEX_DAYS_SCANNED]:
        files.extend(directory.glob("*.jsonl"))
    for path, modified in sorted(_mtimes(files), key=lambda item: item[1], reverse=True):
        limits = _last_rate_limits(path)
        if limits is None:
            continue
        observed = datetime.fromtimestamp(modified, UTC)
        if now - observed > CODEX_MAX_SNAPSHOT_AGE:
            break
        primary, secondary = limits.get("primary") or {}, limits.get("secondary") or {}
        return SubscriptionUsage(
            "openai",
            True,
            session_used_percent=_percent(primary.get("used_percent")),
            session_resets_at=_epoch(primary.get("resets_at")),
            weekly_used_percent=_percent(secondary.get("used_percent")),
            weekly_resets_at=_epoch(secondary.get("resets_at")),
            source="codex session log",
            detail=f"observed {observed.isoformat()}",
        )
    return SubscriptionUsage("openai", False, detail="no_recent_rate_limits")


def _last_rate_limits(path: Path) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            lines = [line for line in handle if '"rate_limits"' in line]
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        found = _find_rate_limits(event)
        if found is not None:
            return found
    return None


def _find_rate_limits(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        limits = value.get("rate_limits")
        if isinstance(limits, dict) and ("primary" in limits or "secondary" in limits):
            return limits
        for child in value.values():
            found = _find_rate_limits(child)
            if found is not None:
                return found
    return None


def _percent(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except ArithmeticError:
        return None


def _mtimes(files: list[Path]) -> list[tuple[Path, float]]:
    """Modification times; a log rotated away while listing is skipped."""

    out: list[tuple[Path, float]] = []
    for path in files:
        try:
            out.append((path, path.stat().st_mtime))
        except OSError:
            continue
    return out


def _epoch(value: Any) -> str | None:
    if not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


async def read_usage(provider_id: str) -> SubscriptionUsage:
    if provider_id == "anthropic":
        return await claude_usage()
    if provider_id == "openai":
        return await asyncio.to_thread(codex_usage)
    return SubscriptionUsage(provider_id, False, detail="provider_publishes_no_limits")


class UsageCache:
    """Per-process cache so polling the UI does not spawn a CLI every time."""

    def __init__(self, ttl: timedelta = CACHE_TTL) -> None:
        self._ttl = ttl
        self._items: dict[str, SubscriptionUsage] = {}
        self._lock = asyncio.Lock()

    def peek(self, provider_id: str) -> SubscriptionUsage | None:
        return self._items.get(provider_id)

    def forget(self, provider_id: str) -> None:
        self._items.pop(provider_id, None)

    async def get(self, provider_id: str, *, refresh: bool = False) -> SubscriptionUsage:
        async with self._lock:
            cached = self._items.get(provider_id)
            if (
                cached is not None
                and not refresh
                and datetime.now(UTC) - cached.checked_at < self._ttl
            ):
                return cached
            usage = await read_usage(provider_id)
            self._items[provider_id] = usage
            return usage
