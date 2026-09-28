"""trend_pullback@1.0.0: buy a confirmed pullback inside an intraday uptrend.

Hypothesis (SPEC: spec/strategies/trend_pullback.md): when QQQ trades above its
session VWAP with the fast EMA above the slow EMA, a shallow pullback to the
fast EMA/VWAP that resumes upward tends to continue. Chasing an extended move
(far above VWAP in ATR units) is refused.
"""

from __future__ import annotations

from decimal import Decimal

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import Evidence
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal
from trading_bot.strategies.base import ExitParams
from trading_bot.strategies.equity import Setup, build_proposal, session_gaps

MAX_EXTENSION_ATR = Decimal("1.5")
PULLBACK_TOLERANCE_ATR = Decimal("0.25")


class TrendPullbackStrategy:
    strategy_id = "trend_pullback"
    version = "1.0.0"
    DEFAULT_EXIT = ExitParams(
        stop_percent=Decimal("0.4"), reward_multiple=Decimal("2"), horizon_seconds=3600
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
        self.last_no_trade: tuple[str, ...] = ()

    def propose(
        self, snapshot: MarketSnapshot, features: FeatureSet, *, risk_budget_usd: Decimal
    ) -> TradeProposal | None:
        reasons = session_gaps(features)
        if features.ema_fast is None or features.ema_slow is None:
            reasons.append("ema_unavailable")
        if reasons:
            self.last_no_trade = tuple(reasons)
            return None
        vwap, atr = features.session_vwap, features.atr
        fast, slow = features.ema_fast, features.ema_slow
        assert vwap is not None and atr is not None and fast is not None and slow is not None
        last, previous = features.prices[-1], features.prices[-2]
        if last <= vwap:
            reasons.append("price_below_vwap")
        if fast <= slow:
            reasons.append("no_intraday_uptrend")
        if (last - vwap) / atr > MAX_EXTENSION_ATR:
            reasons.append("extended_from_vwap")
        recent_low = min(features.prices[-6:-1])
        support = max(fast, vwap)
        if recent_low > support + atr * PULLBACK_TOLERANCE_ATR:
            reasons.append("no_pullback")
        if last <= previous:
            reasons.append("pullback_not_resuming")
        if features.rsi is not None and features.rsi >= 70:
            reasons.append("overbought")
        if reasons:
            self.last_no_trade = tuple(reasons)
            return None
        observed = snapshot.processed_time
        setup = Setup(
            technical=min(
                Decimal("100"), (features.momentum_score + features.trend_score) / 2 + 20
            ),
            regime=Decimal("85"),
            confirmations=frozenset({"trend", "vwap", "pullback"}),
            evidence=(
                Evidence(source=self.strategy_id, category="trend",
                         summary="EMA rápida sobre la lenta: tendencia intradía alcista.",
                         observed_at=observed, strength=Decimal("80")),
                Evidence(source=self.strategy_id, category="vwap",
                         summary="QQQ cotiza sobre el VWAP de la sesión sin estar extendido.",
                         observed_at=observed, strength=Decimal("75")),
                Evidence(source=self.strategy_id, category="pullback",
                         summary="Retroceso a la EMA/VWAP que ya vuelve a subir.",
                         observed_at=observed, strength=Decimal("75")),
            ),
            invalidations=("close_below_vwap", "fast_ema_below_slow_ema"),
            why_now="Retroceso confirmado dentro de una tendencia alcista sobre el VWAP.",
        )
        proposal, why_not = build_proposal(
            strategy_id=self.strategy_id, snapshot=snapshot, features=features, setup=setup,
            exit_params=self._exit, risk_budget_usd=risk_budget_usd, weights=self._weights,
            weights_version=self._weights_version, clock=self._clock,
        )
        self.last_no_trade = why_not
        return proposal
