"""mean_reversion@1.0.0: buy a stretched dip back toward VWAP, only in a range.

Hypothesis (SPEC: spec/strategies/mean_reversion.md): in a RANGING session a
move far below VWAP (in ATR units) with an oversold RSI that starts to turn
tends to revert toward VWAP. Never used against a trend: outside RANGING the
answer is NO_TRADE.
"""

from __future__ import annotations

from decimal import Decimal

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import Evidence
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal
from trading_bot.strategies.base import ExitParams
from trading_bot.strategies.equity import Setup, build_proposal, session_gaps
from trading_bot.strategies.regime import classify_regime

MIN_STRETCH_ATR = Decimal("1.5")
MAX_RSI = Decimal("35")


class MeanReversionStrategy:
    strategy_id = "mean_reversion"
    version = "1.0.0"
    DEFAULT_EXIT = ExitParams(
        stop_percent=Decimal("0.35"), reward_multiple=Decimal("1.5"), horizon_seconds=2700
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
        if features.vwap_distance_atr is None or features.rsi is None:
            reasons.append("stretch_unavailable")
        if reasons:
            self.last_no_trade = tuple(reasons)
            return None
        assert features.vwap_distance_atr is not None and features.rsi is not None
        assert features.session_vwap is not None
        if classify_regime(features.prices) != "ranging":
            reasons.append("regime_not_ranging")
        if features.vwap_distance_atr > -MIN_STRETCH_ATR:
            reasons.append("not_stretched_below_vwap")
        if features.rsi > MAX_RSI:
            reasons.append("not_oversold")
        if features.prices[-1] <= features.prices[-2]:
            reasons.append("still_falling")
        entry = snapshot.ask
        target = entry * (1 + self._exit.stop_percent * self._exit.reward_multiple / 100)
        if features.session_vwap < target:
            reasons.append("not_enough_room_to_vwap")
        if reasons:
            self.last_no_trade = tuple(reasons)
            return None
        observed = snapshot.processed_time
        setup = Setup(
            technical=Decimal("75"),
            regime=Decimal("85"),
            confirmations=frozenset({"regime", "stretch", "momentum_turn"}),
            evidence=(
                Evidence(source=self.strategy_id, category="regime",
                         summary="Sesión lateral: la reversión a la media está habilitada.",
                         observed_at=observed, strength=Decimal("80")),
                Evidence(source=self.strategy_id, category="stretch",
                         summary="Precio muy por debajo del VWAP en unidades de ATR, RSI bajo.",
                         observed_at=observed, strength=Decimal("75")),
                Evidence(source=self.strategy_id, category="momentum_turn",
                         summary="La última barra cierra al alza: la caída se detiene.",
                         observed_at=observed, strength=Decimal("60")),
            ),
            invalidations=("regime_turns_trending", "new_low_below_stop"),
            why_now="Caída estirada bajo el VWAP en sesión lateral que empieza a girar.",
        )
        proposal, why_not = build_proposal(
            strategy_id=self.strategy_id, snapshot=snapshot, features=features, setup=setup,
            exit_params=self._exit, risk_budget_usd=risk_budget_usd, weights=self._weights,
            weights_version=self._weights_version, clock=self._clock,
        )
        self.last_no_trade = why_not
        return proposal
