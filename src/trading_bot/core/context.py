"""Typed context assembly for specialist agents.

The assembler is deterministic and read-only. It validates asset identity and market
freshness before any model can receive a context envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from trading_bot.data.features import FeatureSet
from trading_bot.schemas.assessments import (
    CriticAssessment,
    MarketAssessment,
    NewsAssessment,
    RegimeAssessment,
    SensorAssessment,
    SocialAssessment,
    TechnicalAssessment,
)
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal


class ContextAssemblyError(ValueError):
    """Raised when context is unsafe or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class AssessmentBundle:
    market: MarketAssessment | None = None
    technical: TechnicalAssessment | None = None
    regime: RegimeAssessment | None = None
    news: NewsAssessment | None = None
    social: SocialAssessment | None = None
    proposal: TradeProposal | None = None
    critic: CriticAssessment | None = None
    # QQQ sensors run in parallel after the regime read; only strategy and
    # critic see them.
    sensors: tuple[SensorAssessment, ...] = ()


class AgentContextAssembler:
    def __init__(self, *, stale_after_seconds: int = 30) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self._stale_after = timedelta(seconds=stale_after_seconds)

    def assemble(
        self,
        *,
        agent_id: str,
        snapshot: MarketSnapshot,
        features: FeatureSet,
        assessments: AssessmentBundle | None = None,
        now: datetime,
    ) -> dict[str, Any]:
        timestamp = _utc(now)
        assessments = assessments or AssessmentBundle()
        if snapshot.symbol != features.symbol:
            raise ContextAssemblyError("market snapshot and features use different assets")
        if timestamp - snapshot.event_time > self._stale_after:
            raise ContextAssemblyError("market context is stale")
        models: list[BaseModel] = [snapshot, features]
        for assessment in (
            assessments.market,
            assessments.technical,
            assessments.regime,
            assessments.news,
            assessments.social,
            assessments.proposal,
            assessments.critic,
        ):
            if assessment is None:
                continue
            if getattr(assessment, "asset", snapshot.symbol) != snapshot.symbol:
                raise ContextAssemblyError("agent evidence contains a different asset")
            models.append(assessment)
        allowed = _allowed_models(agent_id, assessments)
        sensors = (
            {sensor.agent_id: sensor.model_dump(mode="json") for sensor in assessments.sensors}
            if agent_id in {"strategy", "critic"}
            else {}
        )
        return {
            "asset": snapshot.symbol,
            "generated_at": timestamp.isoformat(),
            "agent_id": agent_id,
            "market": snapshot.model_dump(mode="json"),
            "features": features.model_dump(mode="json"),
            "assessments": {
                key: value.model_dump(mode="json")
                for key, value in allowed.items()
                if value is not None
            }
            | ({"sensors": sensors} if sensors else {}),
            "evidence_count": sum(
                len(getattr(model, "evidence", ()))
                for model in models
                if hasattr(model, "evidence")
            ),
        }


def _allowed_models(agent_id: str, bundle: AssessmentBundle) -> dict[str, BaseModel | None]:
    mapping: dict[str, BaseModel | None] = {
        "market": bundle.market,
        "technical": bundle.technical,
        "regime": bundle.regime,
        "news": bundle.news,
        "social": bundle.social,
        "proposal": bundle.proposal,
        "critic": bundle.critic,
    }
    if agent_id in {"market", "technical"}:
        return {key: mapping[key] for key in ("market", "technical")}
    if agent_id == "regime":
        return {key: mapping[key] for key in ("market", "technical", "regime")}
    if agent_id in {"news", "social"}:
        return {agent_id: mapping[agent_id]}
    if agent_id == "strategy":
        return {key: value for key, value in mapping.items() if key not in {"proposal", "critic"}}
    if agent_id == "critic":
        return {key: value for key, value in mapping.items() if key != "critic"}
    return mapping


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContextAssemblyError("context timestamps must be timezone-aware")
    return value.astimezone(UTC)
