"""Restart recovery for every non-terminal operation.

Runs before the first cycle after a restart. Each active operation is resolved
only from evidence: a read-only venue lookup or the persisted position
projection. Anything that cannot be proven is escalated to
``RECOVERY_REQUIRED``, which blocks new entries until an operator resolves it.
Recovery never submits, cancels or replaces an order.
"""

from __future__ import annotations

from typing import Any

from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.lifecycle import UNRESOLVED_STATES, OperationState, state_for_order
from trading_bot.core.clock import Clock
from trading_bot.core.post_close import SELF_RECONCILING_MODES, PostCloseEvaluator
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository
from trading_bot.schemas.common import StrictSchema, TradingMode

S = OperationState

_PRE_SUBMISSION = frozenset({S.PROPOSED, S.CRITIC_REVIEWED, S.RISK_APPROVED})
_NEEDS_VENUE_LOOKUP = frozenset(
    {S.EXECUTION_PENDING, S.SUBMITTED, S.ACKNOWLEDGED, S.UNKNOWN, S.CANCEL_PENDING}
)
_HOLDING = frozenset({S.OPEN, S.PARTIALLY_FILLED})
_EXITING = frozenset({S.EXIT_PENDING, S.CLOSING})
_POST_CLOSE = frozenset({S.CLOSED, S.RECONCILED})


class RecoveryReport(StrictSchema):
    inspected: int
    transitions: tuple[str, ...]
    unresolved_operation_ids: tuple[str, ...]

    @property
    def safe(self) -> bool:
        return not self.unresolved_operation_ids


class OperationRecoveryService:
    def __init__(
        self,
        *,
        lifecycle: OperationLifecycleRepository,
        state: TradingStateRepository,
        execution_engine: ExecutionEngine,
        repository: AuditRepository,
        clock: Clock,
    ) -> None:
        self._lifecycle = lifecycle
        self._state = state
        self._execution = execution_engine
        self._repository = repository
        self._clock = clock
        self._post_close = PostCloseEvaluator(
            lifecycle=lifecycle, state=state, repository=repository, clock=clock
        )

    def _simulated(self, row: dict[str, Any]) -> bool:
        return (
            TradingMode(row["mode"]) in SELF_RECONCILING_MODES and self._execution.simulated_venue
        )

    async def recover(self) -> RecoveryReport:
        active = await self._lifecycle.active()
        transitions: list[str] = []
        for row in active:
            before = OperationState(row["state"])
            after = await self._recover_one(row)
            if after is not before:
                transitions.append(f"{row['id']}:{before.value}->{after.value}")
        unresolved = tuple(
            str(row["id"])
            for row in await self._lifecycle.active()
            if OperationState(row["state"]) in UNRESOLVED_STATES
        )
        report = RecoveryReport(
            inspected=len(active),
            transitions=tuple(transitions),
            unresolved_operation_ids=unresolved,
        )
        if report.safe and not report.transitions:
            return report
        now = self._clock.now()
        await self._repository.append(
            "system_events",
            {
                "status": "RESTART_RECOVERY_OK" if report.safe else "RESTART_RECOVERY_REQUIRED",
                "safe_mode": not report.safe,
                "inspected": report.inspected,
                "transitions": list(report.transitions),
                "unresolved_operation_ids": list(report.unresolved_operation_ids),
            },
            created_at=now,
            event_time=now,
            received_time=now,
            processed_time=now,
        )
        return report

    async def _recover_one(self, row: dict[str, Any]) -> OperationState:
        operation_id = str(row["id"])
        state = OperationState(row["state"])
        position_id = row.get("position_id")
        if state in _PRE_SUBMISSION:
            return await self._advance(operation_id, S.REJECTED, "abandoned_before_submission")
        if state in _NEEDS_VENUE_LOOKUP:
            return await self._resolve_order(row, state)
        if state in _HOLDING:
            position = await self._position(position_id)
            if position is not None and str(position.get("status")) == "OPEN":
                return state
            return await self._advance(operation_id, S.RECOVERY_REQUIRED, "position_missing")
        if state in _EXITING:
            return await self._resolve_exit(row, state)
        if state in _POST_CLOSE and position_id:
            return await self._post_close.complete(operation_id, str(position_id), row["asset"])
        if state in _POST_CLOSE:
            return await self._advance(operation_id, S.RECOVERY_REQUIRED, "position_id_missing")
        # SAFE_MODE and RECOVERY_REQUIRED wait for an operator decision.
        return state

    async def _resolve_order(self, row: dict[str, Any], state: OperationState) -> OperationState:
        operation_id = str(row["id"])
        client_order_id = row.get("client_order_id")
        if not client_order_id:
            return await self._advance(operation_id, S.RECOVERY_REQUIRED, "client_order_id_missing")
        order = await self._execution.lookup(str(client_order_id))
        if order is None:
            if not self._simulated(row):
                # A real venue that cannot find the order needs a human decision.
                return await self._advance(
                    operation_id, S.RECOVERY_REQUIRED, "order_not_found_on_venue"
                )
            # Simulated orders live in process memory; absence proves no fill.
            target = S.CANCELED if state in {S.ACKNOWLEDGED, S.CANCEL_PENDING} else S.REJECTED
            return await self._advance(operation_id, target, "simulated_order_absent_on_restart")
        if state is S.EXECUTION_PENDING:
            state = await self._advance(operation_id, S.SUBMITTED, "venue_lookup_found_order")
        target = state_for_order(order)
        if order.filled_quantity > 0 and await self._position(order.order_id) is None:
            return await self._advance(
                operation_id, S.RECOVERY_REQUIRED, "fill_without_local_projection"
            )
        if target is state:
            return state
        return await self._advance(
            operation_id,
            target,
            f"venue_lookup:{order.status}",
            position_id=order.order_id if order.filled_quantity > 0 else None,
        )

    async def _resolve_exit(self, row: dict[str, Any], state: OperationState) -> OperationState:
        operation_id = str(row["id"])
        if not self._simulated(row):
            # A real exit order may be resting or filled; only the venue can say.
            exit_client_order_id = await self._lifecycle.last_exit_client_order_id(operation_id)
            order = (
                await self._execution.lookup(exit_client_order_id)
                if exit_client_order_id
                else None
            )
            if order is not None and order.status in {"OPEN", "PARTIALLY_FILLED"}:
                return state
            return await self._advance(
                operation_id, S.RECOVERY_REQUIRED, "exit_state_unproven_on_restart"
            )
        position = await self._position(row.get("position_id"))
        if position is None:
            return await self._advance(operation_id, S.RECOVERY_REQUIRED, "position_missing")
        if str(position.get("status")) == "OPEN":
            # The deterministic position manager re-evaluates the exit next cycle.
            return await self._advance(operation_id, S.OPEN, "exit_unconfirmed_on_restart")
        if state is S.EXIT_PENDING:
            await self._advance(operation_id, S.CLOSING, "exit_fill_found_on_restart")
        await self._advance(operation_id, S.CLOSED, "position_closed_on_restart")
        return await self._post_close.complete(
            operation_id, str(row["position_id"]), row["asset"]
        )

    async def _position(self, position_id: object) -> dict[str, Any] | None:
        if not position_id:
            return None
        return await self._state.position(str(position_id))

    async def _advance(
        self,
        operation_id: str,
        target: OperationState,
        reason: str,
        *,
        position_id: str | None = None,
    ) -> OperationState:
        result = await self._lifecycle.advance(
            operation_id,
            target,
            reason=reason,
            now=self._clock.now(),
            position_id=position_id,
        )
        return result.to_state
