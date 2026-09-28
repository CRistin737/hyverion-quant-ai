"""Deterministic strategy ensemble with explicit tie-breaking."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from trading_bot.data.features import FeatureSet
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal
from trading_bot.strategies.base import StrategyPlugin


class StrategyEnsemble:
    """Select the highest-scoring proposal without executing anything."""

    strategy_id = "ensemble"
    version = "1.0.0"

    def __init__(self, plugins: tuple[StrategyPlugin, ...]) -> None:
        if not plugins:
            raise ValueError("strategy ensemble requires at least one plugin")
        self._plugins = plugins
        self.last_no_trade: tuple[str, ...] = ()

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(plugin.strategy_id for plugin in self._plugins)

    def set_intelligence(self, intelligence: Any) -> None:
        """Hand the read-only intelligence envelope to plugins that use it."""

        for plugin in self._plugins:
            setter = getattr(plugin, "set_intelligence", None)
            if callable(setter):
                setter(intelligence)

    def propose(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        *,
        risk_budget_usd: Decimal,
    ) -> TradeProposal | None:
        proposals = [
            proposal
            for plugin in self._plugins
            if (proposal := plugin.propose(snapshot, features, risk_budget_usd=risk_budget_usd))
            is not None
        ]
        if not proposals:
            self.last_no_trade = tuple(
                f"{plugin.strategy_id}:{reason}"
                for plugin in self._plugins
                for reason in getattr(plugin, "last_no_trade", ()) or ("no_setup",)
            )
            return None
        self.last_no_trade = ()
        return max(
            enumerate(proposals), key=lambda item: (item[1].signal_score, -item[0])
        )[1]
