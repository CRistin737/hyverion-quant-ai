from __future__ import annotations

import pytest
from test_engine_supervisor import _settings

import trading_bot.main as main
from trading_bot.engine_supervisor import flatten_pending, request_flatten
from trading_bot.monitoring.metrics import MetricsRegistry


@pytest.mark.asyncio
async def test_orphans_get_protective_only_cycles_and_failures_are_recorded(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    calls: list[dict[str, object]] = []
    events: list[tuple[str, dict[str, str]]] = []

    async def fake_orphans(_settings, symbols):
        assert symbols == ("QQQ",)
        return ("IWM", "SMH")

    async def fake_cycle(**kwargs):
        calls.append(kwargs)
        if kwargs["symbol_override"] == "SMH":
            raise RuntimeError("venue down")
        return ()

    async def fake_event(_settings, status, detail):
        events.append((status, detail))

    monkeypatch.setattr(main, "_orphan_position_assets", fake_orphans)
    monkeypatch.setattr(main, "_paper_cycle", fake_cycle)
    monkeypatch.setattr(main, "_record_runtime_event", fake_event)
    scheduled: list[object] = []
    monkeypatch.setattr(main, "_schedule_daily_optimizer", scheduled.append)
    await main._maintenance_tick(settings, ("QQQ",), MetricsRegistry())

    assert scheduled == [settings]  # the daily optimizer check rides on maintenance
    assert [c["symbol_override"] for c in calls] == ["IWM", "SMH"]
    assert all(c["protective_only"] is True and c["collect_external"] is False for c in calls)
    assert events == [
        ("POSITION_PROTECTION_FAILED", {"symbol": "SMH", "error": "RuntimeError"})
    ]


@pytest.mark.asyncio
async def test_flatten_request_clears_once_flat(tmp_path) -> None:
    settings = _settings(tmp_path)
    request_flatten(settings)
    assert flatten_pending(settings)
    await main._complete_flatten_if_flat(settings)  # fresh DB: no open positions
    assert not flatten_pending(settings)


@pytest.mark.asyncio
async def test_flatten_request_stays_while_positions_are_open(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    request_flatten(settings)

    async def still_open(self):
        return [{"position_id": "p1", "asset": "QQQ"}]

    monkeypatch.setattr(main.TradingStateRepository, "open_positions", still_open)
    await main._complete_flatten_if_flat(settings)
    assert flatten_pending(settings)


@pytest.mark.asyncio
async def test_open_positions_are_rechecked_between_entry_cycles(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)  # position_check_seconds defaults to 10
    clock = {"t": 0.0}
    sleeps: list[float] = []
    cycles: list[tuple[str, bool]] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    async def fake_assets(_settings):
        return ("QQQ",)

    async def fake_cycle(**kwargs):
        cycles.append((kwargs["symbol_override"], kwargs["protective_only"]))
        return ()

    monkeypatch.setattr(main, "_open_position_assets", fake_assets)
    monkeypatch.setattr(main, "_paper_cycle", fake_cycle)
    await main._protect_until_next_cycle(
        settings, 60, MetricsRegistry(), sleep=fake_sleep, monotonic=lambda: clock["t"]
    )
    assert sum(sleeps) == 60  # the entry cadence is unchanged
    assert sleeps == [10, 10, 10, 10, 10, 10]
    # Checks every 10 s; the last slot is left to the next full cycle.
    assert cycles == [("QQQ", True)] * 5


@pytest.mark.asyncio
async def test_no_protective_cycles_without_open_positions(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    clock = {"t": 0.0}
    called: list[object] = []

    async def fake_sleep(seconds: float) -> None:
        clock["t"] += seconds

    async def no_assets(_settings):
        return ()

    async def fake_cycle(**kwargs):
        called.append(kwargs)

    monkeypatch.setattr(main, "_open_position_assets", no_assets)
    monkeypatch.setattr(main, "_paper_cycle", fake_cycle)
    await main._protect_until_next_cycle(
        settings, 30, MetricsRegistry(), sleep=fake_sleep, monotonic=lambda: clock["t"]
    )
    assert called == []


@pytest.mark.asyncio
async def test_protection_failure_is_recorded_and_does_not_stop_the_wait(
    tmp_path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    clock = {"t": 0.0}
    events: list[str] = []

    async def fake_sleep(seconds: float) -> None:
        clock["t"] += seconds

    async def fake_assets(_settings):
        return ("SPY",)

    async def failing_cycle(**kwargs):
        raise RuntimeError("venue down")

    async def fake_event(_settings, status, detail):
        events.append(status)

    monkeypatch.setattr(main, "_open_position_assets", fake_assets)
    monkeypatch.setattr(main, "_paper_cycle", failing_cycle)
    monkeypatch.setattr(main, "_record_runtime_event", fake_event)
    await main._protect_until_next_cycle(
        settings, 20, MetricsRegistry(), sleep=fake_sleep, monotonic=lambda: clock["t"]
    )
    assert events == ["POSITION_PROTECTION_FAILED"]
    assert clock["t"] == 20


@pytest.mark.asyncio
async def test_closed_market_protects_positions_records_once_and_sleeps(
    tmp_path, monkeypatch
) -> None:
    from datetime import UTC, datetime

    from trading_bot.core.clock import FixedClock
    from trading_bot.market.clock import MarketClock

    settings = _settings(tmp_path)
    saturday = MarketClock(FixedClock(datetime(2026, 9, 26, 15, 0, tzinfo=UTC))).snapshot()
    calls: list[dict[str, object]] = []
    events: list[str] = []
    naps: list[float] = []

    async def fake_open(_settings):
        return ("QQQ",)

    async def fake_cycle(**kwargs):
        calls.append(kwargs)
        return ()

    async def fake_event(_settings, status, detail):
        events.append(status)

    async def fake_tick(*_args):
        return None

    async def fake_sleep(seconds: float) -> None:
        naps.append(seconds)

    monkeypatch.setattr(main, "_open_position_assets", fake_open)
    monkeypatch.setattr(main, "_paper_cycle", fake_cycle)
    monkeypatch.setattr(main, "_record_runtime_event", fake_event)
    monkeypatch.setattr(main, "_maintenance_tick", fake_tick)
    monkeypatch.setattr(main, "_last_closed_event", None)
    for _ in range(2):
        await main._wait_for_session(settings, saturday, MetricsRegistry(), sleep=fake_sleep)

    # Only protective exits run while closed; the wait is announced once.
    assert [c["protective_only"] for c in calls] == [True, True]
    assert events == ["MARKET_CLOSED_WAITING"]
    assert all(
        settings.public.risk.position_check_seconds <= nap <= main.MARKET_CLOSED_MAX_SLEEP_SECONDS
        for nap in naps
    )
