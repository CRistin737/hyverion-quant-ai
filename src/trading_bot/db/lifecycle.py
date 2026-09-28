"""Durable operation lifecycle projection with an append-only transition log.

``operations`` holds the current state of each operation; ``operation_events``
records every requested transition, including illegal ones, so an operator can
rebuild exactly how an operation reached ``RECOVERY_REQUIRED``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from trading_bot.broker.lifecycle import (
    TERMINAL_STATES,
    UNRESOLVED_STATES,
    OperationState,
    TransitionResult,
    plan_transition,
)
from trading_bot.db.database import Database
from trading_bot.db.models import operation_events, operations
from trading_bot.schemas.common import TradingMode


class OperationLifecycleRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def open(
        self,
        *,
        operation_id: str,
        proposal_id: str,
        asset: str,
        mode: TradingMode,
        now: datetime,
    ) -> OperationState:
        """Create the operation in ``PROPOSED``; repeated calls are idempotent."""

        timestamp = _utc(now)
        async with self._database.engine.begin() as connection:
            existing = await self._state(connection, operation_id)
            if existing is not None:
                return existing
            await connection.execute(
                insert(operations).values(
                    id=operation_id,
                    proposal_id=proposal_id,
                    asset=asset,
                    mode=mode.value,
                    state=OperationState.PROPOSED.value,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
            await self._append_event(
                connection,
                operation_id=operation_id,
                from_state=None,
                result=TransitionResult(
                    from_state=OperationState.PROPOSED,
                    to_state=OperationState.PROPOSED,
                    requested_state=OperationState.PROPOSED,
                    legal=True,
                    reason="proposal_created",
                ),
                details={},
                occurred_at=timestamp,
            )
        return OperationState.PROPOSED

    async def advance(
        self,
        operation_id: str,
        target: OperationState,
        *,
        reason: str,
        now: datetime,
        details: dict[str, Any] | None = None,
        client_order_id: str | None = None,
        position_id: str | None = None,
    ) -> TransitionResult:
        """Apply one transition atomically; illegal requests persist the fail-closed state."""

        timestamp = _utc(now)
        async with self._database.engine.begin() as connection:
            current = await self._state(connection, operation_id)
            if current is None:
                raise LookupError(f"unknown operation: {operation_id}")
            result = plan_transition(current, target, reason=reason)
            values: dict[str, Any] = {"state": result.to_state.value, "updated_at": timestamp}
            if client_order_id is not None:
                values["client_order_id"] = client_order_id
            if position_id is not None:
                values["position_id"] = position_id
            await connection.execute(
                update(operations).where(operations.c.id == operation_id).values(**values)
            )
            await self._append_event(
                connection,
                operation_id=operation_id,
                from_state=current,
                result=result,
                details=details or {},
                occurred_at=timestamp,
            )
        return result

    async def get(self, operation_id: str) -> dict[str, Any] | None:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(operations).where(operations.c.id == operation_id)
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def find_by_position(self, position_id: str) -> dict[str, Any] | None:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(operations).where(operations.c.position_id == position_id)
            )
            row = result.mappings().first()
        return dict(row) if row is not None else None

    async def events(self, operation_id: str) -> list[dict[str, Any]]:
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(operation_events)
                .where(operation_events.c.operation_id == operation_id)
                .order_by(operation_events.c.sequence)
            )
            return [dict(row) for row in result.mappings().all()]

    async def active(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return every non-terminal operation; restart recovery must visit each one."""

        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        terminal = [state.value for state in TERMINAL_STATES]
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(operations)
                .where(operations.c.state.not_in(terminal))
                .order_by(operations.c.created_at)
                .limit(limit)
            )
            return [dict(row) for row in result.mappings().all()]

    async def last_exit_client_order_id(self, operation_id: str) -> str | None:
        """Return the client order id of the most recent exit attempt, if any."""

        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(operation_events.c.details)
                .where(
                    operation_events.c.operation_id == operation_id,
                    operation_events.c.to_state == OperationState.EXIT_PENDING.value,
                    operation_events.c.legal.is_(True),
                )
                .order_by(operation_events.c.sequence.desc())
                .limit(1)
            )
            details = result.scalar_one_or_none()
        if not isinstance(details, dict):
            return None
        value = details.get("client_order_id")
        return str(value) if value else None

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recently updated operations for the native UI."""

        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(operations)
                .order_by(operations.c.updated_at.desc(), operations.c.id)
                .limit(limit)
            )
            return [dict(row) for row in result.mappings().all()]

    async def count_unresolved(self) -> int:
        """Count operations whose venue state is not proven; each blocks new entries."""

        unresolved = [state.value for state in UNRESOLVED_STATES]
        async with self._database.engine.connect() as connection:
            result = await connection.execute(
                select(func.count())
                .select_from(operations)
                .where(operations.c.state.in_(unresolved))
            )
            return int(result.scalar_one())

    @staticmethod
    async def _state(connection: AsyncConnection, operation_id: str) -> OperationState | None:
        result = await connection.execute(
            select(operations.c.state).where(operations.c.id == operation_id)
        )
        value = result.scalar_one_or_none()
        return OperationState(value) if value is not None else None

    @staticmethod
    async def _append_event(
        connection: AsyncConnection,
        *,
        operation_id: str,
        from_state: OperationState | None,
        result: TransitionResult,
        details: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        sequence_result = await connection.execute(
            select(func.coalesce(func.max(operation_events.c.sequence), 0)).where(
                operation_events.c.operation_id == operation_id
            )
        )
        sequence = int(sequence_result.scalar_one()) + 1
        await connection.execute(
            insert(operation_events).values(
                id=str(uuid4()),
                operation_id=operation_id,
                sequence=sequence,
                from_state=from_state.value if from_state is not None else None,
                to_state=result.to_state.value,
                requested_state=result.requested_state.value,
                legal=result.legal,
                reason=result.reason[:255],
                details=details,
                occurred_at=occurred_at,
            )
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("lifecycle timestamps must be timezone-aware")
    return value.astimezone(UTC)
