"""Nightly AI improvement review (role ``improvement``, Opus by default).

The model reads what happened (closed trades, why entries were rejected, how
each agent's calls turned out, the active strategy settings) and suggests at
most three ideas. Each idea goes through the same gates as any change:

* ``PARAMS`` (strategy settings): bounded by ``StrategyParams``, replayed on real
  candles and judged out-of-sample with the champion/challenger rule. Only a
  proven idea becomes a proposal; in ``paper_autonomous`` mode it is promoted by
  ``auto_promote`` after new forward sessions confirm it.
* ``PROMPT`` (an agent's instructions): a new version of that agent's
  ``AGENT.md`` with a "LEARNED GUIDANCE" section. It cannot be replayed, so it
  always waits for the owner. Protected agents (critic, optimizer, …) are never
  targeted.
* ``FEATURE_REQUEST``: a tool the agents would need (e.g. to save tokens). It is
  never applied by software; the owner accepts it and it is built and reviewed.

The model never touches risk limits (``_RISK_KEYS`` guard), orders, the broker
or code. Its text is data for the owner, never an instruction to the system.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from trading_bot.agents.registry import AgentRegistry
from trading_bot.agents.runtime import AgentRuntime
from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.learning.optimizer import ChampionChallenger
from trading_bot.learning.optimizer_job import (
    MIN_OOS_TRADES,
    _metrics,
    bump_minor,
    describe_change,
    evaluate,
)
from trading_bot.learning.proposals import proposal_timeline
from trading_bot.learning.supervisor import LearningSupervisor
from trading_bot.learning.versions import PROTECTED_AGENTS, StrategyParams, VersionRepository
from trading_bot.market.clock import regular_session_only
from trading_bot.providers.base import ProviderError
from trading_bot.providers.factory import build_model_router
from trading_bot.reports.period import period_report
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import Candle
from trading_bot.simulation.costs import SIMULATION_CAPITAL_USD
from trading_bot.strategies.registry import exit_defaults, strategy_version

MAX_IDEAS = 3
LOOKBACK_DAYS = 14
HISTORY_MINUTES = 30 * 24 * 60
_OPEN_STATES = {"PROPOSED", "TESTING", "READY_FOR_REVIEW", "APPROVED"}
_VERSION_LINE = re.compile(r"(^##\s+VERSION\s*\n+\s*)([^\n]+)", re.MULTILINE)

CandleHistory = Callable[[str, int], Awaitable[tuple[Candle, ...]]]


class ImprovementIdea(StrictSchema):
    kind: Literal["PARAMS", "PROMPT", "FEATURE_REQUEST"]
    target: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=160)
    rationale: str = Field(min_length=10, max_length=2000)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=8)
    expected_improvement: str = Field(min_length=3, max_length=600)
    risk: str = Field(min_length=3, max_length=600)
    stop_percent: Decimal | None = None
    reward_multiple: Decimal | None = None
    horizon_minutes: int | None = None
    blocked_regimes: tuple[Literal["ranging", "trending_up", "trending_down"], ...] | None = None
    prompt_guidance: str | None = Field(default=None, max_length=3000)
    feature_description: str | None = Field(default=None, max_length=3000)


class ImprovementPlan(StrictSchema):
    summary: str = Field(min_length=3, max_length=2000)
    ideas: tuple[ImprovementIdea, ...] = Field(default=(), max_length=MAX_IDEAS)


def with_guidance(spec: str, guidance: str, new_version: str) -> str:
    """Append a LEARNED GUIDANCE section and bump the VERSION line."""

    bumped = _VERSION_LINE.sub(lambda match: match.group(1) + new_version, spec, count=1)
    # Model text is data: it cannot open new "## SECTION" headings (that would
    # override ROLE, RULES or VERSION), and it is never a regex template.
    safe = "\n".join(
        line.lstrip("#").strip() if line.lstrip().startswith("#") else line
        for line in guidance.strip().splitlines()
    )
    block = f"\n## LEARNED GUIDANCE\n{safe}\n"
    if "## LEARNED GUIDANCE" in bumped:
        return re.sub(
            r"## LEARNED GUIDANCE\n.*?(?=\n## |\Z)",
            lambda _match: block.strip("\n"),
            bumped,
            count=1,
            flags=re.S,
        )
    marker = bumped.find("\n## VERSION")
    return bumped[:marker] + block + bumped[marker:] if marker >= 0 else bumped + block


async def _context(settings: Settings, database: Database, clock: Clock) -> dict[str, Any]:
    audit = AuditRepository(database)
    now = clock.now()
    report = await period_report(database, start=now - timedelta(days=LOOKBACK_DAYS), end=now)
    risk_rows = await audit.recent("risk_decisions", limit=500)
    reasons: Counter[str] = Counter()
    for row in risk_rows:
        raw = row.get("payload")
        payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
        if payload.get("verdict") == "DENY":
            reasons.update(str(item) for item in payload.get("reasons") or ())
    # Latest meta-memory pass: each agent's hit rate by regime and time bucket.
    reliability: list[Any] = next(
        (
            list(row["payload"].get("agents") or ())
            for row in await audit.with_status("system_events", ["MEMORY_META"])
            if isinstance(row.get("payload"), dict)
            and row["payload"].get("status") == "MEMORY_META"
        ),
        [],
    )
    active = await VersionRepository(database).active("strategy")
    strategies = {
        strategy_id: (
            active[strategy_id]["params"]
            if strategy_id in active
            else StrategyParams.from_exit(exit_defaults(strategy_id)).model_dump(mode="json")
        )
        for strategy_id in settings.public.strategies.enabled
    }
    return {
        "period_days": LOOKBACK_DAYS,
        "results": report.summary(),
        "trades": [trade.model_dump(mode="json") for trade in report.trade_rows[-40:]],
        "rejection_reasons": dict(reasons.most_common(12)),
        "agent_reliability": reliability[:40],
        "strategies": strategies,
        "improvable_agents": sorted(
            descriptor.agent_id
            for descriptor in AgentRegistry().descriptors()
            if descriptor.agent_id not in PROTECTED_AGENTS
        ),
        "limits": {
            "params_bounds": {
                "stop_percent": "0.3-3",
                "reward_multiple": "1-5",
                "horizon_minutes": "15-1440",
            },
            "never": "risk limits, orders, broker, credentials, code",
        },
    }


async def run_ai_improvement(
    settings: Settings,
    database: Database,
    clock: Clock,
    *,
    candle_history: CandleHistory | None = None,
    runtime: AgentRuntime | None = None,
    runner: Callable[[Callable[[], Any]], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """One nightly review. Returns what happened to each idea (for the audit log)."""

    if settings.public.ai.primary_provider == "disabled" and runtime is None:
        return {"status": "skipped", "reason": "ai_disabled"}
    audit = AuditRepository(database)
    runtime = runtime or AgentRuntime(
        registry=AgentRegistry(overrides=await VersionRepository(database).agent_overrides()),
        router=build_model_router(settings),
        repository=audit,
        clock=clock,
    )
    try:
        invocation = await runtime.invoke(
            agent_id="optimizer",
            profile="improvement",
            context=await _context(settings, database, clock),
            output_schema=ImprovementPlan,
        )
    except ProviderError as exc:
        return {"status": "failed", "reason": exc.code}
    plan = invocation.result.output
    if not isinstance(plan, ImprovementPlan):
        return {"status": "failed", "reason": "invalid_structured_output"}
    open_targets = {
        str(item.get("candidate_spec", {}).get("strategy_id") or item.get("agent"))
        for item in proposal_timeline(await audit.recent("change_proposals", limit=500))
        if item.get("status") in _OPEN_STATES
    }
    supervisor = LearningSupervisor(repository=audit, clock=clock)
    outcomes = []
    for idea in plan.ideas[:MAX_IDEAS]:
        if idea.target in open_targets:
            outcomes.append({"target": idea.target, "kind": idea.kind, "outcome": "pending_review"})
            continue
        if idea.kind == "PARAMS":
            outcome = await _params_idea(
                idea, settings, database, supervisor, candle_history, runner
            )
        elif idea.kind == "PROMPT":
            outcome = await _prompt_idea(idea, database, supervisor)
        else:
            outcome = await _feature_idea(idea, supervisor)
        outcomes.append({"target": idea.target, "kind": idea.kind, **outcome})
        open_targets.add(idea.target)
    summary = {
        "status": "AI_IMPROVEMENT_RUN",
        "summary": plan.summary,
        "model": invocation.result.model,
        "ideas": outcomes,
    }
    await audit.append("system_events", summary, created_at=clock.now())
    return summary


async def _params_idea(
    idea: ImprovementIdea,
    settings: Settings,
    database: Database,
    supervisor: LearningSupervisor,
    candle_history: CandleHistory | None,
    runner: Callable[[Callable[[], Any]], Awaitable[Any]] | None,
) -> dict[str, Any]:
    strategies = settings.public.strategies
    if idea.target not in strategies.enabled:
        return {"outcome": "rejected", "reason": "unknown_strategy"}
    if candle_history is None:
        return {"outcome": "rejected", "reason": "no_market_history"}
    active = await VersionRepository(database).active("strategy")
    row = active.get(idea.target)
    current = (
        StrategyParams.model_validate(row["params"])
        if row
        else StrategyParams.from_exit(exit_defaults(idea.target))
    )
    changes = {
        key: value
        for key, value in {
            "stop_percent": idea.stop_percent,
            "reward_multiple": idea.reward_multiple,
            "horizon_minutes": idea.horizon_minutes,
            "blocked_regimes": idea.blocked_regimes,
        }.items()
        if value is not None
    }
    try:
        candidate = StrategyParams.model_validate({**current.model_dump(), **changes})
    except ValueError:
        return {"outcome": "rejected", "reason": "outside_bounds"}
    if candidate == current:
        return {"outcome": "rejected", "reason": "no_change"}
    candles = {
        symbol: regular_session_only(await candle_history(symbol, HISTORY_MINUTES))
        for symbol in settings.public.trading.allowed_symbols
    }

    def judge() -> tuple[Any, Any]:
        before = evaluate(strategies, idea.target, current, candles, capital=SIMULATION_CAPITAL_USD)
        after = evaluate(
            strategies, idea.target, candidate, candles, capital=SIMULATION_CAPITAL_USD
        )
        return before, after

    before, after = await (runner or _inline)(judge)
    champion, challenger = _metrics(before.out_of_sample), _metrics(after.out_of_sample)
    if not ChampionChallenger().ready_for_review(
        champion, challenger, minimum_samples=MIN_OOS_TRADES
    ):
        return {
            "outcome": "not_proven",
            "oos_trades": challenger.sample_size,
            "expectancy_before": str(champion.expectancy),
            "expectancy_after": str(challenger.expectancy),
        }
    current_version = str(row["version"]) if row else strategy_version(idea.target)
    proposal = await supervisor.propose(
        agent=idea.target,
        current_version=current_version,
        candidate_version=bump_minor(current_version),
        reason=f"Idea de la IA: {idea.title}. {idea.rationale}"[:2000],
        evidence=[*idea.evidence, "Validada con precios reales fuera de muestra."],
        affected_rules=describe_change(current, candidate) or ["strategy_params"],
        expected_improvement=idea.expected_improvement,
        risk=idea.risk,
        candidate_spec={
            "kind": "strategy_params",
            "strategy_id": idea.target,
            "params": candidate.model_dump(mode="json"),
            "origin": "ai_improver",
        },
    )
    proposal = await supervisor.transition(
        proposal, "TESTING", reason="Prueba automática con precios reales"
    )
    await supervisor.transition(
        proposal, "READY_FOR_REVIEW", reason="Mejora demostrada fuera de muestra"
    )
    return {"outcome": "proposed", "proposal_id": proposal.id}


async def _prompt_idea(
    idea: ImprovementIdea, database: Database, supervisor: LearningSupervisor
) -> dict[str, Any]:
    if idea.target in PROTECTED_AGENTS or not idea.prompt_guidance:
        return {"outcome": "rejected", "reason": "protected_or_empty"}
    registry = AgentRegistry(overrides=await VersionRepository(database).agent_overrides())
    known = {descriptor.agent_id: descriptor for descriptor in registry.descriptors()}
    if idea.target not in known:
        return {"outcome": "rejected", "reason": "unknown_agent"}
    descriptor, spec = registry.specification(idea.target)
    new_version = bump_minor(descriptor.version)
    proposal = await supervisor.propose(
        agent=idea.target,
        current_version=descriptor.version,
        candidate_version=new_version,
        reason=f"Idea de la IA: {idea.title}. {idea.rationale}"[:2000],
        evidence=list(idea.evidence),
        affected_rules=["agent_spec"],
        expected_improvement=idea.expected_improvement,
        risk=idea.risk,
        candidate_spec={
            "kind": "agent_spec",
            "spec_markdown": with_guidance(spec, idea.prompt_guidance, new_version),
            "origin": "ai_improver",
        },
    )
    # Instructions cannot be replayed: the owner reviews them.
    proposal = await supervisor.transition(
        proposal, "TESTING", reason="Revisión de instrucciones propuesta por la IA"
    )
    await supervisor.transition(proposal, "READY_FOR_REVIEW", reason="Esperando tu revisión")
    return {"outcome": "proposed_for_owner", "proposal_id": proposal.id}


async def _feature_idea(idea: ImprovementIdea, supervisor: LearningSupervisor) -> dict[str, Any]:
    if not idea.feature_description:
        return {"outcome": "rejected", "reason": "empty_feature"}
    proposal = await supervisor.propose(
        agent=idea.target,
        current_version="-",
        candidate_version="feature-request",
        reason=f"{idea.title}. {idea.rationale}"[:2000],
        evidence=list(idea.evidence),
        affected_rules=["feature_request"],
        expected_improvement=idea.expected_improvement,
        risk=idea.risk,
        candidate_spec={
            "kind": "feature_request",
            "title": idea.title,
            "description": idea.feature_description,
            "origin": "ai_improver",
        },
    )
    proposal = await supervisor.transition(
        proposal, "TESTING", reason="Propuesta de nueva herramienta"
    )
    await supervisor.transition(
        proposal, "READY_FOR_REVIEW", reason="Nunca se aplica sola: decide el dueño"
    )
    return {"outcome": "proposed_for_owner", "proposal_id": proposal.id}


async def _inline(task: Callable[[], Any]) -> Any:
    return task()


__all__ = [
    "ImprovementIdea",
    "ImprovementPlan",
    "run_ai_improvement",
    "with_guidance",
]
