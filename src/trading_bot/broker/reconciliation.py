from __future__ import annotations

from decimal import Decimal

from trading_bot.schemas.common import StrictSchema


class ReconciliationSnapshot(StrictSchema):
    balances: dict[str, Decimal]
    open_order_ids: frozenset[str]
    position_ids: frozenset[str]
    recent_fill_ids: frozenset[str]


class ReconciliationResult(StrictSchema):
    ok: bool
    safe_mode: bool
    mismatches: tuple[str, ...]
    exchange_snapshot: ReconciliationSnapshot


class ProtectiveStopRecovery(StrictSchema):
    """Bounded restart check for the protective-stop invariant.

    The local projection is intentionally read-only.  A missing stop never
    gets recreated by guessing exchange semantics; it forces SAFE MODE until
    an authenticated adapter proves the native stop exists or an operator
    closes the exposure.
    """

    ok: bool
    safe_mode: bool
    open_positions: int
    protected_positions: int
    unprotected_position_ids: tuple[str, ...] = ()
    reason: str | None = None


class ReconciliationService:
    def compare(
        self,
        local: ReconciliationSnapshot,
        exchange: ReconciliationSnapshot,
        *,
        balance_tolerance: Decimal = Decimal("0.00000001"),
    ) -> ReconciliationResult:
        mismatches: list[str] = []
        assets = set(local.balances) | set(exchange.balances)
        for asset in sorted(assets):
            difference = abs(
                local.balances.get(asset, Decimal("0"))
                - exchange.balances.get(asset, Decimal("0"))
            )
            if difference > balance_tolerance:
                mismatches.append(f"balance_mismatch:{asset}")
        if local.open_order_ids != exchange.open_order_ids:
            mismatches.append("open_orders_mismatch")
        if local.position_ids != exchange.position_ids:
            mismatches.append("positions_mismatch")
        if not local.recent_fill_ids.issubset(exchange.recent_fill_ids):
            mismatches.append("recent_fills_mismatch")
        return ReconciliationResult(
            ok=not mismatches,
            safe_mode=bool(mismatches),
            mismatches=tuple(mismatches),
            exchange_snapshot=exchange,
        )
