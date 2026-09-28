from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

import pytest
from conftest import control_client
from test_engine_supervisor import _settings

from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.market.clock import nyse_calendar
from trading_bot.schemas.trading import Candle
from trading_bot.simulation.strategy_replay import MIN_CANDLES, replay_plugin, replay_strategies
from trading_bot.strategies.registry import strategy_factories

T0 = datetime(2026, 9, 1, 13, 31, tzinfo=UTC)  # first bar close, Tuesday 09:31 ET


def session_times(n: int) -> list[datetime]:
    """Bar close times inside real NYSE sessions (skips Labor Day and weekends)."""

    calendar = nyse_calendar()
    times: list[datetime] = []
    for label in calendar.sessions_in_range("2026-09-01", "2026-12-31"):
        opened = calendar.session_open(label).to_pydatetime()
        closed = calendar.session_close(label).to_pydatetime()
        minute = opened + timedelta(minutes=1)
        while minute <= closed and len(times) < n:
            times.append(minute)
            minute += timedelta(minutes=1)
        if len(times) >= n:
            return times
    return times


def candles(closes: list[Decimal], symbol: str = "QQQ") -> list[Candle]:
    times = session_times(len(closes))
    return [
        Candle(
            symbol=symbol,
            interval="1m",
            open=close,
            high=close * Decimal("1.001"),
            low=close * Decimal("0.999"),
            close=close,
            volume=Decimal("100"),
            trades=10,
            event_time=moment,
            received_time=moment,
            processed_time=moment,
        )
        for close, moment in zip(closes, times, strict=True)
    ]


def zigzag(n: int) -> list[Decimal]:
    # Rising trend with pullbacks: gives the momentum plugin something to do.
    step, wobble = Decimal("0.05"), Decimal("0.3")
    return [Decimal("100") + Decimal(i) * step + Decimal(i % 7) * wobble for i in range(n)]


class SpyPlugin:
    strategy_id = "spy"
    version = "0"
    seen: ClassVar[list[tuple[datetime, Decimal, Decimal]]] = []

    def __init__(self, clock) -> None:
        self._clock = clock

    def propose(self, snapshot, features, *, risk_budget_usd):
        SpyPlugin.seen.append((snapshot.event_time, snapshot.last, features.fast_sma))
        return None


def test_replay_never_uses_future_candles() -> None:
    base = zigzag(120)
    changed = base[:80] + [Decimal("5000")] * 40  # the future after bar 79 is wildly different
    SpyPlugin.seen = []
    replay_plugin(SpyPlugin, candles(base), capital=Decimal("1000"))
    first = [row for row in SpyPlugin.seen if row[0] <= T0 + timedelta(minutes=79)]
    SpyPlugin.seen = []
    replay_plugin(SpyPlugin, candles(changed), capital=Decimal("1000"))
    second = [row for row in SpyPlugin.seen if row[0] <= T0 + timedelta(minutes=79)]
    assert first and first == second


ALL_PLUGINS = ("trend_pullback", "opening_range_breakout", "mean_reversion", "trend_momentum")


def test_real_plugins_produce_split_evidence_with_costs() -> None:
    settings = load_settings()
    config = settings.public.strategies.model_copy(update={"enabled": ALL_PLUGINS})
    report = replay_strategies(
        strategy_factories(config), candles(zigzag(900)), capital=Decimal("1000")
    )
    assert {p.strategy_id for p in report.plugins} == set(ALL_PLUGINS)
    assert report.sessions == 3 and report.data_audit["ok"] is True
    total = sum(p.in_sample.trades + p.out_of_sample.trades for p in report.plugins)
    assert total > 0
    for plugin in report.plugins:
        assert set(plugin.oos_by_scenario) == {"optimistic", "base", "stress"}
        assert plugin.oos_by_scenario["base"] == plugin.out_of_sample
        assert len(plugin.walk_forward) == 4
        assert plugin.cost_verdict in {
            "robust_to_costs", "fails_stress_costs", "rejected_only_optimistic", "not_profitable"
        }
        scenarios = ("optimistic", "base", "stress")
        costs = [plugin.oos_by_scenario[name].net_pnl_usd for name in scenarios]
        assert costs == sorted(costs, reverse=True)  # more cost, never more profit
        for stats in (plugin.in_sample, plugin.out_of_sample):
            assert stats.wins <= stats.trades
            assert sum(stats.exits.values()) == stats.trades
            if stats.trades:
                assert stats.fees_usd > 0  # costs are always charged
                assert stats.expectancy_usd is not None
    assert report.first_bar < report.split_bar < report.last_bar


