"""Trade-sequence Monte Carlo and bootstrap confidence intervals (§102, §104).

The order of trades is luck. Reshuffling (with replacement) the out-of-sample
trades shows the range of drawdowns and losing streaks the same edge could
have produced. It is a stress view, never a promise: no guarantee is inferred
from it.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from decimal import Decimal

from trading_bot.schemas.common import StrictSchema

RUNS = 2000
SEED = 20260928  # fixed: the same trades always give the same report


class MonteCarloReport(StrictSchema):
    runs: int
    trades: int
    final_pnl_p5: Decimal
    final_pnl_p50: Decimal
    final_pnl_p95: Decimal
    max_drawdown_p50: Decimal
    max_drawdown_p95: Decimal
    losing_streak_p95: int
    probability_loss: Decimal
    expectancy_ci_low: Decimal  # 90 % bootstrap interval of the mean trade
    expectancy_ci_high: Decimal


def _percentile(values: Sequence[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def _dec(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def monte_carlo(
    pnls: Sequence[Decimal], *, runs: int = RUNS, seed: int = SEED
) -> MonteCarloReport | None:
    if len(pnls) < 5:
        return None  # too few trades to say anything
    values = [float(pnl) for pnl in pnls]
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    finals: list[float] = []
    drawdowns: list[float] = []
    streaks: list[float] = []
    means: list[float] = []
    for _ in range(runs):
        sample = [rng.choice(values) for _ in values]
        equity = peak = drawdown = 0.0
        streak = worst = 0
        for pnl in sample:
            equity += pnl
            peak = max(peak, equity)
            drawdown = max(drawdown, peak - equity)
            streak = streak + 1 if pnl < 0 else 0
            worst = max(worst, streak)
        finals.append(equity)
        drawdowns.append(drawdown)
        streaks.append(worst)
        means.append(equity / len(sample))
    return MonteCarloReport(
        runs=runs,
        trades=len(values),
        final_pnl_p5=_dec(_percentile(finals, 0.05)),
        final_pnl_p50=_dec(_percentile(finals, 0.50)),
        final_pnl_p95=_dec(_percentile(finals, 0.95)),
        max_drawdown_p50=_dec(_percentile(drawdowns, 0.50)),
        max_drawdown_p95=_dec(_percentile(drawdowns, 0.95)),
        losing_streak_p95=int(_percentile(streaks, 0.95)),
        probability_loss=_dec(sum(1 for value in finals if value < 0) / runs),
        expectancy_ci_low=_dec(_percentile(means, 0.05)),
        expectancy_ci_high=_dec(_percentile(means, 0.95)),
    )
