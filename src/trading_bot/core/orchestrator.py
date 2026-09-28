from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.broker.base import SubmissionStateUnknown, round_quantity
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.lifecycle import OperationState, state_for_order
from trading_bot.broker.models import OrderResult
from trading_bot.core.agent_pipeline import SpecialistAgentPipeline
from trading_bot.core.clock import Clock
from trading_bot.core.context import AgentContextAssembler, ContextAssemblyError
from trading_bot.core.post_close import SELF_RECONCILING_MODES, PostCloseEvaluator
from trading_bot.data.features import FeatureEngine
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository
from trading_bot.intelligence.evidence import build_evidence, confluence, consensus_matrix
from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.positions import DeterministicPositionManager, PositionState
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import (
    Candle,
    ExecutionIntent,
    MarketSnapshot,
    RiskContext,
    RiskDecision,
    TradeProposal,
)
from trading_bot.simulation.shadow import ShadowComparison, ShadowSimulator
from trading_bot.strategies.base import StrategyPlugin
from trading_bot.strategies.signal_score import compute_signal_score


class CycleResult:
    def __init__(
        self,
        *,
        status: str,
        risk_decision: RiskDecision | None = None,
        order: OrderResult | None = None,
    ) -> None:
        self.status = status
        self.risk_decision = risk_decision
        self.order = order