def test_replay_refuses_data_that_fails_the_audit() -> None:
    factories = strategy_factories(load_settings().public.strategies)
    doubled = candles(zigzag(200))
    with pytest.raises(ValueError, match="data_audit_failed"):
        replay_strategies(factories, doubled + doubled[-5:], capital=Decimal("1000"))


def test_replay_rejects_short_or_mixed_history() -> None:
    factories = strategy_factories(load_settings().public.strategies)
    with pytest.raises(ValueError):
        replay_strategies(factories, candles(zigzag(MIN_CANDLES - 1)), capital=Decimal("1000"))
    mixed = candles(zigzag(60)) + candles(zigzag(60), symbol="SPY")
    with pytest.raises(ValueError):
        replay_strategies(factories, mixed, capital=Decimal("1000"))


def _app(tmp_path, source):
    settings = _settings(tmp_path)
    return create_control_api(settings, candle_source=source)


def test_endpoint_persists_an_experiment_from_real_candles(tmp_path) -> None:
    calls: list[tuple[str, int]] = []

    async def source(symbol: str, limit: int):
        calls.append((symbol, limit))
        return tuple(candles(zigzag(300), symbol=symbol))

    with control_client(_app(tmp_path, source)) as client:
        response = client.post("/api/v1/backtest/strategies", json={"candles": 300})
        assert response.status_code == 200
        body = response.json()
        assert body["period"] == "strategy replay" and body["candles"] == 300
        assert calls == [("QQQ", 300)]
        experiments = client.get("/api/v1/snapshot").json()["experiments"]
        assert any(row.get("experiment_id") == body["experiment_id"] for row in experiments)


def test_endpoint_fails_closed(tmp_path) -> None:
    async def down(symbol: str, limit: int):
        raise RuntimeError("network")

    async def short(symbol: str, limit: int):
        return tuple(candles(zigzag(10), symbol=symbol))

    with control_client(_app(tmp_path, down)) as client:
        assert client.post("/api/v1/backtest/strategies", json={}).json()["detail"] == {
            "code": "market_data_unavailable"
        }
        refused = client.post("/api/v1/backtest/strategies", json={"symbol": "NVDA"})
        assert refused.status_code == 422
        too_many = client.post("/api/v1/backtest/strategies", json={"candles": 30000})
        assert too_many.status_code == 422
    with control_client(_app(tmp_path, short)) as client:
        assert client.post("/api/v1/backtest/strategies", json={}).json()["detail"] == {
            "code": "insufficient_history"
        }


def test_production_exit_defaults_are_unchanged() -> None:
    from trading_bot.strategies.registry import exit_defaults

    pullback = exit_defaults("trend_pullback")
    assert (pullback.stop_percent, pullback.reward_multiple, pullback.horizon_seconds) == (
        Decimal("0.4"),
        Decimal("2"),
        3600,
    )
    assert exit_defaults("opening_range_breakout").stop_percent == Decimal("0.35")
    assert exit_defaults("mean_reversion").reward_multiple == Decimal("1.5")
    assert exit_defaults("trend_momentum").stop_percent == Decimal("1")


def test_exit_params_are_bounded() -> None:
    from trading_bot.strategies.base import ExitParams

    for bad in (
        {"stop_percent": Decimal("0"), "reward_multiple": Decimal("2"), "horizon_seconds": 600},
        {"stop_percent": Decimal("1"), "reward_multiple": Decimal("20"), "horizon_seconds": 600},
        {"stop_percent": Decimal("1"), "reward_multiple": Decimal("2"), "horizon_seconds": 10},
    ):
        with pytest.raises(ValueError):
            ExitParams(**bad)


def test_challenger_is_judged_out_of_sample_with_reasons() -> None:
    from trading_bot.simulation.strategy_replay import compare_champion_challenger
    from trading_bot.strategies.base import ExitParams
    from trading_bot.strategies.registry import strategy_factory

    config = load_settings().public.strategies
    tight = ExitParams(
        stop_percent=Decimal("0.3"), reward_multiple=Decimal("1.5"), horizon_seconds=900
    )
    report = compare_champion_challenger(
        strategy_factory(config, "trend_momentum"),
        strategy_factory(config, "trend_momentum", tight),
        candles(zigzag(400)),
        capital=Decimal("1000"),
        champion_params={"stop_percent": "1"},
        challenger_params={"stop_percent": "0.3"},
    )
    assert report.strategy_id == "trend_momentum"
    # Fewer than 100 OOS trades can never be ready, whatever the PnL.
    assert report.verdict.ready_for_review is False
    assert "insufficient_oos_samples" in report.verdict.reasons
    assert report.champion.out_of_sample.trades + report.challenger.out_of_sample.trades > 0


