"""Read-only projections that join operations, positions and orders for the UI.

The desktop app shows one row per trade: the operation lifecycle (who approved
it and where it is), the latest position projection (prices, stop, result) and
its fills. These functions only reshape rows that were already persisted; they
never decide anything and never touch an exchange.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from trading_bot.broker.lifecycle import UNRESOLVED_STATES

TradeView = Literal["open", "closed", "attention"]

_UNRESOLVED = {state.value for state in UNRESOLVED_STATES}
_OPEN_POSITION = {"OPEN", "PARTIALLY_FILLED"}
_PENDING_OPERATION = {
    "PROPOSED",
    "CRITIC_REVIEWED",
    "RISK_APPROVED",
    "EXECUTION_PENDING",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "PARTIALLY_FILLED",
    "OPEN",
    "EXIT_PENDING",
    "CLOSING",
    "CANCEL_PENDING",
}
# Operations that never became a trade are shown with orders, not as trades.
_NEVER_TRADED = {"REJECTED", "CANCELED"}


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _sum(values: Iterable[object]) -> str:
    total = sum((d for v in values if (d := _decimal(v)) is not None), Decimal("0"))
    return str(total)


def build_trades(
    operations: Iterable[Mapping[str, Any]],
    positions: Iterable[Mapping[str, Any]],
    fills: Iterable[Mapping[str, Any]],
    *,
    unprotected_position_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """One row per trade, newest first.

    ``positions`` must already hold the latest projection per ``position_id``.
    """

    positions_by_id = {str(p.get("position_id")): p for p in positions if p.get("position_id")}
    fills_by_order: dict[str, list[Mapping[str, Any]]] = {}
    for fill in fills:
        key = str(fill.get("client_order_id") or fill.get("order_id") or "")
        if key:
            fills_by_order.setdefault(key, []).append(fill)
    unprotected = set(unprotected_position_ids)

    rows: list[dict[str, Any]] = []
    linked_positions: set[str] = set()
    for operation in operations:
        state = str(operation.get("state") or "")
        position_id = _text(operation.get("position_id"))
        position = positions_by_id.get(position_id or "")
        if position is None and state in _NEVER_TRADED:
            continue
        if position_id:
            linked_positions.add(position_id)
        rows.append(_row(operation, position, fills_by_order, unprotected))
    for position_id, position in positions_by_id.items():
        if position_id not in linked_positions:
            rows.append(_row(None, position, fills_by_order, unprotected))
    rows.sort(key=lambda row: str(row["opened_at"] or ""), reverse=True)
    return rows


def _row(
    operation: Mapping[str, Any] | None,
    position: Mapping[str, Any] | None,
    fills_by_order: Mapping[str, list[Mapping[str, Any]]],
    unprotected: set[str],
) -> dict[str, Any]:
    operation = operation or {}
    position = position or {}
    state = str(operation.get("state") or "")
    position_status = str(position.get("status") or "")
    position_id = _text(position.get("position_id") or operation.get("position_id"))
    client_order_id = _text(
        operation.get("client_order_id") or position.get("client_order_id")
    )
    is_open = position_status in _OPEN_POSITION or (
        not position_status and state in _PENDING_OPERATION
    )
    needs_attention = state in _UNRESOLVED
    view: TradeView = "attention" if needs_attention else "open" if is_open else "closed"
    pnl = position.get("unrealized_pnl") if is_open else position.get("realized_net_pnl")
    exit_ids = position.get("exit_client_order_ids")
    order_ids = [client_order_id or "", *(exit_ids if isinstance(exit_ids, list) else [])]
    trade_fills = [fill for key in order_ids for fill in fills_by_order.get(str(key), [])]
    return {
        "id": str(operation.get("id") or position_id or ""),
        "operation_id": _text(operation.get("id")),
        "position_id": position_id,
        "asset": str(operation.get("asset") or position.get("asset") or ""),
        "side": _text(position.get("side")),
        "mode": _text(operation.get("mode")),
        "state": state or None,
        "position_status": position_status or None,
        "view": view,
        "needs_attention": needs_attention,
        "protected": bool(position.get("stop_price")) and position_id not in unprotected,
        "quantity": _text(position.get("quantity")),
        "entry_price": _text(position.get("entry_price")),
        "current_price": _text(position.get("current_price")),
        "exit_price": _text(position.get("exit_price")),
        "stop_price": _text(position.get("stop_price")),
        "target_price": _text(position.get("target_price")),
        "pnl_usd": _text(pnl),
        "fees_usd": _sum((position.get("entry_fee_usd"), position.get("exit_fee_usd"))),
        "exit_reason": _text(position.get("exit_reason")),
        "opened_at": _text(position.get("opened_at") or operation.get("created_at")),
        "closed_at": _text(position.get("closed_at")),
        "updated_at": _text(operation.get("updated_at") or position.get("state_at")),
        "fills": [dict(fill) for fill in trade_fills],
    }


def filter_trades(rows: list[dict[str, Any]], view: TradeView | None) -> list[dict[str, Any]]:
    return rows if view is None else [row for row in rows if row["view"] == view]


def rejected_entries(
    operations: Iterable[Mapping[str, Any]],
    reject_details: Mapping[str, Mapping[str, Any]],
    shadow_by_proposal: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Entries stopped before any order, with who stopped them and what they would have made."""

    rows: list[dict[str, Any]] = []
    for operation in operations:
        if str(operation.get("state") or "") != "REJECTED":
            continue
        operation_id = str(operation.get("id") or "")
        details = reject_details.get(operation_id, {})
        shadow = shadow_by_proposal.get(str(operation.get("proposal_id") or ""), {})
        rows.append(
            {
                "operation_id": operation_id,
                "proposal_id": _text(operation.get("proposal_id")),
                "asset": str(operation.get("asset") or ""),
                "reasons": [str(reason) for reason in details.get("reasons") or []],
                "rejected_at": _text(operation.get("updated_at")),
                "would_have_net_pnl_usd": _text(shadow.get("net_pnl_usd")),
                "would_have_outcome": _text(shadow.get("outcome")),
            }
        )
    return rows
