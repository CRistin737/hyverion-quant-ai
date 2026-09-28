from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_bot.core.clock import FixedClock
from trading_bot.memory.ports import WorkingMemoryBackend
from trading_bot.memory.working import (
    InMemoryWorkingMemoryAdapter,
    WorkingMemoryUnavailable,
    build_working_memory,
)

NOW = datetime(2026, 9, 23, tzinfo=UTC)


class _MutableClock(FixedClock):
    def advance(self, delta: timedelta) -> None:
        self._value = self._value + delta


async def test_values_are_isolated_copies() -> None:
    memory: WorkingMemoryBackend = InMemoryWorkingMemoryAdapter(FixedClock(NOW))
    value = {"price": "105", "nested": {"regime": "RANGING"}}
    await memory.set("market:QQQ:snapshot", value)
    value["nested"]["regime"] = "mutated"

    stored = await memory.get("market:QQQ:snapshot")
    assert stored == {"price": "105", "nested": {"regime": "RANGING"}}
    assert stored is not None
    stored["price"] = "0"
    assert (await memory.get("market:QQQ:snapshot")) == {
        "price": "105",
        "nested": {"regime": "RANGING"},
    }


async def test_ttl_expires_entries() -> None:
    clock = _MutableClock(NOW)
    memory = InMemoryWorkingMemoryAdapter(clock)
    await memory.set("risk:current", {"level": 0}, ttl=timedelta(seconds=30))
    assert await memory.get("risk:current") == {"level": 0}
    clock.advance(timedelta(seconds=30))
    assert await memory.get("risk:current") is None


async def test_capacity_is_bounded_and_invalid_input_rejected() -> None:
    memory = InMemoryWorkingMemoryAdapter(FixedClock(NOW), max_entries=1)
    await memory.set("a", {})
    await memory.set("a", {"overwrite": True})
    with pytest.raises(WorkingMemoryUnavailable):
        await memory.set("b", {})
    with pytest.raises(ValueError):
        await memory.set("has space", {})
    with pytest.raises(ValueError):
        await memory.set("c", {}, ttl=timedelta(0))
    await memory.delete("a")
    assert await memory.get("a") is None


def test_production_refuses_in_process_working_memory() -> None:
    clock = FixedClock(NOW)
    with pytest.raises(WorkingMemoryUnavailable):
        build_working_memory(environment="production", backend="in_process", clock=clock)
    with pytest.raises(WorkingMemoryUnavailable):
        build_working_memory(environment="development", backend="redis", clock=clock)
    assert isinstance(
        build_working_memory(environment="development", backend="in_process", clock=clock),
        InMemoryWorkingMemoryAdapter,
    )


async def test_values_must_be_json_objects() -> None:
    from decimal import Decimal

    memory = InMemoryWorkingMemoryAdapter(FixedClock(NOW))
    with pytest.raises(ValueError):
        await memory.set("risk:current", {"equity": Decimal("1")})
    with pytest.raises(ValueError):
        await memory.set("risk:current", {"nan": float("nan")})
