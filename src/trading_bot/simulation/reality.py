"""PaperRealityAdjustment (§51, §139): how far paper fills are from the cost model.

Paper fills are generous: no market impact, no queue. For every filled entry
the report compares the fill price with the proposal's entry price and the
slippage the cost model expected (half spread + slippage, BASE scenario). The
broker's paper PnL and the *adjusted* PnL (paper PnL minus the extra cost a
live fill would have paid at the modelled slippage when paper was kinder) are
kept apart and never mixed.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from statistics import median
from typing import Any

from trading_bot.schemas.common import StrictSchema
from trading_bot.simulation.costs import BASE_COSTS, BPS_DENOMINATOR

ENTRY_PREFIX = "hyverion-"


class FillComparison(StrictSchema):
    proposal_id: str
    side: str
    expected_price: Decimal
    fill_price: Decimal
    quantity: Decimal
    realized_slippage_bps: Decimal  # positive = paid more than expected
    modelled_slippage_bps: Decimal


class PaperRealityReport(StrictSchema):
    fills_compared: int
    realized_slippage_bps_mean: Decimal | None
    realized_slippage_bps_median: Decimal | None
    modelled_slippage_bps_mean: Decimal | None
    broker_paper_pnl_usd: Decimal
    adjusted_simulated_pnl_usd: Decimal
    paper_kinder_than_model: bool | None
    comparisons: tuple[FillComparison, ...]
    note: str


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def paper_reality(
    order_rows: Sequence[dict[str, Any]],
    proposal_rows: Sequence[dict[str, Any]],
    *,
    realized_pnl_usd: Decimal,
) -> PaperRealityReport:
    proposals = {
        str(_payload(row).get("proposal_id"))[:20]: _payload(row) for row in proposal_rows
    }
    comparisons: list[FillComparison] = []
    seen: set[str] = set()
    extra_cost = Decimal("0")
    for row in order_rows:
        order = _payload(row)
        client_id = str(order.get("client_order_id") or "")
        fills = order.get("fills") or []
        if (
            not client_id.startswith(ENTRY_PREFIX)
            or client_id.startswith(f"{ENTRY_PREFIX}exit-")
            or not fills
            or client_id in seen
        ):
            continue
        proposal = proposals.get(client_id.removeprefix(ENTRY_PREFIX))
        if proposal is None:
            continue
        seen.add(client_id)
        quantity = sum((Decimal(str(fill["quantity"])) for fill in fills), Decimal("0"))
        if quantity <= 0:
            continue
        price = sum(
            (Decimal(str(fill["price"])) * Decimal(str(fill["quantity"])) for fill in fills),
            Decimal("0"),
        ) / quantity
        expected = Decimal(str(proposal["entry_price"]))
        direction = Decimal("1") if str(order.get("side")) == "buy" else Decimal("-1")
        realized_bps = (price - expected) / expected * BPS_DENOMINATOR * direction
        spread = Decimal(str(proposal.get("observed_spread_bps") or "0"))
        modelled = max(spread / 2, BASE_COSTS.min_half_spread_bps) + BASE_COSTS.slippage_bps
        comparisons.append(
            FillComparison(
                proposal_id=str(proposal.get("proposal_id")),
                side=str(order.get("side")),
                expected_price=expected,
                fill_price=price,
                quantity=quantity,
                realized_slippage_bps=realized_bps.quantize(Decimal("0.01")),
                modelled_slippage_bps=modelled,
            )
        )
        if realized_bps < modelled:
            # Paper filled better than a live order would plausibly fill.
            extra_cost += (modelled - realized_bps) / BPS_DENOMINATOR * price * quantity
    realized = [item.realized_slippage_bps for item in comparisons]
    expected_bps = [item.modelled_slippage_bps for item in comparisons]
    mean_realized = sum(realized, Decimal("0")) / len(realized) if realized else None
    mean_modelled = (
        sum(expected_bps, Decimal("0")) / len(expected_bps) if expected_bps else None
    )
    return PaperRealityReport(
        fills_compared=len(comparisons),
        realized_slippage_bps_mean=(
            mean_realized.quantize(Decimal("0.01")) if mean_realized is not None else None
        ),
        realized_slippage_bps_median=Decimal(str(median(realized))) if realized else None,
        modelled_slippage_bps_mean=mean_modelled,
        broker_paper_pnl_usd=realized_pnl_usd,
        adjusted_simulated_pnl_usd=(realized_pnl_usd - extra_cost).quantize(Decimal("0.01")),
        paper_kinder_than_model=(
            mean_realized < mean_modelled
            if mean_realized is not None and mean_modelled is not None
            else None
        ),
        comparisons=tuple(comparisons[-50:]),
        note=(
            "El PnL del broker paper y el ajustado se muestran por separado. El paper no "
            "modela impacto ni cola; el ajustado resta el coste extra que el modelo espera "
            "cuando el paper llenó mejor."
        ),
    )
