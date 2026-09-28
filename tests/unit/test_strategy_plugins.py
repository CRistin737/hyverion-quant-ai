"""QQQ session strategies: each setup trades only when its conditions hold, and says why not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_bot.config import load_settings
from trading_bot.core.clock import FixedClock
from trading_bot.data.features import FeatureEngine, FeatureSet
from trading_bot.schemas.trading import Candle, MarketSnapshot
from trading_bot.strategies.ensemble import StrategyEnsemble
from trading_bot.strategies.mean_reversion import MeanReversionStrategy
from trading_bot.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from trading_bot.strategies.regime import assess_regime
from trading_bot.strategies.registry import build_strategy_ensemble
from trading_bot.strategies.trend_pullback import TrendPullbackStrategy

OPEN = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)  # Monday 09:30 New York
SETTINGS = load_settings().public.strategies


def bars(
    closes: list[str], *, volumes: list[int] | None = None, wiggle: str = "0.15"
) -> list[Candle]:
    out: list[Candle] = []
    for minute, raw in enumerate(closes, start=1):
        close = Decimal(raw)
        end = OPEN + timedelta(minutes=minute)
        out.append(
            Candle(symbol="QQQ", interval="1m", open=close, high=close + Decimal(wiggle),
                   low=close - Decimal(wiggle), close=close,
                   volume=Decimal((volumes or [1000] * len(closes))[minute - 1]), vwap=close,
                   trades=10, event_time=end, received_time=end, processed_time=end)
        )
    return out


def snapshot(candles: list[Candle]) -> MarketSnapshot:
    last = candles[-1]
    return MarketSnapshot(
        symbol="QQQ", bid=last.close - Decimal("0.01"), ask=last.close, last=last.close,
        session_volume=Decimal("2000000"), session_dollar_volume=Decimal("960000000"),
        event_time=last.event_time, received_time=last.event_time,
        processed_time=last.event_time,
    )


def features(candles: list[Candle]) -> FeatureSet:
    return FeatureEngine().compute("QQQ", tuple(c.close for c in candles[-60:]), candles)


def plugin(cls, when: datetime):
    return cls(SETTINGS.signal_weights, SETTINGS.signal_weights_version, FixedClock(when))


def uptrend_with_pullback() -> list[str]:
    # 50 minutes of steady climb, a 4-minute dip back to the fast EMA, then a turn up.
    rise = [f"{480 + i * 0.05:.2f}" for i in range(50)]
    dip = ["482.30", "482.00", "481.80", "481.70"]
    return [*rise, *dip, "481.95"]


def test_trend_pullback_buys_a_resuming_dip_in_an_uptrend() -> None:
    candles = bars(uptrend_with_pullback(), wiggle="0.6")
    strategy = plugin(TrendPullbackStrategy, candles[-1].event_time)
    proposal = strategy.propose(
        snapshot(candles), features(candles), risk_budget_usd=Decimal("2.5")
    )
    assert proposal is not None, strategy.last_no_trade
    assert proposal.asset == "QQQ" and proposal.side.value == "buy"
    assert proposal.stop_price < proposal.entry_price < proposal.target_price
    assert proposal.expected_r == Decimal("2")
    assert {"trend", "vwap", "pullback", "liquidity"} <= proposal.confirmation_categories
    assert proposal.expected_net_value_usd > 0
    # Worst case stays inside the risk budget (costs included in the sizing).
    assert proposal.stop_loss_usd <= Decimal("2.5")


def test_trend_pullback_explains_every_refusal() -> None:
    falling = bars([f"{490 - i * 0.1:.2f}" for i in range(55)])
    strategy = plugin(TrendPullbackStrategy, falling[-1].event_time)
    refused = strategy.propose(snapshot(falling), features(falling), risk_budget_usd=Decimal("2"))
    assert refused is None
    assert {"price_below_vwap", "no_intraday_uptrend"} <= set(strategy.last_no_trade)
    early = bars(uptrend_with_pullback()[:10])
    assert strategy.propose(snapshot(early), features(early), risk_budget_usd=Decimal("2")) is None
    assert "insufficient_session_bars" in strategy.last_no_trade


def test_opening_range_breakout_needs_volume_and_a_fresh_break() -> None:
    # 40 quiet minutes inside 480.00-480.60, then a close above the range on heavy volume.
    quiet = ["480.30", "480.40", "480.20", "480.50", "480.30"] * 8
    closes = [*quiet, "480.45", "480.70"]
    heavy = [1000] * (len(closes) - 1) + [4000]
    candles = bars(closes, volumes=heavy)
    strategy = plugin(OpeningRangeBreakoutStrategy, candles[-1].event_time)
    feats = features(candles)
    assert feats.opening_range_high == Decimal("480.65")
    proposal = strategy.propose(snapshot(candles), feats, risk_budget_usd=Decimal("2.5"))
    assert proposal is not None, strategy.last_no_trade
    assert "breakout" in proposal.confirmation_categories

    thin = bars(closes)  # same break, ordinary volume
    assert strategy.propose(snapshot(thin), features(thin), risk_budget_usd=Decimal("2")) is None
    assert "breakout_without_volume" in strategy.last_no_trade


def test_mean_reversion_only_in_a_range_and_only_when_stretched() -> None:
    # Flat range around 480 then a sharp dip well below VWAP that starts to turn.
    flat = ["480.0", "480.6", "479.4", "480.4", "479.6"] * 10
    dip = ["479.3", "478.8", "478.3", "477.8", "477.3", "476.8", "476.3", "475.9"]
    candles = bars([*flat, *dip, "476.05"], wiggle="0.1")
    strategy = plugin(MeanReversionStrategy, candles[-1].event_time)
    feats = features(candles)
    proposal = strategy.propose(snapshot(candles), feats, risk_budget_usd=Decimal("2.5"))
    assert proposal is not None, strategy.last_no_trade
    assert proposal.expected_r == Decimal("1.5")
    assert feats.session_vwap is not None and proposal.target_price <= feats.session_vwap
    still_falling = bars([*flat, *dip], wiggle="0.1")
    assert strategy.propose(
        snapshot(still_falling), features(still_falling), risk_budget_usd=Decimal("2")
    ) is None
    assert "still_falling" in strategy.last_no_trade
    trend = bars([f"{480 + i * 0.1:.2f}" for i in range(55)])
    assert strategy.propose(snapshot(trend), features(trend), risk_budget_usd=Decimal("2")) is None
    assert "regime_not_ranging" in strategy.last_no_trade


def test_regime_has_a_primary_label_secondary_tags_and_confidence() -> None:
    trend = bars([f"{480 + i * 0.1:.2f}" for i in range(55)], wiggle="0.6")
    state = assess_regime(features(trend))
    assert state.primary.value == "TRENDING_UP"
    assert 0 <= state.confidence <= 1
    assert "HIGH_VOLATILITY" in state.as_dict()["secondary"]  # type: ignore[operator]
    opening = bars(["480.0"] * 10)
    assert "OPENING_DISCOVERY" in assess_regime(features(opening)).as_dict()["secondary"]  # type: ignore[operator]


def test_registry_builds_an_ensemble_that_reports_why_not() -> None:
    config = SETTINGS.model_copy(
        update={"enabled": ("trend_pullback", "opening_range_breakout", "mean_reversion")}
    )
    ensemble = build_strategy_ensemble(config, clock=FixedClock(OPEN))
    assert isinstance(ensemble, StrategyEnsemble)
    assert ensemble.plugin_ids == ("trend_pullback", "opening_range_breakout", "mean_reversion")
    few = bars(["480.0"] * 8)
    assert ensemble.propose(snapshot(few), features(few), risk_budget_usd=Decimal("2")) is None
    assert "trend_pullback:insufficient_session_bars" in ensemble.last_no_trade
    assert any(reason.startswith("mean_reversion:") for reason in ensemble.last_no_trade)


def test_hyverion_strategy_routes_by_regime_and_needs_confluence() -> None:
    from trading_bot.strategies.hyverion_strategy import HyverionStrategy

    candles = bars(uptrend_with_pullback(), wiggle="0.6")
    strategy = plugin(HyverionStrategy, candles[-1].event_time)
    budget = Decimal("2.5")
    proposal = strategy.propose(snapshot(candles), features(candles), risk_budget_usd=budget)
    assert proposal is not None, strategy.last_no_trade
    assert "[hyverion_strategy]" in proposal.why_now
    assert any(item.category == "confluence" for item in proposal.evidence)
    # The same setup with a macro gate and weak breadth: the evidence says no.
    strategy.set_intelligence(
        {
            "macro": {"gate": "macro_event_pre_block", "upcoming": []},
            "breadth": {"weighted_breadth": "0.1", "pct_green": "0.1", "top10_breadth": "0.1",
                        "divergences": ["qqq_up_breadth_weak"]},
        }
    )
    assert strategy.propose(snapshot(candles), features(candles), risk_budget_usd=budget) is None
    assert strategy.last_no_trade[0].startswith("low_confluence")
    falling = bars([f"{490 - i * 0.1:.2f}" for i in range(55)])
    strategy.set_intelligence(None)
    assert strategy.propose(snapshot(falling), features(falling), risk_budget_usd=budget) is None
    assert strategy.last_no_trade[0].startswith("regime_not_tradable")
