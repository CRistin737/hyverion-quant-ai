"""Leakage-free research replay: what would memory have said at decision time?

For every evaluated proposal (real or shadow) the replay rebuilds the knowledge
that existed *and* its status/reliability exactly as they were when the proposal
was created, using point-in-time retrieval. It then measures whether warnings
preceded losses and supporting knowledge preceded wins.

The replay is research evidence only. It never records memory usage (that would
invent decisions), never writes outcomes, and never touches RiskEngine, sizing
or a limit. Every retrieved item is re-checked against the decision time; any
violation is counted as a leak and excluded, so a non-zero count is a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.models import MemoryType
from trading_bot.memory.repository import SqlMemoryRepository
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vector import NullVectorBackend

MAX_ROWS = 500
ITEMS_PER_DECISION = 5
_WARNING_TYPES = frozenset({MemoryType.FAILURE})


@dataclass(frozen=True, slots=True)
class ReplayedDecision:
    trade_id: str
    asset: str
    decided_at: datetime
    pnl_usd: Decimal
    shadow: bool
    warnings: tuple[str, ...]
    support: tuple[str, ...]


@dataclass(slots=True)
class MemoryReplayReport:
    window_days: int
    decisions: list[ReplayedDecision] = field(default_factory=list)
    skipped_without_decision_time: int = 0
    leaks: int = 0

    @property
    def warned(self) -> list[ReplayedDecision]:
        return [item for item in self.decisions if item.warnings]

    @property
    def supported(self) -> list[ReplayedDecision]:
        return [item for item in self.decisions if item.support and not item.warnings]

    def summary(self) -> dict[str, Any]:
        warned = self.warned
        supported = self.supported
        total = _sum(self.decisions)
        warned_pnl = _sum(warned)
        return {
            "window_days": self.window_days,
            "decisions_replayed": len(self.decisions),
            "skipped_without_decision_time": self.skipped_without_decision_time,
            "leaks": self.leaks,
            "with_memory": sum(1 for item in self.decisions if item.warnings or item.support),
            "warned": len(warned),
            "warned_losing": sum(1 for item in warned if item.pnl_usd <= 0),
            "warned_pnl_usd": str(warned_pnl),
            "supported": len(supported),
            "supported_winning": sum(1 for item in supported if item.pnl_usd > 0),
            "supported_pnl_usd": str(_sum(supported)),
            "total_pnl_usd": str(total),
            # Counterfactual only: what the same decisions would have made had every
            # warned one been skipped. It is evidence for review, never a rule.
            "pnl_if_warned_skipped_usd": str(total - warned_pnl),
        }


class MemoryReplayEvaluator:
    def __init__(
        self, *, repository: SqlMemoryRepository, audit: AuditRepository, clock: Clock
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._clock = clock
        # Metadata-only and point-in-time: embeddings reflect today's content.
        self._retrieval = MemoryRetrievalEngine(
            repository=repository, vector=NullVectorBackend(), point_in_time=True
        )

    async def replay(self, *, window: timedelta = timedelta(days=90)) -> MemoryReplayReport:
        if window <= timedelta(0):
            raise ValueError("the replay window must be positive")
        now = self._clock.now()
        report = MemoryReplayReport(window_days=window.days)
        decided = await self._decision_times()
        for trade_id, (asset, pnl, shadow) in (await self._outcomes()).items():
            decided_at = decided.get(trade_id)
            if decided_at is None:
                report.skipped_without_decision_time += 1
                continue
            if not now - window <= decided_at <= now:
                continue
            result = await self._retrieval.retrieve(
                "", as_of=decided_at, symbol=asset, limit=ITEMS_PER_DECISION
            )
            warnings: list[str] = []
            support: list[str] = []
            for retrieved in result.items:
                memory = retrieved.memory
                if memory.created_at > decided_at or memory.valid_from > decided_at:
                    report.leaks += 1
                    continue
                target = warnings if memory.category in _WARNING_TYPES else support
                target.append(memory.knowledge_id)
            report.decisions.append(
                ReplayedDecision(
                    trade_id=trade_id,
                    asset=asset,
                    decided_at=decided_at,
                    pnl_usd=pnl,
                    shadow=shadow,
                    warnings=tuple(warnings),
                    support=tuple(support),
                )
            )
        report.decisions.sort(key=lambda item: (item.decided_at, item.trade_id))
        await self._audit.append(
            "system_events", {"status": "MEMORY_REPLAY", **report.summary()}, created_at=now
        )
        return report

    async def _decision_times(self) -> dict[str, datetime]:
        times: dict[str, datetime] = {}
        for row in await self._audit.recent("trade_proposals", limit=MAX_ROWS):
            payload = row.get("payload")
            created = row.get("created_at")
            if not isinstance(payload, dict) or not isinstance(created, datetime):
                continue
            proposal_id = str(payload.get("proposal_id") or "")
            if proposal_id:
                aware = created if created.tzinfo else created.replace(tzinfo=UTC)
                times.setdefault(proposal_id, aware.astimezone(UTC))
        return times

    async def _outcomes(self) -> dict[str, tuple[str, Decimal, bool]]:
        outcomes: dict[str, tuple[str, Decimal, bool]] = {}
        for row in await self._audit.recent("trade_evaluations", limit=MAX_ROWS):
            payload = row.get("payload")
            asset = row.get("asset")
            if not isinstance(payload, dict) or not asset:
                continue
            trade_id = str(payload.get("trade_id") or "")
            shadow = payload.get("shadow_comparison")
            pnl = _decimal(
                shadow.get("shadow_trade_pnl_usd")
                if isinstance(shadow, dict)
                else payload.get("realized_net_pnl")
            )
            if trade_id and pnl is not None and trade_id not in outcomes:
                outcomes[trade_id] = (str(asset)[:40], pnl, isinstance(shadow, dict))
        return outcomes


def _sum(items: list[ReplayedDecision]) -> Decimal:
    return sum((item.pnl_usd for item in items), Decimal("0")).quantize(Decimal("0.01"))


def _decimal(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None