class MasterOrchestrator:
    """Coordinates components; it owns neither broker credentials nor order methods."""

    def __init__(
        self,
        *,
        feature_engine: FeatureEngine,
        strategy: StrategyPlugin,
        critic: DeterministicCritic,
        risk_engine: RiskEngine,
        execution_engine: ExecutionEngine,
        repository: AuditRepository,
        clock: Clock,
        context_assembler: AgentContextAssembler | None = None,
        specialist_pipeline: SpecialistAgentPipeline | None = None,
        state_repository: TradingStateRepository | None = None,
        metrics: MetricsRegistry | None = None,
        signal_weights: dict[str, Decimal] | None = None,
        signal_weights_version: str | None = None,
        lifecycle_repository: OperationLifecycleRepository | None = None,
        max_position_hold: timedelta | None = None,
        eod_flatten_minutes: int = 10,
        quote_refresher: Callable[[str], Awaitable[MarketSnapshot]] | None = None,
        max_quote_age: timedelta | None = None,
    ) -> None:
        # The AI stages can take minutes: the price used by RiskEngine is read
        # again right before the decision, and an old quote denies the entry.
        self._quote_refresher = quote_refresher
        self._max_quote_age = max_quote_age
        self._max_position_hold = max_position_hold
        # With overnight disabled, open positions close this long before the bell.
        self._eod_flatten_minutes = eod_flatten_minutes
        self._feature_engine = feature_engine
        self._strategy = strategy
        self._critic = critic
        self._risk_engine = risk_engine
        self._execution_engine = execution_engine
        self._repository = repository
        self._clock = clock
        self._context_assembler = context_assembler or AgentContextAssembler()
        self._specialist_pipeline = specialist_pipeline
        self._state_repository = state_repository
        self._metrics = metrics
        self._signal_weights = signal_weights
        self._signal_weights_version = signal_weights_version
        self._lifecycle = lifecycle_repository

    async def _fresh_quote_context(
        self, snapshot: MarketSnapshot, context: RiskContext
    ) -> RiskContext:
        """Price and freshness as of the risk decision, not the start of the cycle."""

        latest = snapshot
        if self._quote_refresher is not None:
            try:
                latest = await self._quote_refresher(snapshot.symbol)
            except Exception:  # no fresh quote: fail closed
                return context.model_copy(update={"data_fresh": False})
        if self._max_quote_age is None:
            return context.model_copy(update={"market_price": latest.last})
        age = self._clock.now() - latest.event_time
        return context.model_copy(
            update={
                "market_price": latest.last,
                "data_fresh": context.data_fresh and age <= self._max_quote_age,
            }
        )

    async def run_cycle(
        self,
        snapshot: MarketSnapshot,
        prices: tuple[Decimal, ...],
        context: RiskContext,
        external_data: dict[str, Any] | None = None,
        *,
        protective_only: bool = False,
        flatten: bool = False,
        candles: tuple[Candle, ...] = (),
    ) -> CycleResult:
        """Run one cycle and account for its bounded runtime metrics.

        ``protective_only`` runs deterministic exits for open positions and never
        evaluates new entries; used for assets no longer in the trading universe.
        """

        if self._lifecycle is not None:
            unresolved = await self._lifecycle.count_unresolved()
            context = context.model_copy(update={"unresolved_operations": unresolved})
        context = context.model_copy(update={"market_price": snapshot.last})
        result = await self._run_cycle(
            snapshot,
            prices,
            context,
            external_data,
            protective_only=protective_only,
            flatten=flatten,
            candles=candles,
        )
        if self._metrics is not None:
            self._metrics.increment(
                "trading_cycles_total",
                labels={"mode": context.mode.value, "status": result.status},
            )
            if result.risk_decision is not None:
                self._metrics.increment(
                    "risk_decisions_total",
                    labels={"verdict": result.risk_decision.verdict},
                )
            if result.order is not None:
                self._metrics.increment(
                    "orders_total",
                    labels={"status": result.order.status, "mode": result.order.mode.value},
                )
        return result

    async def _run_cycle(
        self,
        snapshot: MarketSnapshot,
        prices: tuple[Decimal, ...],
        context: RiskContext,
        external_data: dict[str, Any] | None = None,
        *,
        protective_only: bool = False,
        flatten: bool = False,
        candles: tuple[Candle, ...] = (),
    ) -> CycleResult:
        await self._repository.append(
            "market_snapshots",
            snapshot,
            created_at=self._clock.now(),
            asset=snapshot.symbol,
            event_time=snapshot.event_time,
            received_time=snapshot.received_time,
            processed_time=snapshot.processed_time,
        )
        protected = await self._protect_open_positions(snapshot, context, flatten=flatten)
        if protected is not None:
            return protected
        if flatten:
            # Operator asked to go flat: never open anything new meanwhile.
            return CycleResult(status="FLATTEN_PENDING")
        if protective_only:
            return CycleResult(status="PROTECTION_ONLY")
        features = self._feature_engine.compute(snapshot.symbol, prices, candles)
        if self._specialist_pipeline is None:
            try:
                self._context_assembler.assemble(
                    agent_id="strategy",
                    snapshot=snapshot,
                    features=features,
                    now=self._clock.now(),
                )
            except ContextAssemblyError as exc:
                await self._repository.append(
                    "system_events",
                    {"status": "STALE_CONTEXT", "detail": str(exc)},
                    created_at=self._clock.now(),
                    asset=snapshot.symbol,
                )
                return CycleResult(status="STALE_CONTEXT")
        await self._repository.append(
            "features", features, created_at=self._clock.now(), asset=snapshot.symbol
        )
        pipeline_result = None
        if self._specialist_pipeline is not None:
            pipeline_result = await self._specialist_pipeline.run(
                snapshot=snapshot,
                features=features,
                now=self._clock.now(),
                external_data=external_data,
            )
            if pipeline_result.status == "FAILED":
                await self._repository.append(
                    "system_events",
                    {
                        "status": "AI_PIPELINE_FAILED",
                        "detail": pipeline_result.failure_code or "unknown",
                    },
                    created_at=self._clock.now(),
                    asset=snapshot.symbol,
                )
                return CycleResult(status="AI_PIPELINE_FAILED")
            if pipeline_result.status == "NO_TRADE":
                await self._repository.append(
                    "system_events",
                    {
                        "status": "NO_TRADE",
                        "detail": "; ".join(pipeline_result.why_not_trade),
                    },
                    created_at=self._clock.now(),
                    asset=snapshot.symbol,
                )
                return CycleResult(status="NO_TRADE")
            proposal = pipeline_result.proposal
            review = pipeline_result.critic
            if proposal is None or review is None:
                await self._repository.append(
                    "system_events",
                    {
                        "status": "AI_PIPELINE_FAILED",
                        "detail": "pipeline returned READY without proposal and critic",
                    },
                    created_at=self._clock.now(),
                    asset=snapshot.symbol,
                )
                return CycleResult(status="AI_PIPELINE_FAILED")
        else:
            # Same base risk RiskEngine will enforce (config + profit ladder), not a constant.
            base_budget = self._risk_engine.risk_budget(context)
            setter = getattr(self._strategy, "set_intelligence", None)
            if callable(setter):
                setter((external_data or {}).get("intelligence"))
            proposal = self._strategy.propose(snapshot, features, risk_budget_usd=base_budget)
            review = None
        if proposal is None:
            # Every NO_TRADE keeps its reason codes so filters can be measured (§121).
            reasons = tuple(getattr(self._strategy, "last_no_trade", ()) or ("no_setup",))
            await self._repository.append(
                "system_events",
                {"status": "NO_TRADE", "reasons": list(reasons), "detail": "; ".join(reasons)},
                created_at=self._clock.now(),
                asset=snapshot.symbol,
            )
            return CycleResult(status="NO_TRADE")
        # A tight stop can size a position larger than the exposure limits allow;
        # shrink it to fit (never grow it), so the risk only gets smaller.
        capacity = self._risk_engine.notional_capacity(context)
        if capacity > 0 and proposal.notional_usd > capacity:
            proposal = _resize(proposal, capacity / proposal.entry_price)
        # Never send a quantity the broker cannot accept; round down, never up.
        quantity = round_quantity(proposal.quantity, self._execution_engine.capabilities)
        if quantity <= 0:
            await self._repository.append(
                "system_events",
                {"status": "NO_TRADE", "detail": "QUANTITY_BELOW_MINIMUM"},
                created_at=self._clock.now(),
                asset=proposal.asset,
            )
            return CycleResult(status="NO_TRADE")
        if quantity != proposal.quantity:
            proposal = proposal.model_copy(update={"quantity": quantity})
        if not self._score_is_deterministic(proposal):
            await self._repository.append(
                "system_events",
                {
                    "status": "SIGNAL_SCORE_INVALID",
                    "detail": "proposal score does not match configured deterministic weights",
                    "weights_version": self._signal_weights_version,
                },
                created_at=self._clock.now(),
                asset=proposal.asset,
            )
            return CycleResult(status="AI_PIPELINE_FAILED")
        await self._repository.append(
            "trade_proposals", proposal, created_at=self._clock.now(), asset=proposal.asset
        )
        operation_id = proposal.proposal_id
        if self._lifecycle is not None:
            await self._lifecycle.open(
                operation_id=operation_id,
                proposal_id=proposal.proposal_id,
                asset=proposal.asset,
                mode=context.mode,
                now=self._clock.now(),
            )
        intelligence = (external_data or {}).get("intelligence")
        if intelligence:
            # Auditable evidence behind this proposal (§37, §57).
            graph = build_evidence(features, snapshot.last, intelligence, now=self._clock.now())
            await self._repository.append(
                "system_events",
                {
                    "status": "PROPOSAL_EVIDENCE",
                    "proposal_id": proposal.proposal_id,
                    "confluence": confluence(graph).model_dump(mode="json"),
                    "consensus": consensus_matrix(graph).model_dump(mode="json"),
                    "evidence": graph.model_dump(mode="json"),
                },
                created_at=self._clock.now(),
                asset=proposal.asset,
            )
        if review is None:
            review = self._critic.review(
                proposal, snapshot, intelligence=intelligence, features=features
            )
        await self._repository.append(
            "critic_reviews", review, created_at=self._clock.now(), asset=proposal.asset
        )
        await self._advance(operation_id, OperationState.CRITIC_REVIEWED, reason="critic_review")
        context = await self._fresh_quote_context(snapshot, context)
        decision = self._risk_engine.evaluate_entry(proposal, review, context)
        await self._repository.append(
            "risk_decisions", decision, created_at=self._clock.now(), asset=proposal.asset
        )
        if decision.verdict != "ALLOW":
            await self._advance(
                operation_id,
                OperationState.REJECTED,
                reason="risk_rejected",
                details={"decision_id": decision.decision_id, "reasons": list(decision.reasons)},
            )
            if context.mode in {TradingMode.PAPER, TradingMode.SHADOW}:
                shadow_result = ShadowSimulator().simulate(proposal, prices)
                await self._repository.append(
                    "shadow_trades",
                    shadow_result,
                    created_at=self._clock.now(),
                    asset=proposal.asset,
                )
                comparison = ShadowComparison.compare(
                    proposal_id=proposal.proposal_id,
                    shadow_trade_pnl_usd=shadow_result.net_pnl_usd,
                    real_trade_pnl_usd=None,
                    reason_real_not_taken="; ".join(decision.reasons),
                )
                await self._repository.append(
                    "trade_evaluations",
                    {
                        "trade_id": proposal.proposal_id,
                        "realized_net_pnl": "0",
                        "mfe_usd": str(shadow_result.mfe_usd),
                        "mae_usd": str(shadow_result.mae_usd),
                        "fees_usd": str(shadow_result.fees_usd),
                        "slippage_usd": str(shadow_result.slippage_usd),
                        "thesis_valid": shadow_result.exit_reason != "stop",
                        "signal_correct": shadow_result.net_pnl_usd > 0,
                        "agent_incremental_values": {},
                        "shadow_comparison": comparison.model_dump(mode="json"),
                        "evaluated_at": self._clock.now().isoformat(),
                    },
                    created_at=self._clock.now(),
                    asset=proposal.asset,
                )
            return CycleResult(status="REJECTED", risk_decision=decision)
        intent = ExecutionIntent(
            intent_id=str(uuid4()),
            decision_id=decision.decision_id,
            proposal_id=proposal.proposal_id,
            client_order_id=f"hyverion-{proposal.proposal_id[:20]}",
            mode=context.mode,
            asset=proposal.asset,
            side=proposal.side,
            quantity=proposal.quantity,
            limit_price=proposal.entry_price,
            stop_price=proposal.stop_price,
            target_price=proposal.target_price,
            created_at=self._clock.now(),
            # Never plan to hold past the close: the session ends the trade.
            max_hold_seconds=_cap_to_close(proposal.time_horizon_seconds, context),
        )
        if not await self._advance(
            operation_id,
            OperationState.RISK_APPROVED,
            reason="risk_allowed",
            details={"decision_id": decision.decision_id},
        ) or not await self._advance(
            operation_id,
            OperationState.EXECUTION_PENDING,
            reason="execution_intent",
            client_order_id=intent.client_order_id,
        ):
            return await self._lifecycle_violation(operation_id, proposal.asset, decision)
        try:
            order = await self._execution_engine.execute(intent, decision)
        except SubmissionStateUnknown:
            return await self._execution_unknown(
                operation_id, intent.client_order_id, proposal.asset, decision
            )
        await self._repository.append(
            "orders", order, created_at=self._clock.now(), asset=proposal.asset
        )
        await self._advance(operation_id, OperationState.SUBMITTED, reason="order_submitted")
        await self._advance(
            operation_id,
            state_for_order(order),
            reason=f"order_status:{order.status}",
            details={"order_id": order.order_id, "filled_quantity": str(order.filled_quantity)},
            position_id=order.order_id if order.filled_quantity > 0 else None,
        )
        if self._state_repository is not None:
            await self._state_repository.record_execution(
                intent=intent,
                order=order,
                now=self._clock.now(),
            )
        return CycleResult(status="EXECUTED", risk_decision=decision, order=order)

    async def _advance(
        self,
        operation_id: str | None,
        target: OperationState,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
        client_order_id: str | None = None,
        position_id: str | None = None,
    ) -> bool:
        """Record a lifecycle step; ``False`` means the operation failed closed."""

        if self._lifecycle is None or operation_id is None:
            return True
        result = await self._lifecycle.advance(
            operation_id,
            target,
            reason=reason,
            now=self._clock.now(),
            details=details,
            client_order_id=client_order_id,
            position_id=position_id,
        )
        return result.legal

    async def _lifecycle_violation(
        self, operation_id: str, asset: str, decision: RiskDecision
    ) -> CycleResult:
        await self._repository.append(
            "system_events",
            {"status": "LIFECYCLE_VIOLATION", "operation_id": operation_id},
            created_at=self._clock.now(),
            asset=asset,
        )
        return CycleResult(status="RECOVERY_REQUIRED", risk_decision=decision)

    async def _execution_unknown(
        self,
        operation_id: str | None,
        client_order_id: str,
        asset: str,
        decision: RiskDecision,
    ) -> CycleResult:
        """A timeout is never a failed order: park it in UNKNOWN for reconciliation."""

        await self._advance(
            operation_id,
            OperationState.UNKNOWN,
            reason="submission_state_unknown",
            details={"client_order_id": client_order_id},
        )
        await self._repository.append(
            "system_events",
            {
                "status": "EXECUTION_UNKNOWN",
                "operation_id": operation_id,
                "client_order_id": client_order_id,
            },
            created_at=self._clock.now(),
            asset=asset,
        )
        return CycleResult(status="EXECUTION_UNKNOWN", risk_decision=decision)

    def _score_is_deterministic(self, proposal: TradeProposal) -> bool:
        """Reject model-supplied scores that do not match the active weights."""

        if self._signal_weights is None:
            # Unit/replay callers may intentionally construct the orchestrator
            # without a production weight set. The production entrypoint always
            # supplies one from the versioned StrategiesConfig.
            return True
        if (
            self._signal_weights_version is not None
            and proposal.signal_components.weights_version != self._signal_weights_version
        ):
            return False
        try:
            computed = compute_signal_score(proposal.signal_components, self._signal_weights)
        except (TypeError, ValueError, ArithmeticError):
            return False
        return computed == proposal.signal_score

    async def _operation_for_position(self, position_id: str) -> str | None:
        if self._lifecycle is None:
            return None
        row = await self._lifecycle.find_by_position(position_id)
        # Positions opened before lifecycle tracking existed have no operation.
        return str(row["id"]) if row is not None else None

    async def _exit_in_flight(
        self, operation_id: str | None, mode: TradingMode, asset: str
    ) -> bool:
        """Return True when a new exit order must not be submitted."""

        if self._lifecycle is None or operation_id is None:
            return False
        row = await self._lifecycle.get(operation_id)
        if row is None:
            return False
        state = OperationState(row["state"])
        if mode in SELF_RECONCILING_MODES and self._execution_engine.simulated_venue:
            if state is OperationState.UNKNOWN:
                # The simulated venue is rebuilt every cycle; an unconfirmed
                # simulated exit left nothing working, so protection resumes.
                await self._advance(
                    operation_id, OperationState.OPEN, reason="simulated_exit_absent"
                )
            return False
        exit_client_order_id = await self._lifecycle.last_exit_client_order_id(operation_id)
        if exit_client_order_id is None:
            if state in {OperationState.EXIT_PENDING, OperationState.CLOSING}:
                await self._escalate_exit(operation_id, asset, "exit_order_id_missing")
                return True
            return False
        if state not in {
            OperationState.EXIT_PENDING,
            OperationState.CLOSING,
            OperationState.UNKNOWN,
            OperationState.SAFE_MODE,
            OperationState.RECOVERY_REQUIRED,
        }:
            return False
        order = await self._execution_engine.lookup(exit_client_order_id)
        if order is not None and order.status in {"OPEN", "PARTIALLY_FILLED"}:
            return True
        if state not in {OperationState.SAFE_MODE, OperationState.RECOVERY_REQUIRED}:
            # Resolved at the venue, but its fills are not in the local projection.
            await self._escalate_exit(operation_id, asset, "exit_order_resolved_unprojected")
        return True

    async def _escalate_exit(self, operation_id: str, asset: str, reason: str) -> None:
        await self._advance(operation_id, OperationState.RECOVERY_REQUIRED, reason=reason)
        await self._repository.append(
            "system_events",
            {"status": "EXIT_RECOVERY_REQUIRED", "operation_id": operation_id, "reason": reason},
            created_at=self._clock.now(),
            asset=asset,
        )

    async def _record_exit_lifecycle(
        self,
        operation_id: str | None,
        position_id: str,
        position_quantity: Decimal,
        order: OrderResult,
    ) -> None:
        details = _exit_details(order)
        # Simulated venues never leave a resting remainder; a real venue can.
        remainder_working = order.status in {"OPEN", "PARTIALLY_FILLED"} and (
            order.mode not in SELF_RECONCILING_MODES or not self._execution_engine.simulated_venue
        )
        if order.filled_quantity <= 0:
            if remainder_working:
                return  # stays EXIT_PENDING; the next cycle checks the working order
            target = (
                OperationState.UNKNOWN if order.status == "UNKNOWN" else OperationState.OPEN
            )
            await self._advance(
                operation_id, target, reason=f"exit_not_filled:{order.status}", details=details
            )
            return
        await self._advance(
            operation_id, OperationState.CLOSING, reason="exit_filled", details=details
        )
        remaining = position_quantity - order.filled_quantity
        closed = remaining <= 0
        if not closed and remainder_working:
            return  # stays CLOSING while the rest of the exit order works
        if not await self._advance(
            operation_id,
            OperationState.CLOSED if closed else OperationState.OPEN,
            reason="position_closed" if closed else "partial_exit",
            details={**details, "remaining_quantity": str(max(remaining, Decimal("0")))},
        ) or not closed:
            return
        if operation_id is not None and self._lifecycle is not None and self._state_repository:
            await PostCloseEvaluator(
                lifecycle=self._lifecycle,
                state=self._state_repository,
                repository=self._repository,
                clock=self._clock,
            ).complete(operation_id, position_id, order.asset)

    async def _protect_open_positions(
        self,
        snapshot: MarketSnapshot,
        context: RiskContext,
        *,
        flatten: bool = False,
    ) -> CycleResult | None:
        """Run deterministic exits before any new AI-dependent entry work."""

        if self._state_repository is None:
            return None
        manager = DeterministicPositionManager(self._clock, max_hold=self._max_position_hold)
        for raw in await self._state_repository.open_positions():
            if str(raw.get("asset")) != snapshot.symbol:
                continue
            try:
                position = PositionState(
                    position_id=str(raw["position_id"]),
                    asset=str(raw["asset"]),
                    side=Side(str(raw["side"]).lower()),
                    entry_price=Decimal(str(raw["entry_price"])),
                    current_price=Decimal(str(raw.get("current_price") or snapshot.last)),
                    quantity=Decimal(str(raw["quantity"])),
                    stop_price=Decimal(str(raw["stop_price"])),
                    target_price=Decimal(str(raw["target_price"])),
                    opened_at=_timestamp(raw.get("opened_at")),
                    protective_stop_active=bool(raw.get("protective_stop_active", False)),
                    max_hold_seconds=_optional_int(raw.get("max_hold_seconds")),
                )
            except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
                await self._repository.append(
                    "system_events",
                    {
                        "status": "POSITION_STATE_INVALID",
                        "detail": type(exc).__name__,
                        "position_id": raw.get("position_id"),
                    },
                    created_at=self._clock.now(),
                    asset=snapshot.symbol,
                )
                return CycleResult(status="POSITION_STATE_INVALID")
            position_decision = manager.evaluate(position, data_fresh=context.data_fresh)
            # Flatten closes at the market, so it needs a fresh price; on stale data
            # the exchange-side stop keeps protecting and the close waits a cycle.
            eod_due = context.market_session in {"POWER_HOUR", "CLOSING"} and (
                context.minutes_to_close is not None
                and context.minutes_to_close <= self._eod_flatten_minutes
            )
            if (
                (flatten or eod_due)
                and context.data_fresh
                and position_decision.action not in {"EMERGENCY_EXIT", "TAKE_PROFIT"}
            ):
                reason = "operator_flatten_requested" if flatten else "end_of_day_flatten"
                position_decision = position_decision.model_copy(
                    update={"action": "FULL_CLOSE", "reasons": (reason,)}
                )
            await self._repository.append(
                "system_events",
                {
                    "status": "POSITION_DECISION",
                    "position_id": position.position_id,
                    "action": position_decision.action,
                    "reasons": list(position_decision.reasons),
                },
                created_at=self._clock.now(),
                asset=position.asset,
            )
            if position_decision.action not in {
                "EMERGENCY_EXIT",
                "TIME_EXIT",
                "TAKE_PROFIT",
                "FULL_CLOSE",
                "PARTIAL_CLOSE",
            }:
                continue
            decision = self._risk_engine.evaluate_exit(position_decision, context)
            await self._repository.append(
                "risk_decisions",
                decision,
                created_at=self._clock.now(),
                asset=position.asset,
            )
            exit_side = Side.SELL if position.side == Side.BUY else Side.BUY
            intent = ExecutionIntent(
                intent_id=str(uuid4()),
                decision_id=decision.decision_id,
                proposal_id=position_decision.position_id,
                client_order_id=f"hyverion-exit-{position.position_id[:20]}-{uuid4().hex[:8]}",
                mode=context.mode,
                asset=position.asset,
                side=exit_side,
                quantity=position.quantity,
                limit_price=snapshot.last,
                stop_price=position.stop_price,
                target_price=position.target_price,
                created_at=self._clock.now(),
                reduce_only=True,
                position_id=position.position_id,
                exit_reason=position_decision.reasons[0],
            )
            operation_id = await self._operation_for_position(position.position_id)
            if await self._exit_in_flight(operation_id, context.mode, position.asset):
                # A previous exit order may still be working at the venue; never
                # send a second, overlapping reduce-only order for the same units.
                continue
            if not await self._advance(
                operation_id,
                OperationState.EXIT_PENDING,
                reason=f"exit:{position_decision.action}",
                details={
                    "decision_id": decision.decision_id,
                    "client_order_id": intent.client_order_id,
                },
            ):
                return await self._lifecycle_violation(
                    str(operation_id), position.asset, decision
                )
            try:
                order = await self._execution_engine.execute(intent, decision)
            except SubmissionStateUnknown:
                return await self._execution_unknown(
                    operation_id, intent.client_order_id, position.asset, decision
                )
            await self._repository.append(
                "orders", order, created_at=self._clock.now(), asset=position.asset
            )
            await self._state_repository.record_execution(
                intent=intent,
                order=order,
                now=self._clock.now(),
            )
            await self._record_exit_lifecycle(
                operation_id, position.position_id, position.quantity, order
            )
            return CycleResult(status="POSITION_EXITED", risk_decision=decision, order=order)
        return None


