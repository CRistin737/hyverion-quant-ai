"""Deterministic post-close steps: ``CLOSED -> RECONCILED -> EVALUATED``.

Shared by the live cycle and restart recovery so both walk exactly the same
path. Only simulated modes can self-reconcile against the local fill log; a
real venue needs an authenticated exchange snapshot before ``RECONCILED``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trading_bot.broker.lifecycle import OperationState
from trading_bot.core.clock import Clock
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository
from trading_bot.schemas.common import TradingMode
from trading_bot.schemas.trading import TradeProposal
from trading_bot.simulation.shadow import ShadowComparison, ShadowSimulator

SELF_RECONCILING_MODES = frozenset({TradingMode.PAPER, TradingMode.SHADOW})


class PostCloseEvaluator:
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

    async def complete(self, operation_id: str, position_id: str, asset: str) -> OperationState:
        """Advance a closed operation as far as the evidence allows."""

        row = await self._lifecycle.get(operation_id)
        if row is None:
            raise LookupError(f"unknown operation: {operation_id}")
        state = OperationState(row["state"])
        if TradingMode(row["mode"]) not in SELF_RECONCILING_MODES:
            return state
        if state is OperationState.CLOSED:
            state = await self._reconcile(operation_id, position_id, asset)
        if state is OperationState.RECONCILED:
            state = await self._evaluate(operation_id, position_id, asset)
        return state

    async def _reconcile(self, operation_id: str, position_id: str, asset: str) -> OperationState:
        mismatches = await self._state.closed_position_mismatches(position_id)
        if mismatches:
            result = await self._lifecycle.advance(
                operation_id,
                OperationState.SAFE_MODE,
                reason="closed_position_mismatch",
                now=self._clock.now(),
                details={"mismatches": list(mismatches)},
            )
            await self._repository.append(
                "system_events",
                {
                    "status": "RECONCILIATION_MISMATCH",
                    "safe_mode": True,
                    "operation_id": operation_id,
                    "position_id": position_id,
                    "mismatches": list(mismatches),
                },
                created_at=self._clock.now(),
                asset=asset,
            )
            return result.to_state
        result = await self._lifecycle.advance(
            operation_id,
            OperationState.RECONCILED,
            reason="paper_fill_log_consistent",
            now=self._clock.now(),
        )
        return result.to_state

    async def _shadow_comparison(
        self, operation_id: str, asset: str, realized: Decimal
    ) -> dict[str, Any]:
        """Replay the original proposal on the recorded prices and compare with the fill.

        Validates the shadow simulator (which judges every rejected proposal)
        against what actually happened. Never blocks the evaluation: missing
        evidence is reported with a stable code instead.
        """

        try:
            proposal = await self._proposal(operation_id)
            if proposal is None:
                return {"status": "unavailable", "reason": "proposal_not_found"}
            closed_at = self._clock.now()
            prices = tuple(
                Decimal(str(payload["last"]))
                for payload in reversed(
                    [
                        _payload(row)
                        for row in await self._repository.recent("market_snapshots", limit=500)
                    ]
                )
                if payload.get("symbol") == asset
                and proposal.created_at <= _as_utc(payload.get("event_time")) <= closed_at
            )
            if not prices:
                return {"status": "unavailable", "reason": "no_recorded_prices"}
            shadow = ShadowSimulator().simulate(proposal, prices)
        except (ArithmeticError, KeyError, TypeError, ValueError):
            return {"status": "unavailable", "reason": "invalid_evidence"}
        comparison = ShadowComparison.compare(
            proposal_id=proposal.proposal_id,
            shadow_trade_pnl_usd=shadow.net_pnl_usd,
            real_trade_pnl_usd=realized,
        )
        return {
            "status": "compared",
            **comparison.model_dump(mode="json"),
            "shadow_exit_reason": shadow.exit_reason,
            "observations": len(prices),
        }

    async def _proposal(self, proposal_id: str) -> TradeProposal | None:
        for row in await self._repository.recent("trade_proposals", limit=500):
            payload = _payload(row)
            if payload.get("proposal_id") == proposal_id:
                return TradeProposal.model_validate(payload)
        return None

    async def _evaluate(self, operation_id: str, position_id: str, asset: str) -> OperationState:
        evaluation = await self._state.closed_trade_evaluation(position_id, now=self._clock.now())
        evaluation["shadow_comparison"] = await self._shadow_comparison(
            operation_id, asset, Decimal(str(evaluation["realized_net_pnl"]))
        )
        await self._repository.append(
            "trade_evaluations", evaluation, created_at=self._clock.now(), asset=asset
        )
        result = await self._lifecycle.advance(
            operation_id,
            OperationState.EVALUATED,
            reason="post_close_evaluation",
            now=self._clock.now(),
            details={"realized_net_pnl": evaluation["realized_net_pnl"]},
        )
        return result.to_state


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)
    return payload if isinstance(payload, dict) else {}


def _as_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        moment = value
    else:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
