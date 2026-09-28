"""Shared machinery for the QQQ session strategies (long-only, regular session).

Each plugin decides *whether* a setup exists and explains *why not* when it
does not (reason codes, §121). This module turns a setup into a sized,
cost-aware ``TradeProposal`` the same way for every plugin:

* risk-based size: ``risk budget / (stop distance + per-share round-trip cost)``;
* costs from the BASE scenario of ``simulation/costs.py`` (spread, slippage,
  sell fees), so live proposals and replays price trades identically;
* if the expected edge does not beat the expected cost, there is no trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import Evidence, Side
from trading_bot.schemas.trading import MarketSnapshot, SignalComponents, TradeProposal
from trading_bot.simulation.costs import BASE_COSTS, BPS_DENOMINATOR
from trading_bot.strategies.base import ExitParams
from trading_bot.strategies.signal_score import compute_signal_score

MIN_SESSION_BARS = 30
MIN_STOP_ATR = Decimal("0.8")  # a stop inside normal noise is just a coin flip


@dataclass(frozen=True, slots=True)
class Setup:
    technical: Decimal
    regime: Decimal
    confirmations: frozenset[str]
    evidence: tuple[Evidence, ...]
    invalidations: tuple[str, ...]
    why_now: str
    is_a_plus: bool = False


def session_gaps(features: FeatureSet) -> list[str]:
    """Reasons the session features are not usable yet (fail closed)."""

    reasons: list[str] = []
    if features.session_bars < MIN_SESSION_BARS:
        reasons.append("insufficient_session_bars")
    if features.session_vwap is None:
        reasons.append("vwap_unavailable")
    if features.atr is None or features.atr <= 0:
        reasons.append("atr_unavailable")
    return reasons


def build_proposal(
    *,
    strategy_id: str,
    snapshot: MarketSnapshot,
    features: FeatureSet,
    setup: Setup,
    exit_params: ExitParams,
    risk_budget_usd: Decimal,
    weights: dict[str, Decimal],
    weights_version: str,
    clock: Clock,
) -> tuple[TradeProposal | None, tuple[str, ...]]:
    """Size and price a long setup; returns (proposal, why_not_trade)."""

    if risk_budget_usd <= 0:
        return None, ("risk_budget_exhausted",)
    entry = snapshot.ask
    stop_distance = entry * exit_params.stop_percent / Decimal("100")
    atr = features.atr or Decimal("0")
    if atr > 0 and stop_distance < atr * MIN_STOP_ATR:
        return None, ("stop_inside_noise",)
    stop = entry - stop_distance
    target = entry + stop_distance * exit_params.reward_multiple
    cost_bps = BASE_COSTS.round_trip_bps(snapshot.spread_bps)
    per_share_cost = entry * cost_bps / BPS_DENOMINATOR
    quantity = risk_budget_usd / (stop_distance + per_share_cost)
    notional = entry * quantity
    fees = BASE_COSTS.fees(buy_notional=notional, sell_notional=target * quantity)
    slippage = notional * (cost_bps - BASE_COSTS.sell_fee_bps) / BPS_DENOMINATOR
    expected_gross = (target - entry) * quantity
    if expected_gross <= fees + slippage:
        return None, ("expected_edge_below_cost",)
    liquidity = min(Decimal("100"), snapshot.session_dollar_volume / Decimal("1000000"))
    components = SignalComponents(
        technical=setup.technical,
        regime=setup.regime,
        liquidity=liquidity,
        risk_reward=min(Decimal("100"), Decimal("40") * exit_params.reward_multiple),
        news=Decimal("50"),
        sentiment=Decimal("50"),
        historical_expectancy=Decimal("60"),
        data_freshness=Decimal("100"),
        agent_agreement=Decimal("70"),
        weights_version=weights_version,
    )
    evidence = (
        *setup.evidence,
        Evidence(
            source="market_data",
            category="liquidity",
            summary="Volumen de la sesión suficiente para el tamaño propuesto.",
            observed_at=snapshot.processed_time,
            strength=liquidity,
        ),
    )
    proposal = TradeProposal(
        proposal_id=str(uuid4()),
        asset=snapshot.symbol,
        side=Side.BUY,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        quantity=quantity,
        expected_r=exit_params.reward_multiple,
        time_horizon_seconds=exit_params.horizon_seconds,
        signal_score=compute_signal_score(components, weights),
        signal_components=components,
        confirmation_categories=frozenset({*setup.confirmations, "liquidity"}),
        evidence=evidence,
        contradictory_evidence=(),
        invalidations=(*setup.invalidations, "protective_stop_reached", "session_close"),
        expected_fees_usd=fees,
        estimated_slippage_usd=slippage,
        estimated_slippage_bps=BASE_COSTS.slippage_bps,
        observed_spread_bps=snapshot.spread_bps,
        observed_liquidity_usd=snapshot.session_dollar_volume,
        expected_net_value_usd=expected_gross - fees - slippage,
        why_now=setup.why_now,
        why_not_trade=(),
        is_a_plus=setup.is_a_plus,
        created_at=clock.now(),
    )
    return proposal, ()
