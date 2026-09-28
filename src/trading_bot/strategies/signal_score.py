from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from trading_bot.schemas.trading import SignalComponents

COMPONENT_NAMES = {
    "technical",
    "regime",
    "liquidity",
    "risk_reward",
    "news",
    "sentiment",
    "historical_expectancy",
    "data_freshness",
    "agent_agreement",
}


def compute_signal_score(components: SignalComponents, weights: dict[str, Decimal]) -> Decimal:
    if set(weights) != COMPONENT_NAMES:
        missing = COMPONENT_NAMES - set(weights)
        extra = set(weights) - COMPONENT_NAMES
        raise ValueError(
            f"invalid signal weights; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if sum(weights.values(), Decimal("0")) != Decimal("1"):
        raise ValueError("signal weights must sum exactly to 1")
    values = components.model_dump(exclude={"weights_version"})
    total = sum(
        (Decimal(str(values[name])) * weights[name] for name in COMPONENT_NAMES),
        Decimal("0"),
    )
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