def _optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    parsed = int(str(value))
    if parsed <= 0:
        raise ValueError("max_hold_seconds must be positive")
    return parsed


def _exit_details(order: OrderResult) -> dict[str, Any]:
    return {"order_id": order.order_id, "filled_quantity": str(order.filled_quantity)}


def _cap_to_close(horizon_seconds: int, context: RiskContext) -> int:
    if context.minutes_to_close is None:
        return horizon_seconds
    return max(60, min(horizon_seconds, context.minutes_to_close * 60))


def default_paper_context(equity: Decimal) -> RiskContext:
    return RiskContext(
        mode=TradingMode.PAPER,
        equity=equity,
        account_high_water_mark=equity,
        realized_net_pnl_today=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        intraday_peak_realized_pnl=Decimal("0"),
        intraday_peak_total_pnl=Decimal("0"),
        fees_today=Decimal("0"),
        realized_net_pnl_week=Decimal("0"),
        current_exposure_usd=Decimal("0"),
        asset_exposure_usd=Decimal("0"),
        correlated_open_risk_usd=Decimal("0"),
        open_remaining_risk_usd=Decimal("0"),
        open_positions=0,
        losing_streak=0,
        market_session="REGULAR",
        minutes_since_open=60,
        minutes_to_close=240,
    )


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("position timestamp is missing")
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("position timestamp must be timezone-aware")
    return timestamp.astimezone(UTC)


def _resize(proposal: TradeProposal, quantity: Decimal) -> TradeProposal:
    """Smaller copy of a proposal: costs and expected value scale with size."""

    ratio = quantity / proposal.quantity
    return proposal.model_copy(
        update={
            "quantity": quantity,
            "expected_fees_usd": proposal.expected_fees_usd * ratio,
            "estimated_slippage_usd": proposal.estimated_slippage_usd * ratio,
            "expected_net_value_usd": proposal.expected_net_value_usd * ratio,
        }
    )
