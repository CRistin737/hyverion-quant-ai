"""Durable paper/session state used to rebuild risk context after a restart.

The audit tables remain the immutable evidence log. This repository owns the
small control-plane projections needed by ``RiskEngine`` and the native UI:
account equity/high-water mark, daily PnL and open paper positions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import insert, select, update

from trading_bot.broker.base import BrokerAccount, BrokerPosition
from trading_bot.broker.models import OrderResult
from trading_bot.broker.reconciliation import (
    ProtectiveStopRecovery,
    ReconciliationResult,
    ReconciliationService,
    ReconciliationSnapshot,
)
from trading_bot.core.clock import FixedClock
from trading_bot.db.database import Database
from trading_bot.db.models import accounts, daily_pnl
from trading_bot.db.repositories import AuditRepository
from trading_bot.market.clock import MarketClock, trading_day
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent, RiskContext

BROKER_ACCOUNT_SYNC = "BROKER_ACCOUNT_SYNC"
# Reconciliation (and the equity sync with it) runs every 10 minutes; older
# than this means the broker link is broken and sizing would be a guess.
BROKER_EQUITY_MAX_AGE = timedelta(minutes=15)


NEW_YORK = ZoneInfo("America/New_York")


class TradingStateRepository:
    """Persist and reconstruct the state that must survive process restarts."""

    def __init__(self, database: Database, *, account_id: str = "default") -> None:
        self._database = database
        self._account_id = account_id
        self._audit = AuditRepository(database)

    async def ensure_account(self, *, equity: Decimal, now: datetime) -> dict[str, Any]:
        timestamp = _utc(now)
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                select(accounts).where(accounts.c.id == self._account_id)
            )
            row = result.mappings().first()
            if row is None:
                values = {
                    "id": self._account_id,
                    "name": "Primary paper account",
                    "currency": "USD",
                    "equity": equity,
                    "high_water_mark": equity,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                await connection.execute(insert(accounts).values(**values))
                return values
            return dict(row)

    async def sync_broker_account(
        self,
        account: BrokerAccount,
        positions: Sequence[BrokerPosition],
        *,
        now: datetime,
    ) -> dict[str, Any]:
        """Make the broker account the single source of equity.

        Hyverion has no configured capital: the paper account at the broker *is*
        the capital. The local projection keeps realized equity (unrealized PnL
        is tracked per position), so the broker's marked equity is reduced by
        the unrealized PnL it reports. A different account (new label) starts a
        new high-water mark instead of inheriting the old one.
        """

        timestamp = _utc(now)
        unrealized = sum((item.unrealized_pnl for item in positions), Decimal("0"))
        realized_equity = account.equity - unrealized
        if realized_equity <= 0:
            raise RuntimeError("broker_equity_non_positive")
        previous = await self.latest_broker_account()
        same_account = previous is not None and previous.get("account_label") == (
            account.account_label
        )
        current = await self.ensure_account(equity=realized_equity, now=timestamp)
        high_water_mark = (
            max(_decimal(current.get("high_water_mark")), realized_equity)
            if same_account
            else realized_equity
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(
                update(accounts)
                .where(accounts.c.id == self._account_id)
                .values(
                    equity=realized_equity,
                    high_water_mark=high_water_mark,
                    updated_at=timestamp,
                )
            )
        payload = {
            "status": BROKER_ACCOUNT_SYNC,
            "provider": account.provider,
            "environment": account.environment,
            "account_label": account.account_label,
            "currency": account.currency,
            "equity": str(account.equity),
            "cash": str(account.cash),
            "buying_power": str(account.buying_power),
            "unrealized_pnl": str(unrealized),
            "synced_at": timestamp.isoformat(),
        }
        await self._audit.append(
            "system_events",
            payload,
            created_at=timestamp,
            event_time=timestamp,
            received_time=timestamp,
            processed_time=timestamp,
        )
        await self._audit.append(
            "equity_snapshots",
            {key: payload[key] for key in ("account_label", "equity", "cash", "unrealized_pnl")},
            created_at=timestamp,
            event_time=timestamp,
        )
        return payload

    async def latest_broker_account(self) -> dict[str, Any] | None:
        """The last broker account sync (masked label, equity, cash, buying power)."""

        for row in await self._audit.with_status("system_events", [BROKER_ACCOUNT_SYNC]):
            return _payload(row)
        return None

    async def _broker_equity_fresh(self, timestamp: datetime) -> bool:
        latest = await self.latest_broker_account()
        if latest is None:
            return False
        try:
            synced_at = datetime.fromisoformat(str(latest.get("synced_at")))
        except ValueError:
            return False
        return timedelta(0) <= timestamp - _utc(synced_at) <= BROKER_EQUITY_MAX_AGE

    async def risk_context(
        self,
        *,
        mode: TradingMode,
        starting_equity: Decimal | None,
        asset: str,
        now: datetime,
        require_reconciliation: bool = False,
        macro_block_reason: str | None = None,
    ) -> RiskContext:
        """Build the RiskEngine input from persisted state.

        ``require_reconciliation`` is set for external brokers: entries stay
        blocked until the latest broker reconciliation succeeded and the
        broker's equity was synced recently (fail closed). External brokers pass
        ``starting_equity=None``: their equity only ever comes from the broker.
        """

        timestamp = _utc(now)
        if starting_equity is None and await self.latest_broker_account() is None:
            raise RuntimeError("broker_equity_unavailable")
        account = await self.ensure_account(
            equity=starting_equity or Decimal("0"), now=timestamp
        )
        daily = await self._daily_snapshot(trading_day(timestamp), timestamp)
        weekly_realized = await self._weekly_realized(trading_day(timestamp))
        positions = await self._open_positions()
        session = MarketClock(FixedClock(timestamp)).snapshot()
        today = trading_day(timestamp)
        entries_today = await self._entries_on(today)
        exposure = sum(
            (_decimal(row.get("entry_price")) * _decimal(row.get("quantity")) for row in positions),
            Decimal("0"),
        )
        asset_exposure = sum(
            (
                _decimal(row.get("entry_price")) * _decimal(row.get("quantity"))
                for row in positions
                if str(row.get("asset")) == asset
            ),
            Decimal("0"),
        )
        unrealized = sum((_decimal(row.get("unrealized_pnl")) for row in positions), Decimal("0"))
        remaining_risk = sum(
            (_decimal(row.get("remaining_risk_usd")) for row in positions), Decimal("0")
        )
        # The repository does not invent cooldown state. A future loss service
        # can persist it; until then the deterministic default is inactive.
        equity = _decimal(account.get("equity"))
        high_water_mark = _decimal(account.get("high_water_mark"))
        if equity <= 0 or high_water_mark <= 0:
            raise RuntimeError("account_equity_non_positive")
        return RiskContext(
            mode=mode,
            equity=equity,
            account_high_water_mark=high_water_mark,
            realized_net_pnl_today=_decimal(daily.get("realized_net_pnl")),
            unrealized_pnl=unrealized,
            intraday_peak_realized_pnl=_decimal(daily.get("peak_realized_pnl")),
            intraday_peak_total_pnl=_decimal(daily.get("peak_total_pnl")),
            fees_today=_decimal(daily.get("fees")),
            realized_net_pnl_week=weekly_realized,
            current_exposure_usd=exposure,
            asset_exposure_usd=asset_exposure,
            correlated_open_risk_usd=remaining_risk,
            open_remaining_risk_usd=remaining_risk,
            open_positions=len(positions),
            losing_streak=int(daily.get("losing_streak") or 0),
            live_stopped=bool(daily.get("live_stopped", False)),
            session_stop_reason=daily.get("stop_reason"),
            reconciliation_ok=(
                await self.latest_reconciliation_ok() is True if require_reconciliation else True
            ),
            broker_equity_fresh=(
                await self._broker_equity_fresh(timestamp) if require_reconciliation else True
            ),
            market_session=session.state.value,
            minutes_since_open=session.minutes_since_open,
            minutes_to_close=session.minutes_to_close,
            entries_today=entries_today,
            macro_block_reason=macro_block_reason,
        )

    async def persist_session_decision(
        self,
        *,
        action: str,
        reasons: tuple[str, ...],
        now: datetime,
    ) -> None:
        """Persist the daily live-stop latch without changing PAPER behavior."""

        timestamp = _utc(now)
        daily = await self._daily_snapshot(trading_day(timestamp), timestamp)
        stopped = bool(daily.get("live_stopped", False)) or action == "STOP_LIVE_FOR_DAY"
        reason = daily.get("stop_reason") or (reasons[0] if reasons else None)
        values = _daily_values(
            account_id=self._account_id,
            session_date=trading_day(timestamp),
            realized_net_pnl=_decimal(daily.get("realized_net_pnl")),
            unrealized_pnl=_decimal(daily.get("unrealized_pnl")),
            peak_realized_pnl=_decimal(daily.get("peak_realized_pnl")),
            peak_total_pnl=_decimal(daily.get("peak_total_pnl")),
            fees=_decimal(daily.get("fees")),
            losing_streak=int(daily.get("losing_streak") or 0),
            updated_at=timestamp,
            live_stopped=stopped,
            stop_reason=reason,
        )
        await self._upsert_daily(values)

    async def persist_context(self, context: RiskContext, *, now: datetime) -> None:
        timestamp = _utc(now)
        await self.ensure_account(equity=context.equity, now=timestamp)
        existing = await self._daily_snapshot(trading_day(timestamp), timestamp)
        values = _daily_values(
            account_id=self._account_id,
            session_date=trading_day(timestamp),
            realized_net_pnl=context.realized_net_pnl_today,
            unrealized_pnl=context.unrealized_pnl,
            peak_realized_pnl=max(
                context.intraday_peak_realized_pnl,
                context.realized_net_pnl_today,
            ),
            peak_total_pnl=max(
                context.intraday_peak_total_pnl,
                context.current_total_pnl,
            ),
            fees=context.fees_today,
            losing_streak=context.losing_streak,
            updated_at=timestamp,
            live_stopped=bool(existing.get("live_stopped", False)),
            stop_reason=existing.get("stop_reason"),
        )
        await self._upsert_daily(values)

    async def record_execution(
        self,
        *,
        intent: ExecutionIntent,
        order: OrderResult,
        now: datetime,
    ) -> None:
        """Apply fills exactly once to the account projection and open-position log."""

        timestamp = _utc(now)
        # Any real fill counts, including a day order cancelled after a partial
        # fill (status CANCELED, filled_quantity > 0).
        if order.filled_quantity <= 0:
            return
        if intent.reduce_only:
            await self._record_exit(intent=intent, order=order, timestamp=timestamp)
            return
        # Idempotency must include closed projections too. After a restart the
        # exchange can return an already-filled entry order whose position was
        # subsequently closed; checking only open positions would recreate the
        # position and double-count the fill.
        existing_positions = list((await self._latest_positions()).values())
        if any(
            row.get("position_id") == order.order_id
            or row.get("client_order_id") == order.client_order_id
            for row in existing_positions
        ):
            # The current order has already been projected by a prior restart.
            return
        fees = sum((fill.fee_usd for fill in order.fills), Decimal("0"))
        account = await self.ensure_account(equity=Decimal("0"), now=timestamp)
        equity = _decimal(account.get("equity")) - fees
        high_water_mark = max(_decimal(account.get("high_water_mark")), equity)
        async with self._database.engine.begin() as connection:
            await connection.execute(
                update(accounts)
                .where(accounts.c.id == self._account_id)
                .values(equity=equity, high_water_mark=high_water_mark, updated_at=timestamp)
            )
        daily = await self._daily_snapshot(trading_day(timestamp), timestamp)
        values = _daily_values(
            account_id=self._account_id,
            session_date=trading_day(timestamp),
            realized_net_pnl=_decimal(daily.get("realized_net_pnl")),
            unrealized_pnl=_decimal(daily.get("unrealized_pnl")),
            peak_realized_pnl=max(
                _decimal(daily.get("peak_realized_pnl")),
                _decimal(daily.get("realized_net_pnl")),
            ),
            peak_total_pnl=max(
                _decimal(daily.get("peak_total_pnl")),
                _decimal(daily.get("realized_net_pnl")) + _decimal(daily.get("unrealized_pnl")),
            ),
            fees=_decimal(daily.get("fees")) + fees,
            losing_streak=int(daily.get("losing_streak") or 0),
            updated_at=timestamp,
            live_stopped=bool(daily.get("live_stopped", False)),
            stop_reason=daily.get("stop_reason"),
        )
        await self._upsert_daily(values)
        entry_price = _weighted_fill_price(order)
        await self._audit.append(
            "positions",
            {
                "position_id": order.order_id,
                "client_order_id": order.client_order_id,
                "asset": intent.asset,
                "side": intent.side.value,
                "entry_price": str(entry_price),
                "current_price": str(entry_price),
                "quantity": str(order.filled_quantity),
                "stop_price": str(intent.stop_price),
                "target_price": str(intent.target_price),
                "unrealized_pnl": "0",
                "remaining_risk_usd": str(
                    abs(entry_price - intent.stop_price) * order.filled_quantity
                ),
                "entry_fee_usd": str(fees),
                "operation_id": intent.proposal_id,
                "entry_quantity": str(order.filled_quantity),
                "entry_fee_total_usd": str(fees),
                "entry_slippage_usd": str(
                    abs(entry_price - intent.limit_price) * order.filled_quantity
                ),
                "mfe_usd": "0",
                "mae_usd": "0",
                "cumulative_realized_net_pnl": "0",
                "cumulative_exit_fees_usd": "0",
                "exit_slippage_usd": "0",
                "exit_client_order_ids": [],
                "status": "OPEN",
                "protective_stop_active": order.protective_stop_active,
                "max_hold_seconds": intent.max_hold_seconds,
                "opened_at": timestamp.isoformat(),
                "state_at": timestamp.isoformat(),
                "state_priority": 0,
            },
            created_at=timestamp,
            asset=intent.asset,
            event_time=timestamp,
            received_time=timestamp,
            processed_time=timestamp,
        )
        for fill in order.fills:
            fill_payload = fill.model_dump(mode="json")
            fill_payload.update(
                {
                    "asset": intent.asset,
                    "side": intent.side.value,
                    "order_id": order.order_id,
                    "client_order_id": order.client_order_id,
                }
            )
            await self._audit.append(
                "fills",
                fill_payload,
                created_at=timestamp,
                asset=intent.asset,
                event_time=fill.filled_at,
                received_time=fill.filled_at,
                processed_time=timestamp,
            )

    async def _record_exit(
        self,
        *,
        intent: ExecutionIntent,
        order: OrderResult,
        timestamp: datetime,
    ) -> None:
        positions = await self._open_positions()
        position = await self._latest_position(str(intent.position_id))
        if position is None:
            raise RuntimeError("position_not_found_for_exit")
        exit_ids = position.get("exit_client_order_ids")
        if isinstance(exit_ids, list) and order.client_order_id in exit_ids:
            return
        if str(position.get("status", "OPEN")) != "OPEN":
            raise RuntimeError("position_not_open_for_exit")
        positions = [
            row
            for row in positions
            if row.get("position_id") != intent.position_id
        ] + [position]
        total_quantity = _decimal(position.get("quantity"))
        exit_quantity = min(total_quantity, order.filled_quantity)
        exit_price = _weighted_fill_price(order)
        entry_price = _decimal(position.get("entry_price"))
        direction = Decimal("1") if str(position.get("side")) == Side.BUY.value else Decimal("-1")
        gross_pnl = (exit_price - entry_price) * exit_quantity * direction
        entry_fee = _decimal(position.get("entry_fee_usd")) * exit_quantity / total_quantity
        exit_fees = sum((fill.fee_usd for fill in order.fills), Decimal("0"))
        realized_net = gross_pnl - entry_fee - exit_fees
        account = await self.ensure_account(equity=Decimal("0"), now=timestamp)
        equity = _decimal(account.get("equity")) + gross_pnl - exit_fees
        high_water_mark = max(_decimal(account.get("high_water_mark")), equity)
        async with self._database.engine.begin() as connection:
            await connection.execute(
                update(accounts)
                .where(accounts.c.id == self._account_id)
                .values(equity=equity, high_water_mark=high_water_mark, updated_at=timestamp)
            )

        remaining_quantity = total_quantity - exit_quantity
        remaining_positions = [
            row
            for row in positions
            if row.get("position_id") != intent.position_id
        ]
        daily = await self._daily_snapshot(trading_day(timestamp), timestamp)
        realized_today = _decimal(daily.get("realized_net_pnl")) + realized_net
        unrealized = sum(
            (_decimal(row.get("unrealized_pnl")) for row in remaining_positions),
            Decimal("0"),
        )
        if remaining_quantity > 0:
            unrealized += (exit_price - entry_price) * remaining_quantity * direction
        previous_streak = int(daily.get("losing_streak") or 0)
        losing_streak = previous_streak + 1 if realized_net < 0 else 0
        values = _daily_values(
            account_id=self._account_id,
            session_date=trading_day(timestamp),
            realized_net_pnl=realized_today,
            unrealized_pnl=unrealized,
            peak_realized_pnl=max(_decimal(daily.get("peak_realized_pnl")), realized_today),
            peak_total_pnl=max(
                _decimal(daily.get("peak_total_pnl")),
                realized_today + unrealized,
            ),
            fees=_decimal(daily.get("fees")) + exit_fees,
            losing_streak=losing_streak,
            updated_at=timestamp,
            live_stopped=bool(daily.get("live_stopped", False)),
            stop_reason=daily.get("stop_reason"),
        )
        await self._upsert_daily(values)

        updated = dict(position)
        exit_excursion = (exit_price - entry_price) * total_quantity * direction
        existing_exit_ids = list(exit_ids) if isinstance(exit_ids, list) else []
        existing_exit_ids.append(order.client_order_id)
        updated.update(
            {
                "current_price": str(exit_price),
                "exit_price": str(exit_price),
                "exit_fee_usd": str(exit_fees),
                "realized_net_pnl": str(realized_net),
                "exit_reason": intent.exit_reason,
                "exit_client_order_ids": existing_exit_ids,
                "mfe_usd": str(max(_decimal(position.get("mfe_usd")), exit_excursion)),
                "mae_usd": str(min(_decimal(position.get("mae_usd")), exit_excursion)),
                "cumulative_realized_net_pnl": str(
                    _decimal(position.get("cumulative_realized_net_pnl")) + realized_net
                ),
                "cumulative_exit_fees_usd": str(
                    _decimal(position.get("cumulative_exit_fees_usd")) + exit_fees
                ),
                "exit_slippage_usd": str(
                    _decimal(position.get("exit_slippage_usd"))
                    + abs(exit_price - intent.limit_price) * exit_quantity
                ),
                "quantity": str(remaining_quantity),
                "entry_fee_usd": str(
                    max(Decimal("0"), _decimal(position.get("entry_fee_usd")) - entry_fee)
                ),
                "unrealized_pnl": "0" if remaining_quantity <= 0 else str(
                    (exit_price - entry_price) * remaining_quantity * direction
                ),
                "remaining_risk_usd": "0" if remaining_quantity <= 0 else str(
                    abs(exit_price - _decimal(position.get("stop_price"))) * remaining_quantity
                ),
                "state_at": timestamp.isoformat(),
                "state_priority": 2,
                "status": "CLOSED" if remaining_quantity <= 0 else "OPEN",
                # The remainder is protected only if the venue says so: an external
                # broker cancels its native stop before exiting (see broker/alpaca.py).
                "protective_stop_active": remaining_quantity > 0 and order.protective_stop_active,
            }
        )
        if remaining_quantity <= 0:
            updated["closed_at"] = timestamp.isoformat()
        await self._audit.append(
            "positions",
            updated,
            created_at=timestamp,
            asset=intent.asset,
            event_time=timestamp,
            received_time=timestamp,
            processed_time=timestamp,
        )
        for fill in order.fills:
            fill_payload = fill.model_dump(mode="json")
            fill_payload.update(
                {
                    "asset": intent.asset,
                    "side": intent.side.value,
                    "order_id": order.order_id,
                    "client_order_id": order.client_order_id,
                }
            )
            await self._audit.append(
                "fills",
                fill_payload,
                created_at=timestamp,
                asset=intent.asset,
                event_time=fill.filled_at,
                received_time=fill.filled_at,
                processed_time=timestamp,
            )

    async def closed_position_mismatches(self, position_id: str) -> tuple[str, ...]:
        """Verify a closed projection against its recorded fills.

        PAPER/SHADOW treat the local fill log as the venue of record, so a closed
        position is reconciled only when it is flat and every entry unit was
        matched by an exit fill. Any gap is returned as a mismatch code.
        """

        position = await self._latest_position(position_id)
        if position is None:
            return ("position_missing",)
        mismatches: list[str] = []
        if str(position.get("status")) != "CLOSED":
            mismatches.append("position_not_closed")
        if _decimal(position.get("quantity")) != 0:
            mismatches.append("position_not_flat")
        exit_ids = position.get("exit_client_order_ids")
        exit_ids = set(exit_ids) if isinstance(exit_ids, list) else set()
        entry_quantity = Decimal("0")
        exit_quantity = Decimal("0")
        for row in await self._audit.recent("fills", limit=500):
            payload = _payload(row)
            client_order_id = payload.get("client_order_id")
            if client_order_id == position.get("client_order_id"):
                entry_quantity += _decimal(payload.get("quantity"))
            elif client_order_id in exit_ids:
                exit_quantity += _decimal(payload.get("quantity"))
        if entry_quantity <= 0:
            mismatches.append("entry_fills_missing")
        if entry_quantity != exit_quantity:
            mismatches.append("fill_quantity_mismatch")
        return tuple(mismatches)

    async def closed_trade_evaluation(self, position_id: str, *, now: datetime) -> dict[str, Any]:
        """Build the post-close evaluation from the persisted projection only."""

        position = await self._latest_position(position_id)
        if position is None or str(position.get("status")) != "CLOSED":
            raise RuntimeError("position_not_closed_for_evaluation")
        realized = _decimal(position.get("cumulative_realized_net_pnl"))
        fees = _decimal(position.get("entry_fee_total_usd")) + _decimal(
            position.get("cumulative_exit_fees_usd")
        )
        mfe = max(_decimal(position.get("mfe_usd")), Decimal("0"))
        mae = min(_decimal(position.get("mae_usd")), Decimal("0"))
        gross = realized + fees
        return {
            "trade_id": str(position.get("operation_id") or position_id),
            "position_id": position_id,
            "realized_net_pnl": str(realized),
            "mfe_usd": str(mfe),
            "mae_usd": str(mae),
            "fees_usd": str(fees),
            "slippage_usd": str(
                _decimal(position.get("entry_slippage_usd"))
                + _decimal(position.get("exit_slippage_usd"))
            ),
            # Share of the best observed excursion that the exit actually kept.
            "exit_efficiency": str(gross / mfe) if mfe > 0 else None,
            "exit_reason": position.get("exit_reason"),
            "thesis_valid": position.get("exit_reason")
            not in {"protective_stop_reached", "protective_stop_missing"},
            "signal_correct": realized > 0,
            "agent_incremental_values": {},
            "evaluated_at": _utc(now).isoformat(),
        }

    async def position(self, position_id: str) -> dict[str, Any] | None:
        """Return the latest projection of one position, open or closed."""

        return await self._latest_position(position_id)

    async def open_positions(self) -> list[dict[str, Any]]:
        """Return the latest open projections for deterministic protection."""

        return await self._open_positions()

    async def account_projection(self) -> dict[str, Any]:
        """Return the persisted cash equity and high-water mark read-only."""

        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(accounts).where(accounts.c.id == self._account_id)
            )
            row = result.mappings().first()
        if row is None:
            return {
                "id": self._account_id,
                "equity": Decimal("0"),
                "high_water_mark": Decimal("0"),
            }
        return dict(row)

    async def protective_stop_recovery(self) -> ProtectiveStopRecovery:
        """Verify every open position remains protected after a restart.

        This is a fail-closed invariant check, not an order-placement helper.
        The paper projection stores the deterministic protection state; a
        private exchange adapter must still reconcile the actual native stop
        before LIVE can ever be enabled.
        """

        positions = await self._open_positions()
        unprotected = tuple(
            sorted(
                str(row.get("position_id"))
                for row in positions
                if not bool(row.get("protective_stop_active", False))
            )
        )
        return ProtectiveStopRecovery(
            ok=not unprotected,
            safe_mode=bool(unprotected),
            open_positions=len(positions),
            protected_positions=len(positions) - len(unprotected),
            unprotected_position_ids=unprotected,
            reason="protective_stop_missing" if unprotected else None,
        )

    async def local_reconciliation_snapshot(self) -> ReconciliationSnapshot:
        """Build the bounded local side of a restart reconciliation.

        This method never assumes that local state is correct. It only
        materializes the current projection so an authenticated broker adapter can
        compare it and force SAFE MODE on any mismatch.
        """

        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(accounts.c.equity).where(accounts.c.id == self._account_id)
            )
            equity = result.scalar_one_or_none()
        order_rows = await self._audit.recent("orders", limit=500)
        active_order_ids: set[str] = set()
        for row in order_rows:
            payload = _payload(row)
            if str(payload.get("status") or "").upper() in {"OPEN", "PARTIALLY_FILLED"}:
                order_id = str(payload.get("order_id") or payload.get("client_order_id") or "")
                if order_id:
                    active_order_ids.add(order_id)
        fill_rows = await self._audit.recent("fills", limit=500)
        fill_ids = {
            str(_payload(row).get("fill_id") or row.get("id"))
            for row in fill_rows
            if _payload(row).get("fill_id") or row.get("id")
        }
        return ReconciliationSnapshot(
            balances={"USD": _decimal(equity)},
            open_order_ids=frozenset(active_order_ids),
            position_ids=frozenset(
                _canonical_exchange_position_id(str(row.get("asset") or ""))
                for row in await self._open_positions()
                if _canonical_exchange_position_id(str(row.get("asset") or ""))
            ),
            recent_fill_ids=frozenset(fill_ids),
        )

    async def reconcile(
        self,
        exchange_snapshot: ReconciliationSnapshot,
        *,
        now: datetime,
        compare_balances: bool = True,
        source: str | None = None,
    ) -> ReconciliationResult:
        """Compare local state with a broker snapshot and audit the result.

        ``compare_balances=False`` is for paper brokers whose account size is
        set at the broker and differs from the configured risk capital.
        """

        local_snapshot = await self.local_reconciliation_snapshot()
        if not compare_balances:
            local_snapshot = local_snapshot.model_copy(update={"balances": {}})
        result = ReconciliationService().compare(local_snapshot, exchange_snapshot)
        protection = await self.protective_stop_recovery()
        if not protection.ok:
            result = result.model_copy(
                update={
                    "ok": False,
                    "safe_mode": True,
                    "mismatches": (
                        *result.mismatches,
                        *tuple(
                            f"protective_stop_missing:{position_id}"
                            for position_id in protection.unprotected_position_ids
                        ),
                    ),
                }
            )
        timestamp = _utc(now)
        await self._audit.append(
            "system_events",
            {
                "status": "RECONCILIATION_OK" if result.ok else "RECONCILIATION_MISMATCH",
                "safe_mode": result.safe_mode,
                "mismatches": list(result.mismatches),
                "source": source,
            },
            created_at=timestamp,
            event_time=timestamp,
            received_time=timestamp,
            processed_time=timestamp,
        )
        return result

    async def record_reconciliation_error(self, code: str, *, now: datetime) -> None:
        """An unreachable broker is a failed reconciliation: entries stay blocked."""

        timestamp = _utc(now)
        await self._audit.append(
            "system_events",
            {"status": "RECONCILIATION_ERROR", "safe_mode": True, "error_code": code},
            created_at=timestamp,
            event_time=timestamp,
            received_time=timestamp,
            processed_time=timestamp,
        )

    async def latest_reconciliation_ok(self) -> bool | None:
        """Outcome of the most recent reconciliation event; None if none ran."""

        for row in await self._audit.with_status(
            "system_events",
            ["RECONCILIATION_OK", "RECONCILIATION_MISMATCH", "RECONCILIATION_ERROR"],
        ):
            return str(_payload(row).get("status") or "") == "RECONCILIATION_OK"
        return None

    async def mark_to_market(
        self,
        *,
        asset: str,
        current_price: Decimal,
        now: datetime,
    ) -> None:
        """Persist deterministic marks and the aggregate unrealized PnL.

        This is intentionally independent of an LLM.  Every public market
        snapshot can refresh open paper/shadow positions, while protective
        decisions remain valid during provider outages.
        """

        if current_price <= 0:
            raise ValueError("current_price must be positive")
        timestamp = _utc(now)
        positions = await self._open_positions()
        total_unrealized = Decimal("0")
        for position in positions:
            if str(position.get("asset")) != asset:
                total_unrealized += _decimal(position.get("unrealized_pnl"))
                continue
            entry = _decimal(position.get("entry_price"))
            quantity = _decimal(position.get("quantity"))
            side = str(position.get("side", "buy")).lower()
            direction = Decimal("1") if side == Side.BUY.value else Decimal("-1")
            unrealized = (current_price - entry) * quantity * direction
            stop = _decimal(position.get("stop_price"))
            remaining_risk = abs(current_price - stop) * quantity
            marked = dict(position)
            marked.update(
                {
                    "current_price": str(current_price),
                    "unrealized_pnl": str(unrealized),
                    "mfe_usd": str(max(_decimal(position.get("mfe_usd")), unrealized)),
                    "mae_usd": str(min(_decimal(position.get("mae_usd")), unrealized)),
                    "remaining_risk_usd": str(remaining_risk),
                    "marked_at": timestamp.isoformat(),
                    "state_at": timestamp.isoformat(),
                    "state_priority": 1,
                }
            )
            await self._audit.append(
                "positions",
                marked,
                created_at=timestamp,
                asset=asset,
                event_time=timestamp,
                received_time=timestamp,
                processed_time=timestamp,
            )
            total_unrealized += unrealized

        daily = await self._daily_snapshot(trading_day(timestamp), timestamp)
        realized = _decimal(daily.get("realized_net_pnl"))
        # Keep the account high-water mark based on marked equity without
        # replacing the cash/equity projection. This avoids compounding the
        # same unrealized PnL on every market tick while still enforcing the
        # drawdown circuit breaker after a restart.
        async with self._database.engine.begin() as connection:
            account_result = await connection.execute(
                select(accounts.c.equity, accounts.c.high_water_mark).where(
                    accounts.c.id == self._account_id
                )
            )
            account_row = account_result.mappings().first()
            if account_row is not None:
                cash_equity = _decimal(account_row.get("equity"))
                high_water_mark = _decimal(account_row.get("high_water_mark"))
                marked_equity = cash_equity + total_unrealized
                if marked_equity > high_water_mark:
                    await connection.execute(
                        update(accounts)
                        .where(accounts.c.id == self._account_id)
                        .values(high_water_mark=marked_equity, updated_at=timestamp)
                    )
        values = _daily_values(
            account_id=self._account_id,
            session_date=trading_day(timestamp),
            realized_net_pnl=realized,
            unrealized_pnl=total_unrealized,
            peak_realized_pnl=max(_decimal(daily.get("peak_realized_pnl")), realized),
            peak_total_pnl=max(
                _decimal(daily.get("peak_total_pnl")),
                realized + total_unrealized,
            ),
            fees=_decimal(daily.get("fees")),
            losing_streak=int(daily.get("losing_streak") or 0),
            updated_at=timestamp,
            live_stopped=bool(daily.get("live_stopped", False)),
            stop_reason=daily.get("stop_reason"),
        )
        await self._upsert_daily(values)

    async def _daily_snapshot(self, session_date: date, now: datetime) -> dict[str, Any]:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(daily_pnl).where(
                    daily_pnl.c.account_id == self._account_id,
                    daily_pnl.c.session_date == session_date,
                )
            )
            row = result.mappings().first()
        if row is not None:
            return dict(row)
        values = _daily_values(
            account_id=self._account_id,
            session_date=session_date,
            realized_net_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            peak_realized_pnl=Decimal("0"),
            peak_total_pnl=Decimal("0"),
            fees=Decimal("0"),
            losing_streak=0,
            updated_at=now,
            live_stopped=False,
            stop_reason=None,
        )
        await self._upsert_daily(values)
        return values

    async def _weekly_realized(self, session_date: date) -> Decimal:
        start = session_date - timedelta(days=6)
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(daily_pnl.c.realized_net_pnl).where(
                    daily_pnl.c.account_id == self._account_id,
                    daily_pnl.c.session_date >= start,
                    daily_pnl.c.session_date <= session_date,
                )
            )
            return sum((_decimal(row[0]) for row in result.all()), Decimal("0"))

    async def _open_positions(self) -> list[dict[str, Any]]:
        return [
            row
            for row in (await self._latest_positions()).values()
            if str(row.get("status", "OPEN")) == "OPEN"
        ]

    async def _latest_position(self, position_id: str) -> dict[str, Any] | None:
        return (await self._latest_positions()).get(position_id)

    async def _entries_on(self, day: date) -> int:
        """Positions opened on this New York day, read from every row since midnight.

        A fixed window of recent rows is not enough: open positions are marked
        every few seconds, which would push earlier entries out and let the
        trades-per-day limit under-count.
        """

        midnight = datetime.combine(day, time(0), tzinfo=NEW_YORK).astimezone(UTC)
        opened: set[str] = set()
        rows = await self._audit.between(
            "positions", start=midnight, end=midnight + timedelta(days=1)
        )
        for row in rows:
            payload = _payload(row)
            position_id = str(payload.get("position_id") or row.get("id") or "")
            if position_id and _opened_on(payload, day):
                opened.add(position_id)
        return len(opened)

    async def _latest_positions(self) -> dict[str, dict[str, Any]]:
        rows = await self._audit.recent("positions", limit=500)
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = _payload(row)
            current_id = str(payload.get("position_id") or row.get("id") or "")
            if not current_id:
                continue
            current = latest.get(current_id)
            if current is None or position_recency(payload) > position_recency(current):
                latest[current_id] = payload
        return latest

    async def _upsert_daily(self, values: dict[str, Any]) -> None:
        async with self._database.engine.begin() as connection:
            result = await connection.execute(
                select(daily_pnl.c.id).where(
                    daily_pnl.c.account_id == self._account_id,
                    daily_pnl.c.session_date == values["session_date"],
                )
            )
            identifier = result.scalar_one_or_none()
            if identifier is None:
                await connection.execute(insert(daily_pnl).values(**values))
            else:
                await connection.execute(
                    update(daily_pnl)
                    .where(daily_pnl.c.id == identifier)
                    .values(**{key: value for key, value in values.items() if key != "id"})
                )


def _daily_values(**values: Any) -> dict[str, Any]:
    values.setdefault("id", f"daily-{values['account_id']}-{values['session_date'].isoformat()}")
    return values


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _weighted_fill_price(order: OrderResult) -> Decimal:
    notional = sum((fill.price * fill.quantity for fill in order.fills), Decimal("0"))
    return notional / order.filled_quantity


def position_recency(position: dict[str, Any]) -> tuple[str, int]:
    """Prefer marked/closed projections when SQLite timestamps tie."""

    return (
        str(
            position.get("state_at")
            or position.get("marked_at")
            or position.get("closed_at")
            or position.get("opened_at")
            or ""
        ),
        int(position.get("state_priority") or 0),
    )


def _canonical_exchange_position_id(asset: str) -> str:
    """Project a local position into the broker reconciliation identity.

    The paper state keeps a stable strategy/order ``position_id`` for audit and
    exit idempotency. A broker reports one aggregate position per symbol, so
    reconciliation compares the canonical ``equity:SYMBOL`` identity while the
    immutable audit log retains the strategy-level identifier.
    """

    symbol = asset.strip().upper()
    if not symbol or symbol == "USD":
        return ""
    return f"equity:{symbol}"


def _opened_on(position: dict[str, Any], day: date) -> bool:
    raw = position.get("opened_at")
    if not isinstance(raw, str) or not raw:
        return False
    try:
        opened = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return opened.tzinfo is not None and trading_day(opened) == day


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("state timestamps must be timezone-aware")
    return value.astimezone(UTC)