def test_challenger_endpoint_persists_and_fails_closed(tmp_path) -> None:
    async def source(symbol: str, limit: int):
        return tuple(candles(zigzag(300), symbol=symbol))

    body = {
        "strategy_id": "trend_pullback",
        "stop_percent": "0.6",
        "reward_multiple": "1.5",
        "horizon_minutes": 30,
    }
    with control_client(_app(tmp_path, source)) as client:
        ok = client.post("/api/v1/backtest/challenger", json=body)
        assert ok.status_code == 200
        data = ok.json()
        assert data["period"] == "champion challenger"
        assert data["challenger_params"]["horizon_minutes"] == "30"
        assert data["champion_params"]["stop_percent"] == "0.4"
        assert client.post(
            "/api/v1/backtest/challenger", json={**body, "strategy_id": "mean_reversion"}
        ).json()["detail"] == {"code": "strategy_not_enabled"}
        assert client.post(
            "/api/v1/backtest/challenger", json={**body, "stop_percent": "50"}
        ).status_code == 422


def test_intrabar_path_is_pessimistic() -> None:
    from trading_bot.schemas.common import Side
    from trading_bot.simulation.strategy_replay import _price_path

    bar = candles([Decimal("100")])[0].model_copy(
        update={"low": Decimal("98"), "high": Decimal("103"), "close": Decimal("101")}
    )
    # A long sees the low (stop side) before the high (target side); a short the opposite.
    assert _price_path([bar], Side.BUY) == (Decimal("98"), Decimal("103"), Decimal("101"))
    assert _price_path([bar], Side.SELL) == (Decimal("103"), Decimal("98"), Decimal("101"))


def test_regime_classifier_is_deterministic_and_directional() -> None:
    from trading_bot.simulation.strategy_replay import classify_regime

    rising = [Decimal("100") + Decimal(i) * Decimal("0.2") for i in range(60)]
    falling = list(reversed(rising))
    flat = [Decimal("100") + (Decimal("0.3") if i % 2 else Decimal("0")) for i in range(60)]
    assert classify_regime(rising) == "trending_up"
    assert classify_regime(falling) == "trending_down"
    assert classify_regime(flat) == "ranging"
    assert classify_regime(rising[:5]) == "unknown"
    # Only the trailing window matters: older history cannot flip today's label.
    assert classify_regime(falling + rising) == "trending_up"


def test_oos_regime_breakdown_adds_up_to_the_oos_totals() -> None:
    report = replay_strategies(
        strategy_factories(load_settings().public.strategies),
        candles(zigzag(600)),
        capital=Decimal("1000"),
    )
    for plugin in report.plugins:
        parts = plugin.oos_by_regime.values()
        assert sum(s.trades for s in parts) == plugin.out_of_sample.trades
        assert sum((s.net_pnl_usd for s in parts), Decimal("0")) == plugin.out_of_sample.net_pnl_usd


class LateBuyer:
    """Buys once near the close with a 4-hour horizon: the replay must end it at the bell."""

    strategy_id = "late"
    version = "0"

    def __init__(self, clock) -> None:
        self._clock = clock

    def propose(self, snapshot, features, *, risk_budget_usd):
        from trading_bot.market.clock import NEW_YORK

        local = snapshot.event_time.astimezone(NEW_YORK)
        if (local.hour, local.minute) != (15, 55):
            return None
        from conftest import make_proposal

        price = snapshot.ask
        return make_proposal(
            self._clock.now(),
            entry_price=price,
            stop_price=price * Decimal("0.9"),
            target_price=price * Decimal("1.1"),
            time_horizon_seconds=4 * 3600,
        )


def test_replay_never_holds_a_position_overnight() -> None:
    flat = [Decimal("480")] * 800  # two sessions and a bit: 390 + 390 + 20
    result = replay_plugin(LateBuyer, candles(flat), capital=Decimal("1000"))
    exits = {**result.in_sample.exits}
    for name, count in result.out_of_sample.exits.items():
        exits[name] = exits.get(name, 0) + count
    assert exits and set(exits) == {"session_close"}
