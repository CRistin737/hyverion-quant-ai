"""Build deterministic strategy plugins from validated configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import cast

from trading_bot.config.models import StrategiesConfig
from trading_bot.core.clock import Clock
from trading_bot.strategies.base import ExitParams, PluginOverrides, StrategyPlugin
from trading_bot.strategies.ensemble import StrategyEnsemble
from trading_bot.strategies.hyverion_strategy import HyverionStrategy
from trading_bot.strategies.mean_reversion import MeanReversionStrategy
from trading_bot.strategies.opening_range_breakout import OpeningRangeBreakoutStrategy
from trading_bot.strategies.regime import RegimeFilteredStrategy
from trading_bot.strategies.trend_momentum import TrendMomentumStrategy
from trading_bot.strategies.trend_pullback import TrendPullbackStrategy

_CONSTRUCTORS: dict[str, Callable[[dict[str, Decimal], str, Clock], StrategyPlugin]] = {
    "trend_pullback": TrendPullbackStrategy,
    "opening_range_breakout": OpeningRangeBreakoutStrategy,
    "mean_reversion": MeanReversionStrategy,
    "trend_momentum": TrendMomentumStrategy,
    "hyverion_strategy": HyverionStrategy,
}


def exit_defaults(strategy_id: str) -> ExitParams:
    constructor = _CONSTRUCTORS[strategy_id]
    return cast(ExitParams, constructor.DEFAULT_EXIT)  # type: ignore[attr-defined]


def strategy_version(strategy_id: str) -> str:
    return str(_CONSTRUCTORS[strategy_id].version)  # type: ignore[attr-defined]


def strategy_factory(
    config: StrategiesConfig, strategy_id: str, exit_params: ExitParams | None = None
) -> Callable[[Clock], StrategyPlugin]:
    """Constructor for one plugin, optionally with challenger exit parameters."""

    if strategy_id not in _CONSTRUCTORS:
        raise ValueError(f"unknown strategy: {strategy_id}")
    constructor = _CONSTRUCTORS[strategy_id]
    return lambda clock: constructor(  # type: ignore[call-arg]
        config.signal_weights,
        config.signal_weights_version,
        clock,
        exit_params=exit_params,
    )


def strategy_factories(
    config: StrategiesConfig,
    overrides: Mapping[str, PluginOverrides] | None = None,
) -> dict[str, Callable[[Clock], StrategyPlugin]]:
    """One clock-injected constructor per enabled plugin, with approved overrides."""

    return {
        strategy_id: plugin_factory(config, strategy_id, (overrides or {}).get(strategy_id))
        for strategy_id in config.enabled
    }


def plugin_factory(
    config: StrategiesConfig, strategy_id: str, override: PluginOverrides | None
) -> Callable[[Clock], StrategyPlugin]:
    """Constructor for one plugin with its approved exits and regime filter."""

    constructor = _CONSTRUCTORS[strategy_id]

    def build(clock: Clock) -> StrategyPlugin:
        plugin = constructor(  # type: ignore[call-arg]
            config.signal_weights,
            config.signal_weights_version,
            clock,
            exit_params=override.exit_params if override else None,
        )
        if override and override.blocked_regimes:
            return RegimeFilteredStrategy(plugin, override.blocked_regimes)
        return plugin

    return build


def build_strategy_ensemble(
    config: StrategiesConfig,
    *,
    clock: Clock,
    overrides: Mapping[str, PluginOverrides] | None = None,
) -> StrategyPlugin:
    """Return one deterministic gateway for the configured plugin set."""

    plugins = tuple(
        factory(clock) for factory in strategy_factories(config, overrides).values()
    )
    if len(plugins) == 1:
        return plugins[0]
    return StrategyEnsemble(plugins)
