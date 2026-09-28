"""hyverion_strategy@0.1.0: the owner's own strategy, built by trial and error.

Starting point (SPEC: spec/strategies/hyverion_strategy.md): well-known QQQ
session setups, each used only in the regime where it makes sense, and only
when the independent evidence agrees.

1. **Regime router** (§6): TRENDING_UP → trend pullback, then an opening-range
   breakout in the first two hours; RANGING → VWAP mean reversion; anything
   else (trending down, event-driven, uncertain) → NO_TRADE.
2. **Confluence filter** (§38): the evidence graph (price structure, breadth,
   mega-caps, volatility, macro, rates, options, news) must lean long
   (``score >= MIN_CONFLUENCE``) and the data must be good enough.
3. The first valid setup wins; sizing, stop and costs are the setup's own
   (``strategies/equity.py``), so the RiskEngine sees the usual proposal.

Replays have no stored intelligence for past days, so there the filter uses
only what bars provide (price structure, volume). That is stated in every
replay report, never hidden. Each change becomes ``0.2.0``, ``0.3.0``… and goes
historical → out-of-sample → walk-forward → paper → shadow before the owner
approves it. It starts **disabled**.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.intelligence.evidence import build_evidence, confluence
from trading_bot.schemas.common import Evidence, MarketRegime
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal
from trading_bot.strategies.base import ExitParams
from trading_bot.strategies.mean_reversion import MeanReversionStrategy
from trading_bot.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from trading_bot.strategies.regime import assess_regime
from trading_bot.strategies.trend_pullback import TrendPullbackStrategy

MIN_CONFLUENCE = Decimal("55")
ORB_LAST_MINUTE = 120  # breakouts after the first two hours fail more often (to be validated)


class HyverionStrategy:
    strategy_id = "hyverion_strategy"
    version = "0.1.0"
    DEFAULT_EXIT = TrendPullbackStrategy.DEFAULT_EXIT

    def __init__(
        self,
        weights: dict[str, Decimal],
        weights_version: str,
        clock: Clock,
        *,
        exit_params: ExitParams | None = None,
    ) -> None:
        self._clock = clock
        self._pullback = TrendPullbackStrategy(
            weights, weights_version, clock, exit_params=exit_params
        )
        self._breakout = OpeningRangeBreakoutStrategy(
            weights, weights_version, clock, exit_params=exit_params
        )
        self._reversion = MeanReversionStrategy(
            weights, weights_version, clock, exit_params=exit_params
        )
        self._intelligence: Mapping[str, Any] | None = None
        self.last_no_trade: tuple[str, ...] = ()

    def set_intelligence(self, intelligence: Mapping[str, Any] | None) -> None:
        """Latest IntelligenceHub envelope (read-only). None in replays."""

        self._intelligence = intelligence

    def propose(
        self, snapshot: MarketSnapshot, features: FeatureSet, *, risk_budget_usd: Decimal
    ) -> TradeProposal | None:
        regime = assess_regime(features)
        candidates: list[
            TrendPullbackStrategy | OpeningRangeBreakoutStrategy | MeanReversionStrategy
        ] = []
        if regime.primary == MarketRegime.TRENDING_UP:
            candidates.append(self._pullback)
            minutes = features.minutes_since_open
            if minutes is not None and minutes <= ORB_LAST_MINUTE:
                candidates.append(self._breakout)
        elif regime.primary == MarketRegime.RANGING:
            candidates.append(self._reversion)
        if not candidates:
            self.last_no_trade = (f"regime_not_tradable:{regime.primary.value}",)
            return None
        graph = build_evidence(features, snapshot.last, self._intelligence, now=self._clock.now())
        score = confluence(graph)
        if score.score < MIN_CONFLUENCE:
            self.last_no_trade = (f"low_confluence:{score.score}",)
            return None
        reasons: list[str] = []
        for plugin in candidates:
            proposal = plugin.propose(snapshot, features, risk_budget_usd=risk_budget_usd)
            if proposal is None:
                reasons.extend(f"{plugin.strategy_id}:{code}" for code in plugin.last_no_trade)
                continue
            note = Evidence(
                source=self.strategy_id,
                category="confluence",
                summary=(
                    f"Hyverion Strategy {self.version}: régimen {regime.primary.value}, "
                    f"setup {plugin.strategy_id}, confluencia {score.score}/100 "
                    f"({score.weights_version})."
                ),
                observed_at=snapshot.processed_time,
                strength=min(Decimal("100"), score.score),
            )
            self.last_no_trade = ()
            return proposal.model_copy(
                update={
                    "evidence": (*proposal.evidence, note),
                    "why_now": f"[{self.strategy_id}] {proposal.why_now}",
                }
            )
        self.last_no_trade = tuple(reasons) or ("no_setup",)
        return None
