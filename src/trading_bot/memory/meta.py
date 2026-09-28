"""Meta-memory: which agents and which memories actually help (deterministic).

Three advisory reports, derived only from persisted audit evidence:

- Agent reliability by market regime: the stance each specialist recorded for
  a proposal is scored against the evaluated outcome of that proposal.
- Memory Value Score: how often a memory shown for a decision pointed the
  right way, blended with the PnL lift of those decisions over the baseline.
- Counterfactual memory: for risk-rejected proposals, shadow replay shows the
  loss a memory was present to help avoid, or the profit it helped forgo.

Nothing here feeds RiskEngine, sizing or a limit. Rates are Laplace-smoothed
so thin evidence stays near 0.5 instead of looking certain.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.models import KnowledgeStatus, MemoryType, StrategicMemory
from trading_bot.memory.repository import SqlMemoryRepository
from trading_bot.schemas.assessments import (
    CriticAssessment,
    MarketAssessment,
    NewsAssessment,
    RegimeAssessment,
    SocialAssessment,
)
from trading_bot.schemas.common import MarketRegime, Side
from trading_bot.schemas.trading import TradeProposal

ATTRIBUTION_TABLE = "decision_attributions"
HIT_RATE_WEIGHT = 0.7
LIFT_WEIGHT = 0.3
MAX_ROWS = 500
_NEGATIVE_TYPES = frozenset({MemoryType.FAILURE})
_ALL_STATUSES = frozenset(KnowledgeStatus)

Stance = Literal["LONG", "SHORT", "NEUTRAL", "APPROVE", "REVISE", "REJECT"]


def attribution_payload(
    *,
    proposal: TradeProposal,
    critic: CriticAssessment,
    market: MarketAssessment | None,
    regime: RegimeAssessment | None,
    news: NewsAssessment | None,
    social: SocialAssessment | None,
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The per-proposal record of what each directional specialist believed.

    ``context`` carries the equity metadata of §54 (instrument, session time
    bucket, macro context) so reliability is measured *by context* (§92).
    """

    stances: dict[str, dict[str, str]] = {
        "strategy": _stance("LONG" if proposal.side is Side.BUY else "SHORT", None),
        "critic": _stance(critic.verdict, critic.confidence),
    }
    if market is not None:
        stances["market"] = _stance(_direction(market.structure), market.confidence)
    if regime is not None:
        stances["regime"] = _stance(_regime_direction(regime.regime), regime.confidence)
    if news is not None:
        stances["news"] = _stance(_direction(news.sentiment), news.confidence)
    if social is not None:
        stances["social"] = _stance(_direction(social.sentiment), social.confidence)
    return {
        "decision_id": proposal.proposal_id,
        "side": proposal.side.value,
        "market_regime": regime.regime.value if regime is not None else "UNKNOWN",
        "time_bucket": (context or {}).get("time_bucket", "UNKNOWN"),
        "macro_context": (context or {}).get("macro_context", "UNKNOWN"),
        "instrument": proposal.asset,
        "stances": stances,
    }


@dataclass(frozen=True, slots=True)
class AgentReliability:
    agent_id: str
    market_regime: str
    samples: int
    correct: int
    reliability: Decimal
    # "ALL" rows aggregate every bucket; others split by New York session time.
    time_bucket: str = "ALL"


@dataclass(frozen=True, slots=True)
class MemoryValue:
    memory_id: str
    knowledge_id: str
    title: str
    status: str
    uses: int
    helpful: int
    hit_rate: Decimal
    lift_usd: Decimal
    value_score: Decimal
    shadow_uses: int
    avoided_loss_usd: Decimal
    forgone_profit_usd: Decimal


@dataclass(slots=True)
class MetaMemoryReport:
    outcomes_considered: int = 0
    agents: list[AgentReliability] = field(default_factory=list)
    memories: list[MemoryValue] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Outcome:
    pnl: Decimal
    shadow: bool


