"""Explicit human resolution of an operation whose venue state is unproven.

The operator confirms what the venue shows after checking it. The resolution is
validated against the local position projection so it can never invent fills,
orphan an open position or reopen a terminal operation. Every resolution is
recorded in the lifecycle log and as a system event.
"""

from __future__ import annotations

import re

from trading_bot.broker.lifecycle import UNRESOLVED_STATES, OperationState
from trading_bot.core.clock import Clock
from trading_bot.core.post_close import PostCloseEvaluator
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository

S = OperationState

OPERATOR_TARGETS: frozenset[OperationState] = frozenset(
    {S.REJECTED, S.CANCELED, S.OPEN, S.CLOSED}
)
_REASON_LIMITS = (3, 200)


class OperatorResolutionError(ValueError):
    """The requested resolution is not supported by the recorded evidence."""


class OperatorResolutionService:
    def __init__(
        self,
        *,
        lifecycle: OperationLifecycleRepository,
        state: TradingStateRepository,
        repository: AuditRepository,
        clock: Clock,
    ) -> None:
        self._lifecycle = lifecycle
        self._state = state
        self._repository = repository
        self._clock = clock

    async def resolve(
        self, operation_id: str, target: OperationState, reason: str
    ) -> OperationState:
        text = _clean_reason(reason)
        if target not in OPERATOR_TARGETS:
            raise OperatorResolutionError("unsupported resolution target")
        row = await self._lifecycle.get(operation_id)
        if row is None:
            raise LookupError("operation not found")
        current = OperationState(row["state"])
        if current not in UNRESOLVED_STATES:
            raise OperatorResolutionError("only unresolved operations can be resolved manually")
        position_id = row.get("position_id")
        position = await self._state.position(str(position_id)) if position_id else None
        position_status = str(position.get("status")) if position is not None else None
        if target is S.OPEN and position_status != "OPEN":
            raise OperatorResolutionError("OPEN requires an open local position")
        if target is S.CLOSED and position_status != "CLOSED":
            raise OperatorResolutionError("CLOSED requires a closed local position")
        if target in {S.REJECTED, S.CANCELED} and position is not None:
            # A recorded position means something filled; it must be closed, not erased.
            raise OperatorResolutionError("a filled operation cannot be rejected or canceled")

        result = await self._lifecycle.advance(
            operation_id,
            target,
            reason=f"operator:{text}",
            now=self._clock.now(),
            details={"operator": True, "previous_state": current.value},
        )
        if not result.legal:
            raise OperatorResolutionError(result.reason)
        await self._repository.append(
            "system_events",
            {
                "status": "OPERATOR_RESOLUTION",
                "operation_id": operation_id,
                "from_state": current.value,
                "to_state": target.value,
                "reason": text,
            },
            created_at=self._clock.now(),
            asset=str(row["asset"]),
        )
        if target is S.CLOSED and position_id:
            return await PostCloseEvaluator(
                lifecycle=self._lifecycle,
                state=self._state,
                repository=self._repository,
                clock=self._clock,
            ).complete(operation_id, str(position_id), str(row["asset"]))
        return result.to_state


def _clean_reason(reason: str) -> str:
    text = re.sub(r"\s+", " ", reason).strip()
    low, high = _REASON_LIMITS
    if not low <= len(text) <= high:
        raise OperatorResolutionError(f"reason must be {low}-{high} characters")
    return text
