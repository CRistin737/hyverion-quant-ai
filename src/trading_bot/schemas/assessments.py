from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from trading_bot.schemas.common import Evidence, MarketRegime, Score, StrictSchema


class AssessmentBase(StrictSchema):
    agent_id: str
    agent_version: str
    asset: str
    assessed_at: datetime
    confidence: Score
    evidence: tuple[Evidence, ...] = ()
    limitations: tuple[str, ...] = ()


class MarketAssessment(AssessmentBase):
    spread_bps: Decimal = Field(ge=0)
    liquidity_usd: Decimal = Field(ge=0)
    volatility_percent: Decimal = Field(ge=0)
    data_fresh: bool
    structure: Literal["bullish", "bearish", "neutral", "uncertain"]


class TechnicalAssessment(AssessmentBase):
    trend_score: Score
    momentum_score: Score
    mean_reversion_score: Score
    volatility_score: Score
    support_levels: tuple[Decimal, ...] = ()
    resistance_levels: tuple[Decimal, ...] = ()


class RegimeAssessment(AssessmentBase):
    regime: MarketRegime
    compatible_strategies: tuple[str, ...] = ()


class NewsAssessment(AssessmentBase):
    status: Literal["available", "insufficient_data"]
    sentiment: Literal["bullish", "bearish", "neutral", "mixed", "unknown"]
    severity: Score
    stale_items: int = Field(ge=0)
    duplicate_items: int = Field(ge=0)


class SocialAssessment(AssessmentBase):
    status: Literal["available", "insufficient_data"]
    sentiment: Literal["bullish", "bearish", "neutral", "mixed", "unknown"]
    manipulation_risk: Score
    unusual_activity: bool


class SensorAssessment(AssessmentBase):
    """One QQQ sensor's read (breadth, mega-caps, macro, rates, earnings/SEC,
    volatility/options). Evidence for strategy and critic, never an order."""

    stance: Literal["supports_long", "against_long", "neutral", "unavailable"]
    strength: Score
    key_points: tuple[str, ...] = Field(default=(), max_length=5)
    risk_flags: tuple[str, ...] = Field(default=(), max_length=5)


class CriticAssessment(AssessmentBase):
    proposal_id: str
    verdict: Literal["APPROVE", "REVISE", "REJECT"]
    critical_conflicts: tuple[str, ...] = ()
    weak_assumptions: tuple[str, ...] = ()
    required_revisions: tuple[str, ...] = ()