class MetaMemoryAnalyzer:
    def __init__(
        self, *, repository: SqlMemoryRepository, audit: AuditRepository, clock: Clock
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._clock = clock

    async def evaluate(self) -> MetaMemoryReport:
        """Compute every score without writing anything (dashboard and CLI reads)."""

        outcomes = await self._outcomes()
        report = MetaMemoryReport(outcomes_considered=len(outcomes))
        report.agents = await self.agent_reliability(outcomes)
        report.memories = await self.memory_values(outcomes)
        return report

    async def run(self) -> MetaMemoryReport:
        report = await self.evaluate()
        await self._audit.append(
            "system_events",
            {
                "status": "MEMORY_META",
                "outcomes_considered": report.outcomes_considered,
                "agents": [
                    {
                        "agent_id": item.agent_id,
                        "market_regime": item.market_regime,
                        "time_bucket": item.time_bucket,
                        "samples": item.samples,
                        "reliability": str(item.reliability),
                    }
                    for item in report.agents
                ],
                "memories": [
                    {
                        "memory_id": item.memory_id,
                        "uses": item.uses,
                        "value_score": str(item.value_score),
                        "avoided_loss_usd": str(item.avoided_loss_usd),
                        "forgone_profit_usd": str(item.forgone_profit_usd),
                    }
                    for item in report.memories[:100]
                ],
            },
            created_at=self._clock.now(),
        )
        return report

    async def agent_reliability(
        self, outcomes: dict[str, _Outcome]
    ) -> list[AgentReliability]:
        tallies: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0])
        seen: set[str] = set()
        for row in await self._audit.recent(ATTRIBUTION_TABLE, limit=MAX_ROWS):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            decision_id = str(payload.get("decision_id") or "")
            outcome = outcomes.get(decision_id)
            stances = payload.get("stances")
            if outcome is None or decision_id in seen or not isinstance(stances, dict):
                continue
            seen.add(decision_id)
            side = str(payload.get("side") or "")
            regime = str(payload.get("market_regime") or "UNKNOWN")[:40]
            bucket = str(payload.get("time_bucket") or "UNKNOWN")[:40]
            for agent_id, entry in sorted(stances.items()):
                stance = entry.get("stance") if isinstance(entry, dict) else None
                correct = stance_correct(str(stance), side, outcome.pnl)
                if correct is None:
                    continue
                agent = str(agent_id)[:64]
                for key in ((agent, regime, "ALL"), (agent, regime, bucket)):
                    tally = tallies[key]
                    tally[0] += 1
                    tally[1] += 1 if correct else 0
        return [
            AgentReliability(
                agent_id=agent_id,
                market_regime=regime,
                samples=samples,
                correct=correct,
                reliability=_laplace(correct, samples),
                time_bucket=bucket,
            )
            for (agent_id, regime, bucket), (samples, correct) in sorted(tallies.items())
        ]

    async def memory_values(self, outcomes: dict[str, _Outcome]) -> list[MemoryValue]:
        if not outcomes:
            return []
        shown: dict[str, list[str]] = defaultdict(list)
        for trade_id in sorted(outcomes):
            for memory_id in {
                usage.memory_id for usage in await self._repository.usage_for_decision(trade_id)
            }:
                shown[memory_id].append(trade_id)
        if not shown:
            return []
        pnls = [outcome.pnl for outcome in outcomes.values()]
        baseline = sum(pnls, Decimal("0")) / len(pnls)
        scale = sum((abs(pnl) for pnl in pnls), Decimal("0")) / len(pnls) or Decimal("1")
        memories = {
            memory.id: memory
            for memory in await self._repository.list_strategic(_ALL_STATUSES, limit=MAX_ROWS)
        }
        values = [
            _value(memory, [outcomes[trade_id] for trade_id in trades], baseline, scale)
            for memory_id, trades in shown.items()
            if (memory := memories.get(memory_id)) is not None
        ]
        return sorted(values, key=lambda item: (-item.value_score, item.memory_id))

    async def _outcomes(self) -> dict[str, _Outcome]:
        outcomes: dict[str, _Outcome] = {}
        for row in await self._audit.recent("trade_evaluations", limit=MAX_ROWS):
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            trade_id = str(payload.get("trade_id") or "")
            shadow = payload.get("shadow_comparison")
            pnl = _decimal(
                shadow.get("shadow_trade_pnl_usd")
                if isinstance(shadow, dict)
                else payload.get("realized_net_pnl")
            )
            if trade_id and pnl is not None and trade_id not in outcomes:
                outcomes[trade_id] = _Outcome(pnl=pnl, shadow=isinstance(shadow, dict))
        return outcomes


