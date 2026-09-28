from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

Score = Annotated[Decimal, Field(ge=0, le=100)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class TradingMode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class MarketRegime(StrEnum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    EVENT_DRIVEN = "EVENT_DRIVEN"
    BREAKOUT_EXPANSION = "BREAKOUT_EXPANSION"
    OPENING_DISCOVERY = "OPENING_DISCOVERY"
    CLOSING_FLOW = "CLOSING_FLOW"
    UNCERTAIN = "UNCERTAIN"


class Evidence(StrictSchema):
    source: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=1000)
    observed_at: datetime
    strength: Score

    @field_validator("observed_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(UTC)
