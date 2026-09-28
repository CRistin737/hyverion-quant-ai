"""Knowledge decay, contradiction detection and revalidation (deterministic).

Knowledge earns or loses reliability only from evaluated outcomes of the
operations it was shown for. Reliability is a Bayesian blend of the confidence
it was promoted with and its real track record, discounted when stale:

    reliability = (confidence * PRIOR_WEIGHT + successes) / (PRIOR_WEIGHT + uses) * staleness

ACTIVE -> NEEDS_REVALIDATION -> RETIRED when it stops working or grows stale;
NEEDS_REVALIDATION -> ACTIVE when fresh outcomes restore it. Retirement is
final and nothing is ever deleted. Contradicting active knowledge is recorded
as a conflict and the weaker side is sent to revalidation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any
from uuid import uuid4

from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.models import (
    KnowledgeStatus,
    MemoryConflict,
    MemoryOutcome,
    MemoryType,
    StrategicMemory,
)
from trading_bot.memory.ports import VaultRepository
from trading_bot.memory.repository import SqlMemoryRepository

PRIOR_WEIGHT = 5
STALE_AFTER_DAYS = 90
STALE_HALF_LIFE_DAYS = 180
REVALIDATE_BELOW = Decimal("0.35")
RETIRE_BELOW = Decimal("0.20")
RESTORE_AT = Decimal("0.50")
MIN_USES_TO_DEMOTE = 5
MIN_USES_TO_RETIRE = 10
RETIRE_WHEN_STALE_DAYS = 365

# Titles are compared without their polarity word to find opposing claims.
_POLARITY = {
    "win": 1, "wins": 1, "won": 1, "profitable": 1, "works": 1, "work": 1, "gain": 1,
    "lose": -1, "loses": -1, "lost": -1, "fail": -1, "fails": -1, "failed": -1,
    "unprofitable": -1, "underperform": -1, "underperforms": -1,
}
_NEGATIVE_TYPES = frozenset({MemoryType.FAILURE})
_SKIP_WORDS = frozenset({"mostly", "often", "usually", "trades", "trade"})


@dataclass(slots=True)
class LifecycleReport:
    outcomes_recorded: int = 0
    demoted: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)


class KnowledgeLifecycleManager:
    def __init__(
        self,
        *,
        repository: SqlMemoryRepository,
        audit: AuditRepository,
        clock: Clock,
        vault: VaultRepository | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._clock = clock
        self._vault = vault

    async def run(self) -> LifecycleReport:
        report = LifecycleReport()
        report.outcomes_recorded = await self.record_outcomes()
        await self.apply_decay(report)
        await self.detect_conflicts(report)
        now = self._clock.now()
        await self._audit.append(
            "system_events",
            {
                "status": "MEMORY_LIFECYCLE",
                "outcomes_recorded": report.outcomes_recorded,
                "demoted": report.demoted,
                "restored": report.restored,
                "retired": report.retired,
                "conflicts": [list(pair) for pair in report.conflicts],
            },
            created_at=now,
        )
        return report

    async def record_outcomes(self) -> int:
        """Credit or debit each memory shown for an evaluated operation, exactly once."""

        recorded = 0
        now = self._clock.now()
        seen: set[str] = set()
        for row in await self._audit.recent("trade_evaluations", limit=500):
            payload = row.get("payload")
            if not isinstance(payload, dict) or "shadow_comparison" in payload:
                continue  # only real, executed trades can validate knowledge
            trade_id = str(payload.get("trade_id") or "")
            pnl = _decimal(payload.get("realized_net_pnl"))
            if not trade_id or trade_id in seen or pnl is None:
                continue
            seen.add(trade_id)
            for usage in await self._repository.usage_for_decision(trade_id):
                if await self._repository.has_outcome(usage.memory_id, trade_id):
                    continue
                memory = await self._repository.get_strategic(usage.memory_id)
                if memory is None or memory.status is KnowledgeStatus.RETIRED:
                    continue
                helpful = _helpful(memory, pnl)
                await self._repository.record_outcome(
                    MemoryOutcome(
                        id=str(uuid4()),
                        memory_id=memory.id,
                        trade_id=trade_id,
                        prediction_context={"category": memory.category.value},
                        actual_outcome="win" if pnl > 0 else "loss_or_flat",
                        helpful=helpful,
                        estimated_contribution=pnl,
                        created_at=now,
                    )
                )
                if helpful is None:
                    continue
                await self._repository.update_strategic_stats(
                    memory.id,
                    reliability=memory.reliability,
                    successful_uses=memory.successful_uses + (1 if helpful else 0),
                    failed_uses=memory.failed_uses + (0 if helpful else 1),
                    status=memory.status,
                    now=now,
                )
                recorded += 1
        return recorded

    async def apply_decay(self, report: LifecycleReport) -> None:
        now = self._clock.now()
        memories = await self._repository.list_strategic(
            frozenset({KnowledgeStatus.ACTIVE, KnowledgeStatus.NEEDS_REVALIDATION}), limit=500
        )
        for memory in memories:
            reliability = reliability_for(memory, now)
            status = _next_status(memory, reliability, now)
            restored = (
                memory.status is KnowledgeStatus.NEEDS_REVALIDATION
                and status is KnowledgeStatus.ACTIVE
            )
            if reliability == memory.reliability and status is memory.status:
                continue
            await self._repository.update_strategic_stats(
                memory.id,
                reliability=reliability,
                successful_uses=memory.successful_uses,
                failed_uses=memory.failed_uses,
                status=status,
                now=now,
                last_validated_at=now if restored else None,
                validation_count=memory.validation_count + 1 if restored else None,
            )
            if status is not memory.status:
                {
                    KnowledgeStatus.NEEDS_REVALIDATION: report.demoted,
                    KnowledgeStatus.ACTIVE: report.restored,
                    KnowledgeStatus.RETIRED: report.retired,
                }[status].append(memory.id)
                await self._export(memory.id)

    async def detect_conflicts(self, report: LifecycleReport) -> None:
        now = self._clock.now()
        active = await self._repository.list_strategic(
            frozenset({KnowledgeStatus.ACTIVE}), limit=500
        )
        for left, right in combinations(active, 2):
            if not _contradict(left, right):
                continue
            if await self._repository.conflict_exists(left.id, right.id):
                continue
            await self._repository.record_conflict(
                MemoryConflict(
                    id=str(uuid4()),
                    memory_a_id=left.id,
                    memory_b_id=right.id,
                    conflict_type="opposing_claims",
                    resolution="weaker_side_sent_to_revalidation",
                    resolved_by="lifecycle",
                    created_at=now,
                )
            )
            weaker = min((left, right), key=lambda memory: (memory.reliability, memory.id))
            await self._repository.update_strategic_stats(
                weaker.id,
                reliability=weaker.reliability,
                successful_uses=weaker.successful_uses,
                failed_uses=weaker.failed_uses,
                status=KnowledgeStatus.NEEDS_REVALIDATION,
                now=now,
            )
            report.conflicts.append((left.id, right.id))
            report.demoted.append(weaker.id)
            await self._export(weaker.id)

    async def _export(self, memory_id: str) -> None:
        if self._vault is None:
            return
        memory = await self._repository.get_strategic(memory_id)
        versions = await self._repository.versions(memory_id)
        if memory is not None and versions:
            await self._vault.write(memory, versions[-1])


def reliability_for(memory: StrategicMemory, now: datetime) -> Decimal:
    uses = memory.successful_uses + memory.failed_uses
    blended = (float(memory.confidence) * PRIOR_WEIGHT + memory.successful_uses) / (
        PRIOR_WEIGHT + uses
    )
    return _unit(blended * _staleness(memory, now))


def _staleness(memory: StrategicMemory, now: datetime) -> float:
    reference = memory.last_validated_at or memory.created_at
    age = max(0.0, (now - reference).total_seconds() / 86400)
    if age <= STALE_AFTER_DAYS:
        return 1.0
    return math.pow(0.5, (age - STALE_AFTER_DAYS) / STALE_HALF_LIFE_DAYS)


def _next_status(memory: StrategicMemory, reliability: Decimal, now: datetime) -> KnowledgeStatus:
    uses = memory.successful_uses + memory.failed_uses
    reference = memory.last_validated_at or memory.created_at
    age = (now - reference).total_seconds() / 86400
    if age > RETIRE_WHEN_STALE_DAYS or (
        reliability < RETIRE_BELOW and uses >= MIN_USES_TO_RETIRE
    ):
        return KnowledgeStatus.RETIRED
    if memory.status is KnowledgeStatus.ACTIVE and (
        (reliability < REVALIDATE_BELOW and uses >= MIN_USES_TO_DEMOTE)
        or age > STALE_AFTER_DAYS
    ):
        return KnowledgeStatus.NEEDS_REVALIDATION
    if (
        memory.status is KnowledgeStatus.NEEDS_REVALIDATION
        and reliability >= RESTORE_AT
        and memory.successful_uses > memory.failed_uses
    ):
        return KnowledgeStatus.ACTIVE
    return memory.status


def _helpful(memory: StrategicMemory, pnl: Decimal) -> bool | None:
    if memory.category in _NEGATIVE_TYPES:
        # A warning that was shown yet the trade still happened cannot be credited either way.
        return None
    return pnl > 0


def _contradict(left: StrategicMemory, right: StrategicMemory) -> bool:
    if (left.symbol, left.market_regime, left.strategy) != (
        right.symbol,
        right.market_regime,
        right.strategy,
    ):
        return False
    left_subject, left_sign = _claim(left)
    right_subject, right_sign = _claim(right)
    return left_subject == right_subject and left_sign * right_sign < 0


def _claim(memory: StrategicMemory) -> tuple[str, int]:
    words = re.findall(r"[a-z0-9/]+", memory.title.lower())
    sign = next((_POLARITY[word] for word in words if word in _POLARITY), 0)
    if sign == 0:
        sign = -1 if memory.category in _NEGATIVE_TYPES else 1
    subject = " ".join(
        word for word in words if word not in _POLARITY and word not in _SKIP_WORDS
    )
    return subject, sign


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _unit(value: float) -> Decimal:
    return Decimal(str(round(max(0.0, min(1.0, value)), 4)))
