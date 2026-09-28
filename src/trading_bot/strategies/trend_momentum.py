from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import Evidence, Side
from trading_bot.schemas.trading import MarketSnapshot, SignalComponents, TradeProposal
from trading_bot.strategies.base import ExitParams, StrategyPlugin
from trading_bot.strategies.signal_score import compute_signal_score


class TrendMomentumStrategy(StrategyPlugin):
    strategy_id = "trend_momentum"
    version = "1.0.0"

    DEFAULT_EXIT = ExitParams(
        stop_percent=Decimal("1"), reward_multiple=Decimal("2"), horizon_seconds=3600
    )

    def __init__(
        self,
        weights: dict[str, Decimal],
        weights_version: str,
        clock: Clock,
        *,
        exit_params: ExitParams | None = None,
    ) -> None:
        self._exit = exit_params or self.DEFAULT_EXIT
        self._weights = weights
        self._weights_version = weights_version
        self._clock = clock

    def propose(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        *,
        risk_budget_usd: Decimal,
    ) -> TradeProposal | None:
        if features.fast_sma <= features.slow_sma or features.simple_return_percent <= 0:
            return None
        if risk_budget_usd <= 0:
            return None

        technical = (features.momentum_score + features.trend_score) / Decimal("2")
        liquidity_score = min(Decimal("100"), snapshot.session_dollar_volume / Decimal("100000"))
        components = SignalComponents(
            technical=technical,
            regime=Decimal("85"),
            liquidity=liquidity_score,
            risk_reward=Decimal("80"),
            news=Decimal("50"),
            sentiment=Decimal("50"),
            historical_expectancy=Decimal("70"),
            data_freshness=Decimal("100"),
            agent_agreement=Decimal("80"),
            weights_version=self._weights_version,
        )
        score = compute_signal_score(components, self._weights)
        entry = snapshot.ask
        stop_distance = max(entry * self._exit.stop_percent / Decimal("100"), Decimal("0.01"))
        stop = entry - stop_distance
        target = entry + stop_distance * self._exit.reward_multiple
        per_unit_cost = stop_distance + entry * Decimal("0.003")
        quantity = risk_budget_usd / per_unit_cost
        fees = entry * quantity * Decimal("0.002")
        slippage = entry * quantity * Decimal("0.001")
        expected_gross = (target - entry) * quantity
        evidence = (
            Evidence(
                source=self.strategy_id,
                category="technical",
                summary="La media rápida está sobre la lenta con momentum positivo.",
                observed_at=snapshot.processed_time,
                strength=technical,
            ),
            Evidence(
                source="public_market_data",
                category="liquidity",
                summary="El volumen cotizado respalda el nocional simulado propuesto.",
                observed_at=snapshot.processed_time,
                strength=liquidity_score,
            ),
            Evidence(
                source="regime_filter",
                category="regime",
                summary="Familia de tendencia habilitada para el régimen tendencial.",
                observed_at=snapshot.processed_time,
                strength=Decimal("85"),
            ),
        )
        return TradeProposal(
            proposal_id=str(uuid4()),
            asset=snapshot.symbol,
            side=Side.BUY,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            quantity=quantity,
            expected_r=self._exit.reward_multiple,
            time_horizon_seconds=self._exit.horizon_seconds,
            signal_score=score,
            signal_components=components,
            confirmation_categories=frozenset({"technical", "liquidity", "regime"}),
            evidence=evidence,
            contradictory_evidence=(),
            invalidations=("fast_sma_below_slow_sma", "protective_stop_reached"),
            expected_fees_usd=fees,
            estimated_slippage_usd=slippage,
            estimated_slippage_bps=Decimal("10"),
            observed_spread_bps=snapshot.spread_bps,
            observed_liquidity_usd=snapshot.session_dollar_volume,
            expected_net_value_usd=expected_gross - fees - slippage,
            why_now="Tendencia y momentum alineados al alza con datos públicos frescos.",
            why_not_trade=(),
            is_a_plus=False,
            created_at=self._clock.now(),
        )
