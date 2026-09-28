"""Memory curation: deterministic confidence from trade evidence and the Curator.

Confidence is computed from evaluated trade outcomes only (never from LLM confidence);
the Curator validates candidates against that evidence and promotes or rejects them.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol

from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.memory.models import (
    CandidateStatus,
    KnowledgeStatus,
    MemoryCandidate,
    MemoryType,
    StrategicMemory,
)
from trading_bot.memory.ports import VaultRepository
from trading_bot.memory.repository import SqlMemoryRepository

# --- confidence ------------------------------------------------------------

SAMPLE_TARGET = 30
RECENCY_HALF_LIFE_DAYS = 90.0
IN_SAMPLE_FRACTION = 0.7
MIN_OOS_SAMPLES = 2
IMPORTANCE_SCALE_USD = Decimal("50")

WEIGHTS: dict[str, float] = {
    "sample_size": 0.20,
    "statistical_strength": 0.20,
    "source_quality": 0.10,
    "recency": 0.10,
    "oos_confirmation": 0.15,
    "regime_consistency": 0.10,
    "data_quality": 0.15,
}
CONTRADICTION_WEIGHT = 0.30
SOURCE_QUALITY = {"real": 1.0, "shadow": 0.6}


@dataclass(frozen=True, slots=True)
class EvidenceSample:
    trade_id: str
    valid: bool
    net_pnl: Decimal = Decimal("0")
    source: Literal["real", "shadow"] = "real"
    observed_at: datetime | None = None
    regime: str | None = None


class EvidenceProvider(Protocol):
    async def samples(self, trade_ids: Sequence[str]) -> list[EvidenceSample]: ...


class AuditEvidenceProvider:
    """Reads evaluated real and shadow trades from the audit log (bounded)."""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def samples(self, trade_ids: Sequence[str]) -> list[EvidenceSample]:
        rows = await self._repository.recent("trade_evaluations", limit=500)
        by_trade: dict[str, dict[str, Any]] = {}
        for row in rows:
            payload = row.get("payload")
            if isinstance(payload, dict) and payload.get("trade_id"):
                by_trade.setdefault(str(payload["trade_id"]), payload)
        return [_sample(trade_id, by_trade.get(trade_id)) for trade_id in trade_ids]


def _sample(trade_id: str, payload: dict[str, Any] | None) -> EvidenceSample:
    if payload is None:
        return EvidenceSample(trade_id=trade_id, valid=False)
    try:
        shadow = payload.get("shadow_comparison")
        if isinstance(shadow, dict):
            pnl = Decimal(str(shadow["shadow_trade_pnl_usd"]))
            source: Literal["real", "shadow"] = "shadow"
        else:
            pnl = Decimal(str(payload["realized_net_pnl"]))
            source = "real"
        observed = datetime.fromisoformat(str(payload["evaluated_at"]).replace("Z", "+00:00"))
        if observed.tzinfo is None or not pnl.is_finite():
            raise ValueError("invalid evidence")
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return EvidenceSample(trade_id=trade_id, valid=False)
    regime = payload.get("market_regime")
    return EvidenceSample(
        trade_id=trade_id,
        valid=True,
        net_pnl=pnl,
        source=source,
        observed_at=observed.astimezone(UTC),
        regime=str(regime) if regime else None,
    )


@dataclass(frozen=True, slots=True)
class ConfidenceReport:
    components: dict[str, Decimal]
    confidence: Decimal
    importance: Decimal
    evidence_strength: Decimal
    valid_samples: int
    expected_sign: int
    oos_confirmed: bool


def score_evidence(
    samples: Sequence[EvidenceSample],
    *,
    now: datetime,
    candidate_regime: str | None,
    expected_sign: int | None = None,
) -> ConfidenceReport:
    """Compute every component in ``0..1`` and the weighted, penalized confidence."""

    valid = sorted(
        (sample for sample in samples if sample.valid and sample.observed_at is not None),
        key=lambda sample: (sample.observed_at, sample.trade_id),
    )
    total = len(samples)
    n = len(valid)
    pnls = [float(sample.net_pnl) for sample in valid]
    split = max(1, math.floor(n * IN_SAMPLE_FRACTION)) if n else 0
    in_sample, out_sample = pnls[:split], pnls[split:]
    in_mean = sum(in_sample) / len(in_sample) if in_sample else 0.0
    sign = expected_sign if expected_sign in (-1, 1) else (1 if in_mean >= 0 else -1)

    components = {
        "sample_size": min(1.0, n / SAMPLE_TARGET),
        "statistical_strength": _t_strength(pnls, sign),
        "source_quality": (sum(SOURCE_QUALITY[s.source] for s in valid) / n) if n else 0.0,
        "recency": _recency(valid, now),
        "oos_confirmation": 1.0 if _oos_confirms(out_sample, sign) else 0.0,
        "regime_consistency": _regime_consistency(valid, candidate_regime),
        "data_quality": (n / total) if total else 0.0,
        "contradiction": (sum(1 for pnl in pnls if pnl * sign < 0) / n) if n else 0.0,
    }
    raw = sum(WEIGHTS[key] * components[key] for key in WEIGHTS)
    raw -= CONTRADICTION_WEIGHT * components["contradiction"]
    total_pnl = sum((sample.net_pnl for sample in valid), Decimal("0"))
    return ConfidenceReport(
        components={key: _unit(value) for key, value in components.items()},
        confidence=_unit(raw),
        importance=_unit(float(abs(total_pnl) / IMPORTANCE_SCALE_USD)),
        evidence_strength=_unit(components["statistical_strength"]),
        valid_samples=n,
        expected_sign=sign,
        oos_confirmed=components["oos_confirmation"] == 1.0,
    )


def _t_strength(pnls: list[float], sign: int) -> float:
    if len(pnls) < 2:
        return 0.0
    mean = sum(pnls) / len(pnls)
    variance = sum((value - mean) ** 2 for value in pnls) / (len(pnls) - 1)
    if mean * sign <= 0:
        return 0.0
    if variance == 0:
        return 1.0
    t = abs(mean) / math.sqrt(variance / len(pnls))
    return min(1.0, t / 3.0)


def _oos_confirms(out_sample: list[float], sign: int) -> bool:
    if len(out_sample) < MIN_OOS_SAMPLES:
        return False
    return (sum(out_sample) / len(out_sample)) * sign > 0


def _recency(samples: Sequence[EvidenceSample], now: datetime) -> float:
    if not samples:
        return 0.0
    factors = []
    for sample in samples:
        assert sample.observed_at is not None
        age = max(0.0, (now - sample.observed_at).total_seconds() / 86400)
        factors.append(math.pow(0.5, age / RECENCY_HALF_LIFE_DAYS))
    return sum(factors) / len(factors)


def _regime_consistency(samples: Sequence[EvidenceSample], regime: str | None) -> float:
    known = [sample for sample in samples if sample.regime]
    if regime is None or not known:
        return 0.5  # neutral when the regime of the evidence is unknown
    return sum(1 for sample in known if sample.regime == regime) / len(known)


def _unit(value: float) -> Decimal:
    return Decimal(str(round(max(0.0, min(1.0, value)), 4)))


# --- curator ---------------------------------------------------------------

MIN_SAMPLES = 3
PATTERN_SAMPLES = 10
HYPOTHESIS_CONFIDENCE = Decimal("0.35")
PATTERN_CONFIDENCE = Decimal("0.55")
MIN_DATA_QUALITY = Decimal("0.8")
MAX_CONTRADICTION = Decimal("0.4")


@dataclass(frozen=True, slots=True)
class CurationResult:
    candidate_id: str
    status: CandidateStatus
    promoted_as: MemoryType | None
    strategic_memory_id: str | None
    reasons: tuple[str, ...]
    report: ConfidenceReport


class MemoryCurator:
    def __init__(
        self,
        *,
        repository: SqlMemoryRepository,
        evidence: EvidenceProvider,
        audit: AuditRepository,
        clock: Clock,
        vault: VaultRepository | None = None,
    ) -> None:
        self._repository = repository
        self._evidence = evidence
        self._audit = audit
        self._clock = clock
        self._vault = vault

    async def curate_pending(self, *, limit: int = 100) -> list[CurationResult]:
        candidates = await self._repository.list_candidates(
            frozenset({CandidateStatus.PENDING, CandidateStatus.VALIDATING}), limit=limit
        )
        return [await self.curate(candidate) for candidate in candidates]

    async def curate(self, candidate: MemoryCandidate) -> CurationResult:
        now = self._clock.now()
        samples = await self._evidence.samples(candidate.source_ids)
        report = score_evidence(
            samples,
            now=now,
            candidate_regime=candidate.market_regime,
            expected_sign=-1 if candidate.memory_type is MemoryType.FAILURE else None,
        )
        duplicate = await self._is_duplicate(candidate, now)
        status, category, reasons = _decide(report, duplicate=duplicate)
        memory_id: str | None = None
        if category is not None:
            memory_id = await self._promote(candidate, category, report, now)
        await self._repository.update_candidate_scores(
            candidate.id,
            confidence=report.confidence,
            importance=report.importance,
            novelty=Decimal("0") if duplicate else Decimal("1"),
            evidence_strength=report.evidence_strength,
            components=report.components,
            status=status,
        )
        await self._audit.append(
            "system_events",
            {
                "status": "MEMORY_CURATION",
                "candidate_id": candidate.id,
                "decision": status.value,
                "promoted_as": category.value if category else None,
                "strategic_memory_id": memory_id,
                "reasons": list(reasons),
                "confidence": str(report.confidence),
                "components": {key: str(value) for key, value in report.components.items()},
            },
            created_at=now,
            asset=candidate.symbol,
        )
        return CurationResult(candidate.id, status, category, memory_id, reasons, report)

    async def _is_duplicate(self, candidate: MemoryCandidate, now: datetime) -> bool:
        existing = await self._repository.search_strategic(
            as_of=now,
            symbol=candidate.symbol,
            market_regime=candidate.market_regime,
            statuses=frozenset({KnowledgeStatus.ACTIVE, KnowledgeStatus.NEEDS_REVALIDATION}),
            limit=50,
        )
        title = _normalize(candidate.title)
        return any(
            _normalize(memory.title) == title
            and memory.symbol == candidate.symbol
            and memory.market_regime == candidate.market_regime
            for memory in existing
        )

    async def _promote(
        self,
        candidate: MemoryCandidate,
        category: MemoryType,
        report: ConfidenceReport,
        now: datetime,
    ) -> str:
        memory = StrategicMemory(
            id=f"mem-{candidate.id}"[:64],
            knowledge_id=f"KNOW-{now.year}-{candidate.id.replace('-', '')[:12]}",
            title=candidate.title,
            summary=candidate.summary,
            category=category,
            symbol=candidate.symbol,
            market_regime=candidate.market_regime,
            confidence=report.confidence,
            reliability=report.confidence,
            importance=report.importance,
            status=KnowledgeStatus.ACTIVE,
            valid_from=now,
            last_validated_at=now,
            validation_count=1,
            created_at=now,
            updated_at=now,
        )
        version = await self._repository.create_strategic(
            memory,
            content=_note_body(candidate, report),
            created_by="curator",
            change_reason=f"promoted_from_candidate:{candidate.id}",
        )
        if self._vault is not None:
            await self._vault.write(memory, version)
        return memory.id


def _decide(
    report: ConfidenceReport, *, duplicate: bool
) -> tuple[CandidateStatus, MemoryType | None, tuple[str, ...]]:
    components = report.components
    if report.valid_samples and components["data_quality"] < MIN_DATA_QUALITY:
        return CandidateStatus.REJECTED, None, ("evidence_data_quality_low",)
    if report.valid_samples < MIN_SAMPLES:
        return CandidateStatus.PENDING, None, ("insufficient_evidence",)
    if components["contradiction"] > MAX_CONTRADICTION:
        return CandidateStatus.REJECTED, None, ("evidence_contradicts_claim",)
    if duplicate:
        return CandidateStatus.REJECTED, None, ("duplicate_of_active_knowledge",)
    if (
        report.valid_samples >= PATTERN_SAMPLES
        and report.oos_confirmed
        and report.confidence >= PATTERN_CONFIDENCE
    ):
        return CandidateStatus.PROMOTED, MemoryType.PATTERN, ("pattern_criteria_met",)
    if report.confidence >= HYPOTHESIS_CONFIDENCE:
        return CandidateStatus.PROMOTED, MemoryType.HYPOTHESIS, ("hypothesis_criteria_met",)
    return CandidateStatus.VALIDATING, None, ("confidence_below_threshold",)


def _note_body(candidate: MemoryCandidate, report: ConfidenceReport) -> str:
    components = "\n".join(
        f"- {key}: {value}" for key, value in sorted(report.components.items())
    )
    sources = ", ".join(candidate.source_ids)
    return (
        f"## Pattern\n{candidate.content}\n\n"
        f"## Evidence\n- samples: {report.valid_samples}\n- sources: {sources}\n"
        f"- out-of-sample confirmed: {report.oos_confirmed}\n\n"
        f"## Confidence components\n{components}\n"
    )


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
