"""Working-memory backends.

Working memory is disposable: it answers "what is happening now" and is always
rebuilt from the database plus reconciliation. The in-process adapter exists for
development and PAPER on a laptop; production refuses it instead of silently
degrading (see ``build_working_memory``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Protocol

import structlog

from trading_bot.core.clock import Clock
from trading_bot.memory.ports import WorkingMemoryBackend

MAX_KEY_LENGTH = 200

logger = structlog.get_logger(__name__)


class WorkingMemoryUnavailable(RuntimeError):
    """No acceptable working-memory backend for this environment."""


class InMemoryWorkingMemoryAdapter:
    """Process-local, TTL-aware store with the same JSON contract as Redis."""

    def __init__(self, clock: Clock, *, max_entries: int = 10_000) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._clock = clock
        self._max_entries = max_entries
        self._entries: dict[str, tuple[dict[str, Any], datetime | None]] = {}

    async def get(self, key: str) -> dict[str, Any] | None:
        _validate_key(key)
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock.now() >= expires_at:
            del self._entries[key]
            return None
        return _decode(_encode(value))

    async def set(
        self, key: str, value: dict[str, Any], *, ttl: timedelta | None = None
    ) -> None:
        _validate_key(key)
        if ttl is not None and ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        if key not in self._entries and len(self._entries) >= self._max_entries:
            self._evict_expired()
            if len(self._entries) >= self._max_entries:
                raise WorkingMemoryUnavailable("working memory capacity reached")
        expires_at = self._clock.now() + ttl if ttl is not None else None
        self._entries[key] = (_decode(_encode(value)), expires_at)

    async def delete(self, key: str) -> None:
        _validate_key(key)
        self._entries.pop(key, None)

    def _evict_expired(self) -> None:
        now = self._clock.now()
        for key in [k for k, (_, exp) in self._entries.items() if exp is not None and now >= exp]:
            del self._entries[key]


class _AsyncRedis(Protocol):
    async def get(self, name: str) -> Any: ...

    async def set(self, name: str, value: str, ex: int | None = None) -> Any: ...

    async def delete(self, *names: str) -> Any: ...


class RedisWorkingMemory:
    """Redis working memory. Disposable: no persistence, rebuilt from the database."""

    def __init__(self, client: _AsyncRedis, *, prefix: str) -> None:
        self._client = client
        self._prefix = prefix

    async def get(self, key: str) -> dict[str, Any] | None:
        _validate_key(key)
        try:
            raw = await self._client.get(self._key(key))
        except Exception as exc:  # boundary: surface as an explicit unavailability
            raise WorkingMemoryUnavailable(type(exc).__name__) from exc
        if raw is None:
            return None
        return _decode(raw.decode() if isinstance(raw, bytes) else str(raw))

    async def set(
        self, key: str, value: dict[str, Any], *, ttl: timedelta | None = None
    ) -> None:
        _validate_key(key)
        if ttl is not None and ttl <= timedelta(0):
            raise ValueError("ttl must be positive")
        seconds = max(1, int(ttl.total_seconds())) if ttl is not None else None
        payload = _encode(value)  # validation errors are not availability errors
        try:
            await self._client.set(self._key(key), payload, ex=seconds)
        except Exception as exc:
            raise WorkingMemoryUnavailable(type(exc).__name__) from exc

    async def delete(self, key: str) -> None:
        _validate_key(key)
        try:
            await self._client.delete(self._key(key))
        except Exception as exc:
            raise WorkingMemoryUnavailable(type(exc).__name__) from exc

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"


def build_working_memory(
    *,
    environment: str,
    backend: str,
    clock: Clock,
    redis_url: str | None = None,
    prefix: str = "hyverion",
) -> WorkingMemoryBackend:
    """Return the working-memory backend allowed for this environment.

    Production never falls back silently: it requires Redis.
    """

    if backend == "redis":
        if not redis_url:
            raise WorkingMemoryUnavailable("REDIS_URL is required for the redis backend")
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise WorkingMemoryUnavailable("install the 'memory' extra for Redis") from exc
        client = Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
        return RedisWorkingMemory(client, prefix=prefix)
    if environment == "production":
        raise WorkingMemoryUnavailable("production requires a Redis working-memory backend")
    logger.warning("working_memory_in_process", environment=environment)
    return InMemoryWorkingMemoryAdapter(clock)


def _encode(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        raise ValueError("working-memory values must be JSON objects")
    try:
        return json.dumps(value, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("working-memory values must be JSON-serializable") from exc


def _decode(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("working-memory payload is not a JSON object")
    return value


def _validate_key(key: str) -> None:
    if not key or len(key) > MAX_KEY_LENGTH or any(char.isspace() for char in key):
        raise ValueError("working-memory keys must be non-empty, bounded and without spaces")
