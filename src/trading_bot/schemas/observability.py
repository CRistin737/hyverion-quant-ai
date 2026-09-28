from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator

from trading_bot.schemas.common import StrictSchema


class AgentDescriptor(StrictSchema):
    agent_id: str = Field(min_length=1)
    role: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    version: str = Field(min_length=1)
    spec_hash: str = Field(min_length=64, max_length=64)
    spec_path: str = Field(min_length=1)
    status: Literal["ACTIVE", "IDLE", "ERROR", "UNAVAILABLE"] = "IDLE"
    last_run_at: datetime | None = None
    last_error: str | None = None

    @field_validator("last_run_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("agent timestamps must be timezone-aware")
        return value.astimezone(UTC)


class AgentRunRecord(StrictSchema):
    run_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    agent_version: str = Field(min_length=1)
    provider: str | None = None
    model: str | None = None
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "SKIPPED", "CANCELLED"]
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: Decimal | None = Field(default=None, ge=0)
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class SubscriptionStatus(StrictSchema):
    """Provider-owned subscription state, kept separate from API billing."""

    supported: bool = False
    cli_available: bool = False
    auth_state: Literal[
        "CONNECTED",
        "AUTH_REQUIRED",
        "DEGRADED",
        "UNKNOWN",
        "UNSUPPORTED",
    ] = "UNKNOWN"
    plan: str | None = None
    usage_state: Literal["AVAILABLE", "UNKNOWN", "UNAVAILABLE"] = "UNKNOWN"
    context_remaining_percent: Decimal | None = Field(default=None, ge=0, le=100)
    context_used_tokens: str | None = None
    context_window_tokens: str | None = None
    context_model: str | None = None
    session_remaining: Decimal | None = Field(default=None, ge=0)
    four_hour_remaining: Decimal | None = Field(default=None, ge=0)
    weekly_remaining: Decimal | None = Field(default=None, ge=0)
    session_reset_label: str | None = None
    weekly_reset_label: str | None = None
    usage_detail: str | None = None
    limit_source: Literal["OFFICIAL_CLI", "OFFICIAL_API", "UNKNOWN"] = "UNKNOWN"
    last_checked_at: datetime | None = None
    detail: str = ""


class ProviderProbeOutput(StrictSchema):
    """Minimal response accepted by the read-only subscription chain probe."""

    status: Literal["READY"] = "READY"
    summary: str = Field(min_length=1, max_length=280)


class ReadinessCheck(StrictSchema):
    """One deterministic readiness gate exposed to the native audit view."""

    check_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    status: Literal["PASS", "INFO", "GATED", "FAIL"]
    detail: str = Field(min_length=1, max_length=500)
    evidence: tuple[str, ...] = ()


class ReadinessReport(StrictSchema):
    """Safe PAPER/LIVE readiness projection; it never grants authorization."""

    generated_at: datetime
    overall: Literal["PAPER_READY", "PAPER_BLOCKED"]
    live_authorized: bool = False
    checks: tuple[ReadinessCheck, ...]


class ProviderStatus(StrictSchema):
    provider_id: str
    display_name: str
    api_configured: bool
    official_login_available: bool
    auth_state: Literal[
        "CONNECTED",
        "AVAILABLE",
        "NOT_CONFIGURED",
        "AUTH_REQUIRED",
        "EXPIRED",
        "RATE_LIMITED",
        "DEGRADED",
        "DISABLED",
        "ERROR",
        "UNSUPPORTED",
        "UNKNOWN",
    ]
    billing_mode: Literal["api", "subscription", "disabled", "unknown"]
    usage_state: Literal["AVAILABLE", "UNKNOWN", "UNAVAILABLE"]
    context_window: int | None = Field(default=None, ge=0)
    session_limit: Decimal | None = Field(default=None, ge=0)
    four_hour_limit: Decimal | None = Field(default=None, ge=0)
    weekly_limit: Decimal | None = Field(default=None, ge=0)
    limit_source: Literal["CONFIGURED", "OFFICIAL", "UNKNOWN"] = "UNKNOWN"
    subscription: SubscriptionStatus
    detail: str


class DataSourceStatus(StrictSchema):
    source_id: str
    category: Literal["market", "news", "social"]
    enabled: bool
    allowlisted: bool
    compliance_status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED"] = "PENDING_REVIEW"
    freshness_seconds: int | None = Field(default=None, ge=0)
    last_success_at: datetime | None = None
    last_error: str | None = None
    records_today: int = Field(default=0, ge=0)
    detail: str = ""
    recovery_action: str = ""


class SourceRunRecord(StrictSchema):
    """Bounded outcome of one reviewed news/social collection attempt."""

    source_id: str = Field(min_length=1)
    status: Literal["SUCCEEDED", "FAILED", "SKIPPED"]
    records_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    error: str | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source run timestamps must be timezone-aware")
        return value.astimezone(UTC)


class AgentMemoryEntry(StrictSchema):
    agent_id: str
    version: str
    prompt_hash: str
    summary: str
    evidence_count: int = Field(ge=0)
    outcome: str
    recorded_at: datetime