def _value(
    memory: StrategicMemory, outcomes: list[_Outcome], baseline: Decimal, scale: Decimal
) -> MemoryValue:
    warning = memory.category in _NEGATIVE_TYPES
    # A warning is right when the decision it was shown for lost money.
    helpful = sum(1 for outcome in outcomes if (outcome.pnl <= 0) == warning)
    hit_rate = _laplace(helpful, len(outcomes))
    lift = sum((outcome.pnl for outcome in outcomes), Decimal("0")) / len(outcomes) - baseline
    signed_lift = -lift if warning else lift
    lift_score = 0.5 + 0.5 * math.tanh(float(signed_lift / scale))
    shadow = [outcome.pnl for outcome in outcomes if outcome.shadow]
    return MemoryValue(
        memory_id=memory.id,
        knowledge_id=memory.knowledge_id,
        title=memory.title,
        status=memory.status.value,
        uses=len(outcomes),
        helpful=helpful,
        hit_rate=hit_rate,
        lift_usd=lift.quantize(Decimal("0.01")),
        value_score=_unit(HIT_RATE_WEIGHT * float(hit_rate) + LIFT_WEIGHT * lift_score),
        shadow_uses=len(shadow),
        avoided_loss_usd=sum((-pnl for pnl in shadow if pnl < 0), Decimal("0")).quantize(
            Decimal("0.01")
        ),
        forgone_profit_usd=sum((pnl for pnl in shadow if pnl > 0), Decimal("0")).quantize(
            Decimal("0.01")
        ),
    )


def stance_correct(stance: str, side: str, pnl: Decimal) -> bool | None:
    won = pnl > 0
    if stance == "APPROVE":
        return won
    if stance in {"REVISE", "REJECT"}:
        return not won
    if stance in {"LONG", "SHORT"} and side in {Side.BUY.value, Side.SELL.value}:
        agrees = (stance == "LONG") == (side == Side.BUY.value)
        return won if agrees else not won
    return None  # neutral or unknown stances make no claim to score


def _stance(stance: Stance, confidence: Decimal | None) -> dict[str, str]:
    entry: dict[str, str] = {"stance": stance}
    if confidence is not None:
        entry["confidence"] = str(confidence)
    return entry


def _direction(label: str) -> Stance:
    if label == "bullish":
        return "LONG"
    if label == "bearish":
        return "SHORT"
    return "NEUTRAL"


def _regime_direction(regime: MarketRegime) -> Stance:
    if regime is MarketRegime.TRENDING_UP:
        return "LONG"
    if regime is MarketRegime.TRENDING_DOWN:
        return "SHORT"
    return "NEUTRAL"


def _laplace(successes: int, samples: int) -> Decimal:
    return Decimal(str(round((successes + 1) / (samples + 2), 4)))


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _unit(value: float) -> Decimal:
    return Decimal(str(round(max(0.0, min(1.0, value)), 4)))


__all__ = [
    "ATTRIBUTION_TABLE",
    "AgentReliability",
    "MemoryValue",
    "MetaMemoryAnalyzer",
    "MetaMemoryReport",
    "attribution_payload",
]
