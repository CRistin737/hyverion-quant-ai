"""Deterministic market regime shared by live cycles and research replays.

The same function labels a bar in the strategy replay and gates a plugin in the
live cycle, so a regime filter behaves identically in both. It only reads past
closes (no lookahead) and never touches risk or execution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Literal

from trading_bot.data.features import FeatureSet
from trading_bot.schemas.common import MarketRegime
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal
from trading_bot.strategies.base import StrategyPlugin

Regime = Literal["trending_up", "trending_down", "ranging", "unknown"]
FILTERABLE_REGIMES: tuple[Regime, ...] = ("trending_up", "trending_down", "ranging")
REGIME_WINDOW = 60


def classify_regime(closes: Sequence[Decimal]) -> Regime:
    """Regime from past closes: drift beyond one random-walk deviation is a trend.

    Drift over the window is compared with the noise a random walk of the same
    per-bar volatility would show.
    """

    window = closes[-REGIME_WINDOW:]
    if len(window) < 10:
        return "unknown"
    returns = [(b / a - Decimal("1")) for a, b in pairwise(window)]
    mean = sum(returns, Decimal("0")) / Decimal(len(returns))
    variance = sum(((r - mean) ** 2 for r in returns), Decimal("0")) / Decimal(len(returns))
    noise = variance.sqrt() * Decimal(len(returns)).sqrt()
    drift = window[-1] / window[0] - Decimal("1")
    if noise == 0:
        return "ranging"
    score = drift / noise
    if score > 1:
        return "trending_up"
    if score < -1:
        return "trending_down"
    return "ranging"


_PRIMARY = {
    "trending_up": MarketRegime.TRENDING_UP,
    "trending_down": MarketRegime.TRENDING_DOWN,
    "ranging": MarketRegime.RANGING,
    "unknown": MarketRegime.UNCERTAIN,
}
HIGH_VOLATILITY_ATR_PERCENT = Decimal("0.08")  # per 1-minute bar
LOW_VOLATILITY_ATR_PERCENT = Decimal("0.02")


@dataclass(frozen=True, slots=True)
class RegimeState:
    """Primary regime plus secondary tags (§6); a market can carry several labels."""

    primary: MarketRegime
    secondary: tuple[MarketRegime, ...]
    confidence: Decimal

    def as_dict(self) -> dict[str, object]:
        return {
            "primary": self.primary.value,
            "secondary": [tag.value for tag in self.secondary],
            "confidence": str(self.confidence),
        }


def assess_regime(features: FeatureSet) -> RegimeState:
    """Deterministic regime from features only (no LLM, no lookahead)."""

    window = features.prices[-REGIME_WINDOW:]
    primary = _PRIMARY[classify_regime(window)]
    confidence = Decimal("0")
    if len(window) >= 10:
        returns = [(b / a - 1) for a, b in pairwise(window)]
        mean = sum(returns, Decimal("0")) / len(returns)
        variance = sum(((r - mean) ** 2 for r in returns), Decimal("0")) / len(returns)
        noise = variance.sqrt() * Decimal(len(returns)).sqrt()
        if noise > 0:
            confidence = min(Decimal("1"), abs(window[-1] / window[0] - 1) / noise / 2)
            if primary == MarketRegime.RANGING:
                confidence = 1 - confidence
    secondary: list[MarketRegime] = []
    if features.atr is not None and features.prices[-1] > 0:
        atr_percent = features.atr / features.prices[-1] * 100
        if atr_percent >= HIGH_VOLATILITY_ATR_PERCENT:
            secondary.append(MarketRegime.HIGH_VOLATILITY)
        elif atr_percent <= LOW_VOLATILITY_ATR_PERCENT:
            secondary.append(MarketRegime.LOW_VOLATILITY)
    if features.minutes_since_open is not None:
        if features.minutes_since_open < 30:
            secondary.append(MarketRegime.OPENING_DISCOVERY)
        elif features.minutes_since_open >= 360:  # last 30 minutes of a full session
            secondary.append(MarketRegime.CLOSING_FLOW)
    if (
        features.opening_range_high is not None
        and features.relative_volume is not None
        and features.prices[-1] > features.opening_range_high
        and features.relative_volume >= Decimal("1.5")
    ):
        secondary.append(MarketRegime.BREAKOUT_EXPANSION)
    return RegimeState(primary=primary, secondary=tuple(secondary), confidence=confidence)


class RegimeFilteredStrategy:
    """Wrap a plugin so it stays silent in the blocked regimes.

    The regime is read from the feature window the plugin already receives
    (the same 20 closes in the live cycle and in the replay). The filter can
    only remove proposals; it never creates or enlarges one.
    """

    def __init__(self, plugin: StrategyPlugin, blocked: Sequence[str]) -> None:
        self._plugin = plugin
        self._blocked = frozenset(blocked)
        self.strategy_id = plugin.strategy_id
        self.version = plugin.version
        self.last_no_trade: tuple[str, ...] = ()

    def set_intelligence(self, intelligence: object) -> None:
        setter = getattr(self._plugin, "set_intelligence", None)
        if callable(setter):
            setter(intelligence)

    def propose(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        *,
        risk_budget_usd: Decimal,
    ) -> TradeProposal | None:
        regime = classify_regime(features.prices)
        if regime in self._blocked:
            self.last_no_trade = (f"regime_blocked:{regime}",)
            return None
        proposal = self._plugin.propose(snapshot, features, risk_budget_usd=risk_budget_usd)
        self.last_no_trade = tuple(getattr(self._plugin, "last_no_trade", ()))
        return proposal
