"""Deterministic summaries for evaluated real and shadow trades."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_bot.schemas.learning import LearningMetrics


def summarize_evaluations(
    evaluations: Iterable[Mapping[str, Any]],
) -> LearningMetrics:
    """Calculate bounded learning evidence without mutating active strategy state.

    Rows that cannot be parsed as an evaluation are counted as invalid rather
    than coerced to zero. This keeps the Learning workspace honest while
    allowing a malformed historical row to remain visible in the audit log.
    """

    valid: list[dict[str, Any]] = []
    invalid = 0
    for raw in evaluations:
        try:
            pnl = _decimal(raw.get("realized_net_pnl"))
            mfe = _decimal(raw.get("mfe_usd"))
            mae = _decimal(raw.get("mae_usd"))
            fees = _non_negative(raw.get("fees_usd"))
            slippage = _non_negative(raw.get("slippage_usd"))
            signal_correct = raw.get("signal_correct")
            if not isinstance(signal_correct, bool):
                raise ValueError("signal_correct must be boolean")
            raw_agents = raw.get("agent_incremental_values", {})
            if not isinstance(raw_agents, Mapping):
                raise ValueError("agent_incremental_values must be a mapping")
            agents = {
                str(agent): _decimal(value)
                for agent, value in raw_agents.items()
                if str(agent).strip()
            }
        except (InvalidOperation, TypeError, ValueError):
            invalid += 1
            continue
        valid.append(
            {
                "pnl": pnl,
                "mfe": mfe,
                "mae": mae,
                "fees": fees,
                "slippage": slippage,
                "signal_correct": signal_correct,
                "agents": agents,
            }
        )

    sample_size = len(valid)
    if sample_size == 0:
        return LearningMetrics(
            sample_size=0,
            invalid_records=invalid,
            wins=0,
            losses=0,
            win_rate_percent=Decimal("0"),
            expectancy_usd=Decimal("0"),
            profit_factor=None,
            max_drawdown_usd=Decimal("0"),
            average_win_usd=Decimal("0"),
            average_loss_usd=Decimal("0"),
            average_mfe_usd=Decimal("0"),
            average_mae_usd=Decimal("0"),
            fees_usd=Decimal("0"),
            slippage_usd=Decimal("0"),
            cost_drag_percent=None,
            agent_incremental_value_usd={},
        )

    pnls = [row["pnl"] for row in valid]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    cumulative = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for pnl in pnls:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    fees = sum((row["fees"] for row in valid), Decimal("0"))
    slippage = sum((row["slippage"] for row in valid), Decimal("0"))
    gross_wins = sum(wins, Decimal("0"))
    gross_losses = abs(sum(losses, Decimal("0")))
    cost_drag = (
        (fees + slippage) / gross_wins * Decimal("100") if gross_wins > 0 else None
    )
    agent_totals: dict[str, Decimal] = {}
    for row in valid:
        for agent, value in row["agents"].items():
            agent_totals[agent] = agent_totals.get(agent, Decimal("0")) + value

    return LearningMetrics(
        sample_size=sample_size,
        invalid_records=invalid,
        wins=len(wins),
        losses=len(losses),
        win_rate_percent=(Decimal(len(wins)) / Decimal(sample_size) * Decimal("100")),
        expectancy_usd=sum(pnls, Decimal("0")) / Decimal(sample_size),
        profit_factor=(gross_wins / gross_losses if gross_losses > 0 else None),
        max_drawdown_usd=max_drawdown,
        average_win_usd=(gross_wins / Decimal(len(wins)) if wins else Decimal("0")),
        average_loss_usd=(gross_losses / Decimal(len(losses)) if losses else Decimal("0")),
        average_mfe_usd=sum((row["mfe"] for row in valid), Decimal("0")) / Decimal(sample_size),
        average_mae_usd=sum((row["mae"] for row in valid), Decimal("0")) / Decimal(sample_size),
        fees_usd=fees,
        slippage_usd=slippage,
        cost_drag_percent=cost_drag,
        agent_incremental_value_usd=agent_totals,
    )


def _decimal(value: Any) -> Decimal:
    if value is None:
        raise ValueError("numeric value is missing")
    return Decimal(str(value))


def _non_negative(value: Any) -> Decimal:
    parsed = _decimal(value)
    if parsed < 0:
        raise ValueError("cost value cannot be negative")
    return parsed
