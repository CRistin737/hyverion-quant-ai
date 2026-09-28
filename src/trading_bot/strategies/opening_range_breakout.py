"""opening_range_breakout@1.0.0: buy a fresh break of the 30-minute opening range.

Hypothesis (SPEC: spec/strategies/opening_range_breakout.md): a first close
above the opening-range high, on above-average volume and above VWAP, tends to
follow through. Without volume confirmation, or once the move is already
extended, the answer is NO_TRADE.
"""

from __future__ import annotations

from decimal import Decimal

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import Evidence
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal
from trading_bot.strategies.base import ExitParams
from trading_bot.strategies.equity import Setup, build_proposal, session_gaps

MIN_RELATIVE_VOLUME = Decimal("1.5")
MAX_BREAK_EXTENSION_ATR = Decimal("0.5")
LATEST_ENTRY_MINUTES = 180  # breakouts after 12:30 ET are a different animal


class OpeningRangeBreakoutStrategy:
    strategy_id = "opening_range_breakout"
    version = "1.0.0"
    DEFAULT_EXIT = ExitParams(
        stop_percent=Decimal("0.35"), reward_multiple=Decimal("2"), horizon_seconds=5400
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
        high = features.opening_range_high
        if high is None:
            reasons.append("opening_range_not_formed")
        if reasons:
            self.last_no_trade = tuple(reasons)
            return None
        assert high is not None and features.atr is not None and features.session_vwap is not None
        last, previous = features.prices[-1], features.prices[-2]
        if features.minutes_since_open is not None and (
            features.minutes_since_open > LATEST_ENTRY_MINUTES
        ):
            reasons.append("too_late_for_opening_breakout")
        if not previous <= high < last:
            reasons.append("no_fresh_breakout")
        if (last - high) / features.atr > MAX_BREAK_EXTENSION_ATR:
            reasons.append("breakout_extended")
        if features.relative_volume is None or features.relative_volume < MIN_RELATIVE_VOLUME:
            reasons.append("breakout_without_volume")
        if last <= features.session_vwap:
            reasons.append("price_below_vwap")
        if reasons:
            self.last_no_trade = tuple(reasons)
            return None
        observed = snapshot.processed_time
        setup = Setup(
            technical=Decimal("80"),
            regime=Decimal("80"),
            confirmations=frozenset({"breakout", "volume", "vwap"}),
            evidence=(
                Evidence(source=self.strategy_id, category="breakout",
                         summary="Primer cierre sobre el máximo del rango de apertura (30 min).",
                         observed_at=observed, strength=Decimal("80")),
                Evidence(source=self.strategy_id, category="volume",
                         summary="Volumen de la barra por encima de la media de la sesión.",
                         observed_at=observed,
                         strength=min(Decimal("100"), features.relative_volume * 40
                                      if features.relative_volume else Decimal("0"))),
                Evidence(source=self.strategy_id, category="vwap",
                         summary="La ruptura ocurre sobre el VWAP de la sesión.",
                         observed_at=observed, strength=Decimal("70")),
            ),
            invalidations=("close_back_inside_opening_range",),
            why_now="Ruptura del rango de apertura confirmada por volumen y VWAP.",
        )
        proposal, why_not = build_proposal(
            strategy_id=self.strategy_id, snapshot=snapshot, features=features, setup=setup,
            exit_params=self._exit, risk_budget_usd=risk_budget_usd, weights=self._weights,
            weights_version=self._weights_version, clock=self._clock,
        )
        self.last_no_trade = why_not
        return proposal
