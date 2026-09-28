"""Per-provider circuit breaker shared by every cycle of the engine process.

A subscription that ran out of quota, lost its login, or is being rate limited
must not be retried by every agent on every cycle (each try can burn the full
request timeout). The board remembers failures across cycles, skips a provider
while its circuit is open, and lets one probe through after the cooldown so the
primary recovers automatically without operator action.

Failover itself stays in ``ModelRouter``; this module only decides *when a hop is
worth trying*. It never selects a provider outside the configured chain.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# code -> (base cooldown seconds, max cooldown seconds)
COOLDOWNS: dict[str, tuple[float, float]] = {
    # The operator has to act (log in again / wait for the quota window).
    "provider_auth_required": (300.0, 900.0),
    "subscription_cli_missing": (600.0, 1800.0),
    "provider_quota_exhausted": (1800.0, 14400.0),
    # Transient: back off quickly, recover quickly.
    "provider_rate_limited": (60.0, 600.0),
    "provider_overloaded": (60.0, 600.0),
    "provider_timeout": (30.0, 300.0),
    "provider_request_failed": (30.0, 300.0),
    "provider_runtime_error": (30.0, 300.0),
}
DEFAULT_COOLDOWN = (30.0, 300.0)
# Codes that describe a bad *answer*, not an unavailable provider.
PROBE_LEASE_SECONDS = 120.0
NEVER_TRIP = frozenset({"invalid_structured_output"})


@dataclass(slots=True)
class _Entry:
    failures: int = 0
    open_until: float = 0.0
    last_code: str | None = None
    last_failure_at: float | None = None
    # Half-open probe lease; expires so a cancelled probe cannot wedge the provider.
    probe_until: float = 0.0


@dataclass(slots=True)
class ProviderHealthBoard:
    """Thread-safe failure memory with exponential per-code cooldowns."""

    monotonic: Callable[[], float] = time.monotonic
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self, provider_id: str) -> bool:
        """True when the provider may be called now (closed, or cooldown elapsed)."""

        with self._lock:
            entry = self._entries.get(provider_id)
            if entry is None or entry.failures == 0:
                return True
            now = self.monotonic()
            if now < entry.open_until:
                return False
            if now < entry.probe_until:
                # Another caller already holds the half-open probe.
                return False
            entry.probe_until = now + PROBE_LEASE_SECONDS
            return True

    def record_success(self, provider_id: str) -> None:
        with self._lock:
            self._entries.pop(provider_id, None)

    def record_failure(self, provider_id: str, code: str) -> None:
        if code in NEVER_TRIP:
            with self._lock:
                entry = self._entries.get(provider_id)
                if entry is not None:
                    entry.probe_until = 0.0
            return
        base, cap = COOLDOWNS.get(code, DEFAULT_COOLDOWN)
        with self._lock:
            entry = self._entries.setdefault(provider_id, _Entry())
            entry.failures += 1
            entry.probe_until = 0.0
            entry.last_code = code
            now = self.monotonic()
            entry.last_failure_at = now
            entry.open_until = now + min(cap, base * (2 ** (entry.failures - 1)))

    def reset(self, provider_id: str | None = None) -> None:
        """Forget failures, e.g. after the operator logs in again."""

        with self._lock:
            if provider_id is None:
                self._entries.clear()
            else:
                self._entries.pop(provider_id, None)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Bounded, secret-free view for the UI and audit events."""

        now = self.monotonic()
        with self._lock:
            return {
                provider_id: {
                    "state": "open" if now < entry.open_until else "half_open",
                    "failures": entry.failures,
                    "last_code": entry.last_code,
                    "retry_in_seconds": max(0, round(entry.open_until - now)),
                }
                for provider_id, entry in self._entries.items()
                if entry.failures
            }


# One board per engine process: the run loop builds a fresh router every cycle
# but must remember which subscriptions are unavailable.
PROCESS_HEALTH = ProviderHealthBoard()
