"""One deterministic memory cycle: distill → curate → index → lifecycle → meta → vault."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.curation import AuditEvidenceProvider, CurationResult, MemoryCurator
from trading_bot.memory.distiller import DistillationReport, TradeReviewDistiller
from trading_bot.memory.knowledge_lifecycle import KnowledgeLifecycleManager, LifecycleReport
from trading_bot.memory.maintenance import (
    IndexReport,
    MemoryIndexer,
    build_memory_gateway,
    semantic_stack,
)
from trading_bot.memory.meta import MetaMemoryAnalyzer, MetaMemoryReport
from trading_bot.memory.operations import MemoryOperations
from trading_bot.memory.repository import SqlMemoryRepository
from trading_bot.memory.vault import FileVaultRepository


@dataclass(frozen=True, slots=True)
class MemoryCycleReport:
    daily: DistillationReport
    weekly: DistillationReport
    curated: tuple[CurationResult, ...]
    index: IndexReport
    lifecycle: LifecycleReport
    meta: MetaMemoryReport

    def summary(self) -> dict[str, Any]:
        """Counts only; the shape the app shows after "Ejecutar ciclo de memoria"."""

        return {
            "daily_candidates": len(self.daily.created),
            "weekly_candidates": len(self.weekly.created),
            "curated": len(self.curated),
            "outcomes_recorded": self.lifecycle.outcomes_recorded,
            # Counts: the UI renders "N retirado(s)"; ids are in as_dict().
            "demoted": len(self.lifecycle.demoted),
            "restored": len(self.lifecycle.restored),
            "retired": len(self.lifecycle.retired),
            "conflicts": len(self.lifecycle.conflicts),
            "outcomes_considered": self.meta.outcomes_considered,
        }

    def as_dict(self) -> dict[str, Any]:
        """Full JSON-safe report (CLI). Decimals are serialized as strings."""

        return {
            "daily_candidates": len(self.daily.created),
            "weekly_candidates": len(self.weekly.created),
            "skipped_duplicates": self.daily.skipped_duplicates + self.weekly.skipped_duplicates,
            "curated": [
                {
                    "candidate_id": result.candidate_id,
                    "decision": result.status.value,
                    "promoted_as": result.promoted_as.value if result.promoted_as else None,
                    "reasons": list(result.reasons),
                    "confidence": str(result.report.confidence),
                }
                for result in self.curated
            ],
            "lifecycle": {
                "outcomes_recorded": self.lifecycle.outcomes_recorded,
                "demoted": self.lifecycle.demoted,
                "restored": self.lifecycle.restored,
                "retired": self.lifecycle.retired,
                "conflicts": [list(pair) for pair in self.lifecycle.conflicts],
            },
            "meta": {
                "outcomes_considered": self.meta.outcomes_considered,
                "agents": [
                    {
                        "agent_id": item.agent_id,
                        "market_regime": item.market_regime,
                        "samples": item.samples,
                        "reliability": str(item.reliability),
                    }
                    for item in self.meta.agents
                ],
                "memories": [
                    {
                        "knowledge_id": item.knowledge_id,
                        "title": item.title,
                        "uses": item.uses,
                        "value_score": str(item.value_score),
                        "lift_usd": str(item.lift_usd),
                        "avoided_loss_usd": str(item.avoided_loss_usd),
                        "forgone_profit_usd": str(item.forgone_profit_usd),
                    }
                    for item in self.meta.memories
                ],
            },
        }


async def run_memory_cycle(
    settings: Settings, database: Database, clock: Clock
) -> MemoryCycleReport:
    audit = AuditRepository(database)
    repository = SqlMemoryRepository(database)
    gateway = await build_memory_gateway(settings, database)
    distiller = TradeReviewDistiller(
        audit=audit, repository=repository, gateway=gateway, clock=clock
    )
    daily = await distiller.daily_review()
    weekly = await distiller.weekly_synthesis()
    vault = FileVaultRepository(settings.public.memory.vault_path)
    vault.ensure_layout()
    curator = MemoryCurator(
        repository=repository,
        evidence=AuditEvidenceProvider(audit),
        audit=audit,
        clock=clock,
        vault=vault,
    )
    curated = tuple(await curator.curate_pending())
    vector, embedder = await semantic_stack(settings, database)
    index = await MemoryIndexer(repository=repository, vector=vector, embedder=embedder).run()
    lifecycle = await KnowledgeLifecycleManager(
        repository=repository, audit=audit, clock=clock, vault=vault
    ).run()
    meta = await MetaMemoryAnalyzer(repository=repository, audit=audit, clock=clock).run()
    # Refresh every note's links and the market/regime/map indexes for Obsidian.
    await MemoryOperations(database=database, clock=clock, vault=vault).export_vault()
    return MemoryCycleReport(
        daily=daily,
        weekly=weekly,
        curated=curated,
        index=index,
        lifecycle=lifecycle,
        meta=meta,
    )
