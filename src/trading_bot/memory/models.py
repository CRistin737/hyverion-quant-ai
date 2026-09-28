"""Typed memory contracts shared by the gateway, curator and repositories.

Agents can only produce a ``MemoryProposal``. Candidates, strategic knowledge
and their versions are created by deterministic services, never by an LLM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from trading_bot.schemas.common import StrictSchema

UnitScore = Annotated[Decimal, Field(ge=0, le=1)]
ShortText = Annotated[str, Field(min_length=1, max_length=160)]
Identifier = Annotated[str, Field(min_length=1, max_length=64)]


class MemoryType(StrEnum):
    OBSERVATION = "OBSERVATION"
    HYPOTHESIS = "HYPOTHESIS"
    PATTERN = "PATTERN"
    RULE = "RULE"
    LESSON = "LESSON"
    FAILURE = "FAILURE"
    RESEARCH = "RESEARCH"
    CONFIRMED_KNOWLEDGE = "CONFIRMED_KNOWLEDGE"
    RETIRED_KNOWLEDGE = "RETIRED_KNOWLEDGE"


# Agents may report what they saw; promotion to stronger types is the curator's job.
AGENT_PROPOSABLE_TYPES = frozenset(
    {MemoryType.OBSERVATION, MemoryType.HYPOTHESIS, MemoryType.LESSON, MemoryType.FAILURE}
)


class CandidateStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATING = "VALIDATING"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"


class KnowledgeStatus(StrEnum):
    ACTIVE = "ACTIVE"
    NEEDS_REVALIDATION = "NEEDS_REVALIDATION"
    RETIRED = "RETIRED"


class _Timestamped(StrictSchema):
    @field_validator("*", mode="after")
    @classmethod
    def _require_utc(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("memory timestamps must be timezone-aware")
            return value.astimezone(UTC)
        return value


class MemoryProposal(_Timestamped):
    """The only memory write an agent can emit. It is untrusted until curated."""

    memory_type: MemoryType
    title: ShortText
    summary: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=4000)
    agent_id: Identifier
    source_type: Identifier
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=50)
    symbol: str | None = Field(default=None, max_length=40)
    market_regime: str | None = Field(default=None, max_length=40)
    trade_id: Identifier | None = None
    created_at: datetime

    @field_validator("memory_type")
    @classmethod
    def _agent_types_only(cls, value: MemoryType) -> MemoryType:
        if value not in AGENT_PROPOSABLE_TYPES:
            raise ValueError(f"agents cannot propose {value.value} memories")
        return value


class EvidenceLink(_Timestamped):
    id: Identifier
    memory_candidate_id: Identifier
    source_type: Identifier
    source_id: Identifier
    relationship: Literal["supports", "contradicts", "context"]
    weight: UnitScore
    created_at: datetime


class MemoryCandidate(_Timestamped):
    id: Identifier
    memory_type: MemoryType
    title: ShortText
    summary: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=4000)
    source_type: Identifier
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=50)
    agent_id: Identifier | None = None
    trade_id: Identifier | None = None
    symbol: str | None = Field(default=None, max_length=40)
    market_regime: str | None = Field(default=None, max_length=40)
    confidence: UnitScore
    importance: UnitScore
    novelty: UnitScore
    evidence_strength: UnitScore
    confidence_components: dict[str, UnitScore] | None = None
    status: CandidateStatus = CandidateStatus.PENDING
    created_at: datetime


class StrategicMemory(_Timestamped):
    id: Identifier
    knowledge_id: Identifier
    title: ShortText
    summary: str = Field(min_length=1, max_length=500)
    category: MemoryType
    symbol: str | None = Field(default=None, max_length=40)
    strategy: str | None = Field(default=None, max_length=80)
    market_regime: str | None = Field(default=None, max_length=40)
    confidence: UnitScore
    reliability: UnitScore
    importance: UnitScore
    status: KnowledgeStatus = KnowledgeStatus.ACTIVE
    valid_from: datetime
    valid_until: datetime | None = None
    last_validated_at: datetime | None = None
    validation_count: int = Field(default=0, ge=0)
    successful_uses: int = Field(default=0, ge=0)
    failed_uses: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class StrategicMemoryVersion(_Timestamped):
    id: Identifier
    strategic_memory_id: Identifier
    version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=20000)
    content_hash: str = Field(min_length=64, max_length=64)
    previous_version: int | None = Field(default=None, ge=1)
    change_reason: str = Field(min_length=1, max_length=255)
    created_by: Identifier
    created_at: datetime


class MemoryUsage(_Timestamped):
    id: Identifier
    memory_id: Identifier
    agent_run_id: Identifier | None = None
    decision_id: Identifier | None = None
    retrieval_score: UnitScore
    used_in_prompt: bool
    influenced_decision: bool | None = None
    created_at: datetime


class MemoryOutcome(_Timestamped):
    id: Identifier
    memory_id: Identifier
    trade_id: Identifier
    prediction_context: dict[str, Any]
    actual_outcome: str = Field(min_length=1, max_length=255)
    helpful: bool | None = None
    estimated_contribution: Decimal | None = None
    created_at: datetime


class MemoryConflict(_Timestamped):
    id: Identifier
    memory_a_id: Identifier
    memory_b_id: Identifier
    conflict_type: Identifier
    resolution: str | None = Field(default=None, max_length=255)
    resolved_by: Identifier | None = None
    created_at: datetime
