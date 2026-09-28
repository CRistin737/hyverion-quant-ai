from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_bot.data.features import FeatureSet
from trading_bot.schemas.trading import MarketSnapshot


@dataclass(frozen=True, slots=True)
class DetectionResult:
    interesting: bool
    triggers: tuple[str, ...]


class EventDetector:
    """Keeps normal ticks away from paid AI providers."""

    def __init__(
        self,
        *,
        volatility_trigger_percent: Decimal = Decimal("1"),
        displacement_trigger_percent: Decimal = Decimal("1"),
        max_spread_bps: Decimal = Decimal("20"),
    ) -> None:
        self._volatility_trigger = volatility_trigger_percent
        self._displacement_trigger = displacement_trigger_percent
        self._max_spread = max_spread_bps

    def detect(self, snapshot: MarketSnapshot, features: FeatureSet) -> DetectionResult:
        triggers: list[str] = []
        if features.realized_volatility_percent >= self._volatility_trigger:
            triggers.append("volatility_breakout")
        if abs(features.simple_return_percent) >= self._displacement_trigger:
            triggers.append("price_displacement")
        if snapshot.spread_bps >= self._max_spread:
            triggers.append("liquidity_deterioration")
        return DetectionResult(interesting=bool(triggers), triggers=tuple(triggers))
