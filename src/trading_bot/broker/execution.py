from __future__ import annotations

from datetime import timedelta
from typing import cast

from trading_bot.broker.base import (
    BrokerCapabilities,
    CancelReplacePort,
    ExecutionResultPort,
    SubmissionStateUnknown,
)
from trading_bot.broker.models import OrderResult
from trading_bot.broker.simulator import SIMULATOR_CAPABILITIES
from trading_bot.core.clock import Clock
from trading_bot.core.instruments import SYMBOL_NOT_EXECUTION_WHITELISTED, is_executable
from trading_bot.schemas.common import Side
from trading_bot.schemas.trading import ExecutionIntent, RiskDecision


class ExecutionEngine:
    """The only application boundary authorized to call a private execution adapter."""

    def __init__(
        self,
        adapter: ExecutionResultPort,
        *,
        clock: Clock | None = None,
        max_decision_age: timedelta = timedelta(seconds=60),
    ) -> None:
        self._adapter = adapter
        self._clock = clock
        self._max_decision_age = max_decision_age
        # A risk decision authorizes exactly one order; a replay with a new
        # client_order_id must not open a second position from the same approval.
        self._consumed_decisions: set[str] = set()

    def _authorize(self, intent: ExecutionIntent, decision: RiskDecision) -> None:
        """Fail closed unless the decision authorizes exactly this intent."""

        # Second, independent check of the QQQ-only whitelist (RiskEngine is the first).
        if not is_executable(intent.asset):
            raise PermissionError(SYMBOL_NOT_EXECUTION_WHITELISTED)
        allowed_verdict = decision.verdict == "ALLOW" or (
            decision.verdict == "EXIT_ONLY" and intent.reduce_only
        )
        if not allowed_verdict or decision.decision_id != intent.decision_id:
            raise PermissionError("execution requires a matching risk decision")
        age = self._clock.now() - decision.decided_at if self._clock is not None else None
        if age is not None and age > self._max_decision_age:
            raise PermissionError("risk decision expired; re-evaluate before executing")
        if intent.reduce_only:
            return
        if (
            decision.approved_asset is None
            or decision.approved_side is None
            or decision.approved_quantity is None
            or decision.approved_notional_usd is None
        ):
            raise PermissionError("risk decision does not state what it authorizes")
        if intent.asset != decision.approved_asset or intent.side != decision.approved_side:
            raise PermissionError("intent asset/side differs from the risk decision")
        if intent.quantity > decision.approved_quantity:
            raise PermissionError("intent quantity exceeds the risk decision")
        if intent.quantity * intent.limit_price > decision.approved_notional_usd:
            raise PermissionError("intent notional exceeds the risk decision")
        stop = decision.approved_stop_price
        if stop is not None:
            # A wider stop would risk more than RiskEngine allowed.
            long = intent.side == Side.BUY
            wider = intent.stop_price < stop if long else intent.stop_price > stop
            if wider:
                raise PermissionError("intent stop is wider than the risk decision")

    @property
    def capabilities(self) -> BrokerCapabilities:
        declared = getattr(self._adapter, "capabilities", None)
        return declared if isinstance(declared, BrokerCapabilities) else SIMULATOR_CAPABILITIES

    @property
    def simulated_venue(self) -> bool:
        """True only for the in-process simulator, whose orders vanish on restart.

        A PAPER account at a real broker (Alpaca Paper) is an external venue:
        a missing lookup there needs a human, never an assumption.
        """

        return bool(getattr(self._adapter, "simulated", False))

    async def lookup(self, client_order_id: str) -> OrderResult | None:
        """Read-only order query used by restart recovery; it never mutates state."""

        if not client_order_id.strip():
            raise ValueError("client_order_id is required")
        return await self._adapter.lookup(client_order_id)

    async def execute(self, intent: ExecutionIntent, decision: RiskDecision) -> OrderResult:
        self._authorize(intent, decision)
        existing = await self._adapter.lookup(intent.client_order_id)
        if existing is not None:
            return existing
        if decision.decision_id in self._consumed_decisions:
            raise PermissionError("risk decision already used for another order")
        self._consumed_decisions.add(decision.decision_id)
        try:
            return await self._adapter.submit(intent)
        except SubmissionStateUnknown:
            reconciled = await self._adapter.lookup(intent.client_order_id)
            if reconciled is None:
                raise
            return reconciled

    async def cancel_replace(
        self,
        *,
        previous_client_order_id: str,
        intent: ExecutionIntent,
        decision: RiskDecision,
    ) -> OrderResult:
        """Replace a known open order without blind cancellation or resubmission."""

        self._authorize(intent, decision)
        if not previous_client_order_id.strip():
            raise ValueError("previous_client_order_id is required")
        previous = await self._adapter.lookup(previous_client_order_id)
        if previous is None:
            raise ValueError("previous order was not found; refusing blind replace")
        if previous.status not in {"OPEN", "PARTIALLY_FILLED"}:
            raise ValueError("only an open or partially filled order can be replaced")
        if not callable(getattr(self._adapter, "cancel_replace", None)):
            raise RuntimeError("adapter does not expose a cancel/replace capability")
        try:
            return await cast(CancelReplacePort, self._adapter).cancel_replace(
                previous_client_order_id=previous_client_order_id,
                intent=intent,
            )
        except SubmissionStateUnknown:
            reconciled = await self._adapter.lookup(intent.client_order_id)
            if reconciled is None:
                raise
            return reconciled
