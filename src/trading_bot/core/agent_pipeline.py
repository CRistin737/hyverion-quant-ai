"""Typed specialist-agent fan-out for the Master Orchestrator.

The pipeline is deliberately optional: PAPER can run with the deterministic
strategy when no provider is configured, while a configured provider must pass
every specialist schema before a proposal reaches RiskEngine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, cast
from uuid import uuid4

from trading_bot.agents.runtime import AgentRuntime
from trading_bot.agents.tools import tool_view
from trading_bot.core.clock import FixedClock
from trading_bot.core.context import (
    AgentContextAssembler,
    AssessmentBundle,
    ContextAssemblyError,
)
from trading_bot.data.features import FeatureSet
from trading_bot.db.repositories import AuditRepository
from trading_bot.market.clock import MarketClock
from trading_bot.memory.gateway import CAPSULE_BUDGETS, MemoryGateway
from trading_bot.memory.meta import ATTRIBUTION_TABLE, attribution_payload
from trading_bot.providers.base import ProviderError
from trading_bot.schemas.assessments import (
    CriticAssessment,
    MarketAssessment,
    NewsAssessment,
    RegimeAssessment,
    SensorAssessment,
    SocialAssessment,
    TechnicalAssessment,
)
from trading_bot.schemas.trading import MarketSnapshot, StrategyOutput, TradeProposal

# Which model role each agent runs on. Decision agents get the strongest model
# (Opus by default); everything that reads and summarises runs on "analysis".
# position_manager and session_guardian are deterministic today; they are listed
# so that an AI version of them would run on the decision model too.
DECISION_AGENTS = frozenset({"strategy", "critic", "position_manager", "session_guardian"})
IMPROVEMENT_AGENTS = frozenset({"optimizer"})
# QQQ sensors (§37-§39). Each runs only when its intelligence slice exists, in
# parallel, and a failed sensor is recorded as unavailable instead of failing
# the cycle: missing context lowers confidence, it is never invented.
SENSOR_AGENTS = ("breadth", "mega_cap", "macro", "rates", "earnings_sec", "volatility_options")


def role_for(agent_id: str) -> str:
    if agent_id in DECISION_AGENTS:
        return "decision"
    if agent_id in IMPROVEMENT_AGENTS:
        return "improvement"
    return "analysis"


@dataclass(frozen=True, slots=True)
class SpecialistPipelineResult:
    status: Literal["READY", "NO_TRADE", "FAILED"]
    assessments: AssessmentBundle
    proposal: TradeProposal | None = None
    critic: CriticAssessment | None = None
    failure_code: str | None = None
    why_not_trade: tuple[str, ...] = ()


class SpecialistAgentPipeline:
    """Run specialists in dependency order and fail closed on provider errors."""

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        context_assembler: AgentContextAssembler | None = None,
        profile_by_agent: dict[str, str] | None = None,
        memory_gateway: MemoryGateway | None = None,
        audit: AuditRepository | None = None,
        social_enabled: bool = True,
        sensors_enabled: bool = True,
        deadline_seconds: float = 240.0,
    ) -> None:
        # Whole-pipeline budget: a stuck provider must not hold the engine
        # (and its position checks) for many minutes.
        self._deadline = deadline_seconds
        self._social_enabled = social_enabled
        self._sensors_enabled = sensors_enabled
        self._runtime = runtime
        self._memory = memory_gateway
        # Specialist stances are recorded per proposal so meta-memory can score them.
        self._audit = audit
        # Memory usage is recorded under a per-run id, then linked to the proposal.
        self._decision_id: str | None = None
        self._context_assembler = context_assembler or AgentContextAssembler()
        self._profiles = profile_by_agent or {}

    async def run(
        self,
        *,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        now: datetime,
        external_data: dict[str, Any] | None = None,
    ) -> SpecialistPipelineResult:
        """Run every specialist within the deadline; running out of time is NO trade."""

        try:
            async with asyncio.timeout(self._deadline):
                return await self._run(
                    snapshot=snapshot, features=features, now=now, external_data=external_data
                )
        except TimeoutError:
            return SpecialistPipelineResult(
                status="FAILED",
                assessments=AssessmentBundle(),
                failure_code="pipeline_deadline_exceeded",
                why_not_trade=("specialist_pipeline_failed",),
            )

    async def _run(
        self,
        *,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        now: datetime,
        external_data: dict[str, Any] | None = None,
    ) -> SpecialistPipelineResult:
        bundle = AssessmentBundle()
        self._decision_id = f"cycle-{uuid4().hex}"
        try:
            market = await self._invoke(
                "market", snapshot, features, bundle, now, MarketAssessment, external_data
            )
            bundle = AssessmentBundle(market=cast(MarketAssessment, market))
            technical = await self._invoke(
                "technical", snapshot, features, bundle, now, TechnicalAssessment, external_data
            )
            bundle = AssessmentBundle(
                market=bundle.market, technical=cast(TechnicalAssessment, technical)
            )
            regime = await self._invoke(
                "regime", snapshot, features, bundle, now, RegimeAssessment, external_data
            )
            bundle = AssessmentBundle(
                market=bundle.market,
                technical=bundle.technical,
                regime=cast(RegimeAssessment, regime),
            )
            # News, social and the QQQ sensors are independent: run them together.
            news_task = self._invoke(
                "news", snapshot, features, bundle, now, NewsAssessment, external_data
            )
            social_task = (
                self._invoke(
                    "social", snapshot, features, bundle, now, SocialAssessment, external_data
                )
                if self._social_enabled
                else _none()
            )
            news, social, sensors = await _gather_or_cancel(
                news_task,
                social_task,
                self._sensors(snapshot, features, bundle, now, external_data),
            )
            bundle = AssessmentBundle(
                market=bundle.market,
                technical=bundle.technical,
                regime=bundle.regime,
                news=cast(NewsAssessment, news),
                social=cast(SocialAssessment | None, social),
                sensors=sensors,
            )
            strategy = cast(
                StrategyOutput,
                await self._invoke(
                    "strategy", snapshot, features, bundle, now, StrategyOutput, external_data
                ),
            )
            if strategy.decision == "NO_TRADE":
                return SpecialistPipelineResult(
                    status="NO_TRADE",
                    assessments=bundle,
                    why_not_trade=strategy.why_not_trade,
                )
            proposal = strategy.proposal
            if proposal is None:
                return SpecialistPipelineResult(
                    status="FAILED",
                    assessments=bundle,
                    failure_code="strategy_missing_proposal",
                    why_not_trade=("strategy_missing_proposal",),
                )
            bundle = replace(bundle, proposal=proposal)
            critic = cast(
                CriticAssessment,
                await self._invoke(
                    "critic", snapshot, features, bundle, now, CriticAssessment, external_data
                ),
            )
            if self._memory is not None:
                await self._memory.link_decision(self._decision_id, proposal.proposal_id)
            if self._audit is not None:
                await self._audit.append(
                    ATTRIBUTION_TABLE,
                    attribution_payload(
                        proposal=proposal,
                        critic=critic,
                        market=bundle.market,
                        regime=bundle.regime,
                        news=bundle.news,
                        social=bundle.social,
                        context=_decision_context(now, external_data),
                    ),
                    created_at=now,
                    asset=proposal.asset,
                )
            return SpecialistPipelineResult(
                status="READY",
                assessments=bundle,
                proposal=proposal,
                critic=critic,
            )
        except (ProviderError, ContextAssemblyError) as exc:
            failure_code = (
                exc.code if isinstance(exc, ProviderError) else "context_assembly_failed"
            )
            return SpecialistPipelineResult(
                status="FAILED",
                assessments=bundle,
                failure_code=failure_code,
                why_not_trade=("specialist_pipeline_failed",),
            )

    async def _sensors(
        self,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        bundle: AssessmentBundle,
        now: datetime,
        external_data: dict[str, Any] | None,
    ) -> tuple[SensorAssessment, ...]:
        intelligence = (external_data or {}).get("intelligence")
        if not self._sensors_enabled or not intelligence:
            return ()
        active = [agent for agent in SENSOR_AGENTS if tool_view(agent, intelligence)]
        results = await asyncio.gather(
            *(
                self._invoke(
                    agent, snapshot, features, bundle, now, SensorAssessment, external_data
                )
                for agent in active
            ),
            return_exceptions=True,
        )
        sensors: list[SensorAssessment] = []
        for result in results:
            if isinstance(result, SensorAssessment):
                sensors.append(result)
            elif isinstance(result, (ProviderError, ContextAssemblyError)):
                continue  # recorded by AgentRuntime as a failed run
            elif isinstance(result, BaseException):
                raise result
        return tuple(sensors)

    async def _invoke(
        self,
        agent_id: str,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        bundle: AssessmentBundle,
        now: datetime,
        output_schema: type[Any],
        external_data: dict[str, Any] | None,
    ) -> Any:
        context = self._context_assembler.assemble(
            agent_id=agent_id,
            snapshot=snapshot,
            features=features,
            assessments=bundle,
            now=now,
        )
        if external_data and agent_id in {"news", "social", "strategy", "critic"}:
            context["external_data"] = {
                key: value for key, value in external_data.items() if key != "intelligence"
            }
        if external_data and external_data.get("intelligence"):
            # Least privilege (§60, §144): each agent sees only its tools' slices.
            granted = tool_view(agent_id, external_data["intelligence"])
            if granted:
                context["intelligence"] = granted
        if self._memory is not None and agent_id in CAPSULE_BUDGETS:
            regime = bundle.regime.regime.value if bundle.regime is not None else None
            capsule = await self._memory.context(
                agent_id=agent_id,
                decision_id=self._decision_id,
                query=f"{agent_id} decision for {snapshot.symbol} in {regime or 'unknown'} regime",
                as_of=now,
                symbol=snapshot.symbol,
                market_regime=regime,
            )
            # Bounded, sanitized and explicitly labelled as untrusted evidence.
            context["memory"] = capsule.to_context()
        invocation = await self._runtime.invoke(
            agent_id=agent_id,
            profile=self._profiles.get(agent_id, role_for(agent_id)),
            context=context,
            output_schema=output_schema,
        )
        return _bind_output(invocation.result.output, agent_id, snapshot.symbol, bundle)


async def _none() -> None:
    return None


async def _gather_or_cancel(*awaitables: Awaitable[Any]) -> list[Any]:
    """Like gather, but one failure cancels the rest (no orphan AI calls)."""

    tasks = [asyncio.ensure_future(item) for item in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


def _bind_output(output: Any, agent_id: str, symbol: str, bundle: AssessmentBundle) -> Any:
    """Tie a model's answer to the call that produced it.

    Identifiers written by the model are not trusted: the agent id (and the
    critic's proposal id) are set from the call, and an answer about another
    asset is rejected instead of being used.
    """

    if getattr(output, "asset", symbol) != symbol:
        raise ProviderError("invalid_structured_output", retryable=False, detail="asset")
    updates: dict[str, Any] = {}
    if hasattr(output, "agent_id") and output.agent_id != agent_id:
        updates["agent_id"] = agent_id
    if isinstance(output, CriticAssessment) and bundle.proposal is not None:
        if output.proposal_id != bundle.proposal.proposal_id:
            updates["proposal_id"] = bundle.proposal.proposal_id
    return output.model_copy(update=updates) if updates else output


def _decision_context(now: datetime, external_data: dict[str, Any] | None) -> dict[str, str]:
    """Equity metadata for memory and per-context reliability (§54, §92)."""

    snapshot = MarketClock(FixedClock(now)).snapshot()
    macro = ((external_data or {}).get("intelligence") or {}).get("macro") or {}
    near = [
        str(event.get("event_type"))
        for event in macro.get("upcoming") or ()
        if event.get("importance") == "HIGH"
        and 0 <= int(event.get("minutes_to_event") or -1) <= 240
    ]
    return {
        "time_bucket": snapshot.state.value,
        "macro_context": near[0] if near else ("GATED" if macro.get("gate") else "CLEAR"),
    }
