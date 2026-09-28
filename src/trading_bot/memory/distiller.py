"""Deterministic distillation of evaluated trades into memory candidates.

No LLM is involved. Evaluated real and shadow trades inside a time window are
grouped by asset and exit reason; each group with enough evidence yields one
candidate whose ``source_ids`` are the trades themselves, so the curator can
re-score it from the same evidence. Distilled knowledge is advisory: it can
describe forgone shadow profit but can never relax a risk limit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.gateway import MemoryGateway
from trading_bot.memory.models import CandidateStatus, MemoryProposal, MemoryType
from trading_bot.memory.repository import SqlMemoryRepository

DAILY_REVIEW = (timedelta(days=7), 3)
WEEKLY_SYNTHESIS = (timedelta(days=30), 5)
MAX_SOURCES = 50


@dataclass(frozen=True, slots=True)
class _Trade:
    trade_id: str
    asset: str
    pnl: Decimal
    exit_reason: str
    shadow: bool
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class DistillationReport:
    window_days: int
    trades_considered: int
    created: tuple[str, ...]
    skipped_duplicates: int


class TradeReviewDistiller:
    def __init__(
        self,
        *,
        audit: AuditRepository,
        repository: SqlMemoryRepository,
        gateway: MemoryGateway,
        clock: Clock,
    ) -> None:
        self._audit = audit
        self._repository = repository
        self._gateway = gateway
        self._clock = clock

    async def daily_review(self) -> DistillationReport:
        window, minimum = DAILY_REVIEW
        return await self.distill(window=window, min_samples=minimum)

    async def weekly_synthesis(self) -> DistillationReport:
        window, minimum = WEEKLY_SYNTHESIS
        return await self.distill(window=window, min_samples=minimum)

    async def distill(self, *, window: timedelta, min_samples: int) -> DistillationReport:
        if min_samples < 3:
            raise ValueError("one or two trades are not knowledge; min_samples must be >= 3")
        now = self._clock.now()
        trades = [
            trade
            for trade in await self._trades()
            if now - window <= trade.evaluated_at <= now
        ]
        open_titles = {
            candidate.title
            for candidate in await self._repository.list_candidates(
                frozenset({CandidateStatus.PENDING, CandidateStatus.VALIDATING}), limit=500
            )
        }
        created: list[str] = []
        skipped = 0
        for proposal in self._proposals(trades, min_samples=min_samples, now=now):
            if proposal.title in open_titles:
                skipped += 1
                continue
            candidate = await self._gateway.propose(proposal)
            open_titles.add(proposal.title)
            created.append(candidate.id)
        return DistillationReport(
            window_days=window.days,
            trades_considered=len(trades),
            created=tuple(created),
            skipped_duplicates=skipped,
        )

    def _proposals(
        self, trades: list[_Trade], *, min_samples: int, now: datetime
    ) -> list[MemoryProposal]:
        real: dict[tuple[str, str], list[_Trade]] = defaultdict(list)
        forgone: dict[str, list[_Trade]] = defaultdict(list)
        for trade in trades:
            if trade.shadow:
                forgone[trade.asset].append(trade)
            else:
                real[(trade.asset, trade.exit_reason)].append(trade)
        proposals: list[MemoryProposal] = []
        for (asset, exit_reason), group in sorted(real.items()):
            if len(group) < min_samples:
                continue
            losses = sum(1 for trade in group if trade.pnl < 0)
            total = sum((trade.pnl for trade in group), Decimal("0"))
            failing = losses * 2 > len(group)
            reason = exit_reason.replace("_", " ")
            proposals.append(
                self._proposal(
                    MemoryType.FAILURE if failing else MemoryType.LESSON,
                    title=(
                        f"{asset} trades closed by {reason} mostly lose"
                        if failing
                        else f"{asset} trades closed by {reason} mostly win"
                    ),
                    summary=(
                        f"{len(group)} evaluated trades, {losses} losing, "
                        f"net {total.quantize(Decimal('0.01'))} USD."
                    ),
                    asset=asset,
                    group=group,
                    now=now,
                )
            )
        for asset, group in sorted(forgone.items()):
            winners = [trade for trade in group if trade.pnl > 0]
            if len(group) < min_samples or len(winners) * 2 <= len(group):
                continue
            proposals.append(
                self._proposal(
                    MemoryType.OBSERVATION,
                    title=f"{asset} risk-rejected proposals often won in shadow",
                    summary=(
                        f"{len(winners)} of {len(group)} rejected proposals were profitable in "
                        "shadow replay. Informational only; risk limits are unchanged."
                    ),
                    asset=asset,
                    group=group,
                    now=now,
                )
            )
        return proposals

    @staticmethod
    def _proposal(
        memory_type: MemoryType,
        *,
        title: str,
        summary: str,
        asset: str,
        group: list[_Trade],
        now: datetime,
    ) -> MemoryProposal:
        recent = sorted(group, key=lambda trade: trade.evaluated_at)[-MAX_SOURCES:]
        lines = "\n".join(
            f"- {trade.trade_id}: {trade.pnl} USD ({trade.exit_reason})" for trade in recent
        )
        return MemoryProposal(
            memory_type=memory_type,
            title=title[:160],
            summary=summary,
            content=f"Distilled deterministically from evaluated trades.\n{lines}"[:4000],
            agent_id="distiller",
            source_type="trade_evaluation",
            source_ids=tuple(trade.trade_id for trade in recent),
            symbol=asset,
            created_at=now,
        )

    async def _trades(self) -> list[_Trade]:
        trades: dict[str, _Trade] = {}
        for row in await self._audit.recent("trade_evaluations", limit=500):
            trade = _parse(row)
            if trade is not None:
                trades.setdefault(trade.trade_id, trade)
        return list(trades.values())


def _parse(row: dict[str, Any]) -> _Trade | None:
    payload = row.get("payload")
    asset = row.get("asset")
    if not isinstance(payload, dict) or not asset or not payload.get("trade_id"):
        return None
    try:
        shadow = payload.get("shadow_comparison")
        if isinstance(shadow, dict):
            pnl = Decimal(str(shadow["shadow_trade_pnl_usd"]))
        else:
            pnl = Decimal(str(payload["realized_net_pnl"]))
        evaluated = datetime.fromisoformat(str(payload["evaluated_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return None
    if evaluated.tzinfo is None or not pnl.is_finite():
        return None
    return _Trade(
        trade_id=str(payload["trade_id"])[:64],
        asset=str(asset)[:40],
        pnl=pnl,
        exit_reason=str(payload.get("exit_reason") or ("shadow" if shadow else "unknown"))[:40],
        shadow=isinstance(shadow, dict),
        evaluated_at=evaluated.astimezone(UTC),
    )
