"""Contracts shared by deterministic strategy plugins."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from trading_bot.data.features import FeatureSet
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal

SUPPORTED_STRATEGY_IDS = frozenset(
    {
        # QQQ session strategies (VWAP/ATR/opening range, regular session, long-only).
        "trend_pullback",
        "opening_range_breakout",
        "mean_reversion",
        # Price-only baseline kept as a benchmark for the session strategies.
        "trend_momentum",
        # The owner's own strategy, grown by trial and error (starts disabled).
        "hyverion_strategy",
    }
)


@dataclass(frozen=True, slots=True)
class ExitParams:
    """Protective exit geometry of a plugin: stop distance, reward multiple, horizon.

    Production uses each plugin's DEFAULT_EXIT. Research replays may pass a
    challenger set; nothing here changes production configuration.
    """

    stop_percent: Decimal
    reward_multiple: Decimal
    horizon_seconds: int

    def __post_init__(self) -> None:
        if not Decimal("0.05") <= self.stop_percent <= Decimal("10"):
            raise ValueError("stop_percent must be within 0.05..10")
        if not Decimal("0.5") <= self.reward_multiple <= Decimal("10"):
            raise ValueError("reward_multiple must be within 0.5..10")
        if not 60 <= self.horizon_seconds <= 86_400:
            raise ValueError("horizon_seconds must be within 60..86400")


@dataclass(frozen=True, slots=True)
class PluginOverrides:
    """Human-approved settings of one plugin: exit geometry and silent regimes."""

    exit_params: ExitParams | None = None
    blocked_regimes: tuple[str, ...] = ()


class StrategyPlugin(Protocol):
    """Deterministic strategy boundary; it can propose but never execute."""

    strategy_id: str
    version: str

    def propose(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        *,
        risk_budget_usd: Decimal,
    ) -> TradeProposal | None:
        ...
