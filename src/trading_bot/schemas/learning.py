from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from trading_bot.schemas.common import StrictSchema


class TradeEvaluation(StrictSchema):
    trade_id: str
    realized_net_pnl: Decimal
    mfe_usd: Decimal
    mae_usd: Decimal
    fees_usd: Decimal = Field(ge=0)
    slippage_usd: Decimal = Field(ge=0)
    thesis_valid: bool
    signal_correct: bool
    agent_incremental_values: dict[str, Decimal]
    evaluated_at: datetime


class LearningMetrics(StrictSchema):
    """Auditable outcome metrics used by the learning workspace.

    These metrics describe observed evaluations only. They never change risk
    weights or promote a candidate configuration by themselves.
    """

    sample_size: int = Field(ge=0)
    invalid_records: int = Field(ge=0)
    wins: int = Field(ge=0)
    losses: int = Field(ge=0)
    win_rate_percent: Decimal = Field(ge=0, le=100)
    expectancy_usd: Decimal
    profit_factor: Decimal | None = Field(default=None, ge=0)
    max_drawdown_usd: Decimal = Field(ge=0)
    average_win_usd: Decimal = Field(ge=0)
    average_loss_usd: Decimal = Field(ge=0)
    average_mfe_usd: Decimal
    average_mae_usd: Decimal
    fees_usd: Decimal = Field(ge=0)
    slippage_usd: Decimal = Field(ge=0)
    cost_drag_percent: Decimal | None = Field(default=None, ge=0)
    agent_incremental_value_usd: dict[str, Decimal]


class BacktestRunRequest(StrictSchema):
    """Bounded local replay request exposed to the native research workspace."""

    walk_forward: bool = False
    capital_usd: Decimal | None = Field(default=None, gt=0)
    prices: tuple[Decimal, ...] | None = None

    @model_validator(mode="after")
    def validate_prices(self) -> BacktestRunRequest:
        if self.prices is not None and (
            len(self.prices) > 500 or any(price <= 0 for price in self.prices)
        ):
            raise ValueError("prices must contain at most 500 positive observations")
        return self


class ChangeProposal(StrictSchema):
    id: str
    agent: str
    current_version: str
    candidate_version: str
    reason: str
    evidence: tuple[str, ...]
    affected_rules: tuple[str, ...]
    expected_improvement: str
    risk: str
    candidate_spec: dict[str, Any]
    created_at: datetime
    status: Literal[
        "PROPOSED",
        "TESTING",
        "REJECTED",
        "READY_FOR_REVIEW",
        "APPROVED",
        "DEPLOYED",
        "ROLLED_BACK",
    ] = "PROPOSED"


class ChangeProposalRequest(StrictSchema):
    """Bounded request used by the review-only learning control path."""

    agent: str = Field(min_length=1, max_length=80)
    current_version: str = Field(min_length=1, max_length=80)
    candidate_version: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=2_000)
    evidence: tuple[str, ...] = Field(min_length=1, max_length=50)
    affected_rules: tuple[str, ...] = Field(min_length=1, max_length=50)
    expected_improvement: str = Field(min_length=1, max_length=1_000)
    risk: str = Field(min_length=1, max_length=1_000)
    candidate_spec: dict[str, Any] = Field(min_length=1)


class ReviewReasonRequest(StrictSchema):
    """Reason typed by the owner for applying or undoing an approved change."""

    reason: str = Field(min_length=3, max_length=200)


class ChangeProposalTransitionRequest(StrictSchema):
    """Manual review transition; deployment states are intentionally excluded."""

    target: Literal["TESTING", "READY_FOR_REVIEW", "APPROVED", "REJECTED"]
    # Every human review decision is audited with its reason (same rule as operations).
    reason: str = Field(min_length=3, max_length=200)
