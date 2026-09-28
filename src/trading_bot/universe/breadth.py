"""NasdaqBreadthEngine, QQQContributionEngine and BreadthDivergenceDetector (§17-§20, §120).

Pure arithmetic over component quotes and estimated weights; no LLM (§93).
Contributions are *estimates* (weights are estimated between N-PORT reports)
and are labelled as such.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from statistics import pstdev

from trading_bot.market_data.base import ComponentQuote
from trading_bot.schemas.common import StrictSchema

ZERO = Decimal("0")
BPS = Decimal("10000")
TOP_N = 10


class Contribution(StrictSchema):
    symbol: str
    weight: Decimal
    change: Decimal
    contribution_bps: Decimal
    above_vwap: bool | None


class BreadthSnapshot(StrictSchema):
    as_of: datetime
    components: int
    coverage_weight: Decimal  # share of index weight with a fresh quote (0-1)
    advancers: int
    decliners: int
    pct_green: Decimal
    weighted_breadth: Decimal  # weight share of advancers (0-1)
    top10_breadth: Decimal
    ex_top10_breadth: Decimal  # equal-weight breadth without the 10 largest (§120)
    pct_above_vwap: Decimal | None
    dispersion: Decimal  # cross-sectional std of returns
    estimated_index_change: Decimal  # sum of weight * change
    top10_contribution_share: Decimal  # share of |contribution| from the top 10
    leaders: tuple[Contribution, ...]
    laggards: tuple[Contribution, ...]
    megacaps: tuple[Contribution, ...]
    divergences: tuple[str, ...]
    estimated: bool = True


def _ratio(part: int | Decimal, whole: int | Decimal) -> Decimal:
    return Decimal(part) / Decimal(whole) if whole else ZERO


def compute_breadth(
    quotes: Sequence[ComponentQuote],
    weights: dict[str, Decimal],
    *,
    as_of: datetime,
    qqq_change: Decimal | None = None,
) -> BreadthSnapshot | None:
    rows: list[Contribution] = []
    for quote in quotes:
        weight = weights.get(quote.symbol)
        if weight is None or quote.previous_close <= 0:
            continue
        change = quote.change
        above = None if quote.session_vwap is None else quote.last >= quote.session_vwap
        rows.append(
            Contribution(
                symbol=quote.symbol,
                weight=weight,
                change=change,
                contribution_bps=weight * change * BPS,
                above_vwap=above,
            )
        )
    if not rows:
        return None
    rows.sort(key=lambda row: row.weight, reverse=True)
    top = rows[:TOP_N]
    rest = rows[TOP_N:]
    advancers = sum(1 for row in rows if row.change > 0)
    decliners = sum(1 for row in rows if row.change < 0)
    coverage = sum((row.weight for row in rows), ZERO)
    weighted_up = sum((row.weight for row in rows if row.change > 0), ZERO)
    top_weight = sum((row.weight for row in top), ZERO)
    vwap_rows = [row for row in rows if row.above_vwap is not None]
    abs_total = sum((abs(row.contribution_bps) for row in rows), ZERO)
    estimated_change = sum((row.weight * row.change for row in rows), ZERO) / (coverage or 1)
    by_contribution = sorted(rows, key=lambda row: row.contribution_bps, reverse=True)
    snapshot = BreadthSnapshot(
        as_of=as_of,
        components=len(rows),
        coverage_weight=coverage,
        advancers=advancers,
        decliners=decliners,
        pct_green=_ratio(advancers, len(rows)),
        weighted_breadth=_ratio(weighted_up, coverage),
        top10_breadth=_ratio(
            sum((row.weight for row in top if row.change > 0), ZERO), top_weight
        ),
        ex_top10_breadth=_ratio(sum(1 for row in rest if row.change > 0), len(rest)),
        pct_above_vwap=(
            _ratio(sum(1 for row in vwap_rows if row.above_vwap), len(vwap_rows))
            if vwap_rows
            else None
        ),
        dispersion=(
            Decimal(str(pstdev([float(row.change) for row in rows]))) if len(rows) > 1 else ZERO
        ),
        estimated_index_change=estimated_change,
        top10_contribution_share=_ratio(
            sum((abs(row.contribution_bps) for row in top), ZERO), abs_total
        ),
        leaders=tuple(by_contribution[:5]),
        laggards=tuple(reversed(by_contribution[-5:])),
        megacaps=tuple(top),
        divergences=(),
    )
    return snapshot.model_copy(
        update={"divergences": detect_divergences(snapshot, qqq_change=qqq_change)}
    )


def detect_divergences(
    snapshot: BreadthSnapshot, *, qqq_change: Decimal | None = None
) -> tuple[str, ...]:
    """Named patterns (§18, §118). A divergence informs the Critic; it never trades."""

    change = qqq_change if qqq_change is not None else snapshot.estimated_index_change
    found: list[str] = []
    if change > 0 and snapshot.pct_green < Decimal("0.45"):
        found.append("qqq_up_breadth_weak")
    if change > 0 and snapshot.ex_top10_breadth < Decimal("0.40"):
        found.append("megacap_only_rally")
    if change < 0 and snapshot.weighted_breadth > Decimal("0.55"):
        found.append("qqq_down_weighted_breadth_strong")
    if change < 0 and snapshot.top10_breadth > Decimal("0.60"):
        found.append("megacaps_holding_on_red_day")
    if snapshot.top10_contribution_share > Decimal("0.75"):
        found.append("concentrated_leadership")
    return tuple(found)
