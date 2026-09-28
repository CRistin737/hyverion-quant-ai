"""Public-data source catalog, reviewed source registry and connector scheduling.

The registry is deliberately independent from network clients. A connector may only
fetch a source after its definition has passed the explicit ToS/robots boundary.
The scheduler provides deterministic scheduling primitives for public-data connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import Field, field_validator

from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.observability import DataSourceStatus

if TYPE_CHECKING:
    from trading_bot.config.models import ExternalDataConfig

# The catalog is intentionally identifier-only until each URL has passed a live ToS/robots review.
# Collectors must resolve these identifiers through a configured provider adapter.
# Official sources first (SEC, Fed, CFTC), then major financial press.
NEWS_ALLOWLIST = (
    "sec-press-releases",
    "federal-reserve-news",
    "cftc-news",
    "reuters-markets",
    "ap-news-business",
    "financial-times-markets",
)


def default_source_statuses(
    config: ExternalDataConfig | None = None,
) -> tuple[DataSourceStatus, ...]:
    sources = [
        DataSourceStatus(
            source_id="market-data",
            category="market",
            enabled=True,
            allowlisted=True,
            compliance_status="APPROVED",
            detail="QQQ quotes and bars from the configured market-data provider.",
            recovery_action="Configure the market-data provider credentials.",
        ),
    ]
    sources.extend(
        DataSourceStatus(
            source_id=source_id,
            category="news",
            enabled=bool(
                config
                and getattr(config, "news_provider", "") == "rss"
                and source_id in getattr(config, "news_feeds", {})
                and source_id in getattr(config, "news_reviewed_sources", ())
            ),
            allowlisted=True,
            compliance_status=(
                "APPROVED"
                if config and source_id in getattr(config, "news_reviewed_sources", ())
                else "PENDING_REVIEW"
            ),
            last_error=(
                None
                if config
                and source_id in getattr(config, "news_feeds", {})
                and source_id in getattr(config, "news_reviewed_sources", ())
                else "reviewed provider URL is not configured"
            ),
            detail=(
                f"Reviewed RSS; interval={getattr(config, 'collection_interval_seconds', 300)}s."
                if config
                and source_id in getattr(config, "news_feeds", {})
                and source_id in getattr(config, "news_reviewed_sources", ())
                else ""
            ),
            recovery_action=(
                "Source is ready for the configured RSS schedule."
                if config
                and source_id in getattr(config, "news_feeds", {})
                and source_id in getattr(config, "news_reviewed_sources", ())
                else "Add the exact URL and explicit review acknowledgement."
            ),
        )
        for source_id in NEWS_ALLOWLIST
    )
    social_provider = str(getattr(config, "social_provider", "disabled") or "disabled")
    social_enabled = social_provider in {"x", "reddit"}
    sources.extend(
        (
            DataSourceStatus(
                source_id="social-official-api",
                category="social",
                enabled=social_enabled,
                allowlisted=social_enabled,
                compliance_status="APPROVED" if social_enabled else "PENDING_REVIEW",
                last_error=None if social_enabled else "official API credentials not configured",
                detail=(
                    f"API; every {getattr(config, 'collection_interval_seconds', 300)}s."
                    if config
                    else "Official API connector."
                ),
                recovery_action="Configure an official Reddit/X API credential.",
            ),
        )
    )
    return tuple(sources)


# --- Reviewed source registry -----------------------------------------------

Transport = Literal["api", "rss", "html"]
SourceCategory = Literal["market", "news", "social"]
ComplianceStatus = Literal["PENDING_REVIEW", "APPROVED", "REJECTED"]


class SourceDefinition(StrictSchema):
    source_id: str = Field(min_length=1, max_length=120)
    category: SourceCategory
    region: str = Field(min_length=2, max_length=32)
    transport: Transport
    base_url: str = Field(min_length=1, max_length=500)
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    allowed_paths: tuple[str, ...] = ()
    terms_url: str | None = None
    robots_url: str | None = None
    html_allowed: bool = False
    poll_interval_seconds: int = Field(default=300, ge=5)
    rate_limit_per_minute: int = Field(default=30, ge=1)
    parser_version: str = Field(min_length=1, max_length=40)
    compliance_status: ComplianceStatus = "PENDING_REVIEW"
    enabled: bool = False

    @field_validator("base_url", "terms_url", "robots_url")
    @classmethod
    def validate_urls(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = httpx.URL(value)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("source URLs must be absolute HTTP(S) URLs")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def validate_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(host.strip().lower() for host in value if host.strip())
        if not normalized:
            raise ValueError("at least one allowed host is required")
        if any("/" in host or " " in host for host in normalized):
            raise ValueError("allowed hosts must contain hostnames only")
        return normalized

    def is_fetchable(self) -> bool:
        """Return whether this definition may be handed to a network connector."""

        return self.enabled and self.compliance_status == "APPROVED" and (
            self.transport != "html" or self.html_allowed
        )


@dataclass(frozen=True, slots=True)
class SourcePolicyFailure:
    code: str
    detail: str


class SourceRegistry:
    """In-memory registry used by collectors and health/status views."""

    def __init__(self, definitions: tuple[SourceDefinition, ...] = ()) -> None:
        self._definitions = {definition.source_id: definition for definition in definitions}

    def add(self, definition: SourceDefinition) -> None:
        if definition.source_id in self._definitions:
            raise ValueError(f"source already registered: {definition.source_id}")
        self._definitions[definition.source_id] = definition

    def get(self, source_id: str) -> SourceDefinition:
        try:
            return self._definitions[source_id]
        except KeyError as exc:
            raise KeyError(f"source is not registered: {source_id}") from exc

    def definitions(self) -> tuple[SourceDefinition, ...]:
        return tuple(self._definitions.values())

    def policy_failure(self, source_id: str) -> SourcePolicyFailure | None:
        definition = self.get(source_id)
        if not definition.enabled:
            return SourcePolicyFailure("source_disabled", "source is disabled")
        if definition.compliance_status != "APPROVED":
            return SourcePolicyFailure(
                "source_not_reviewed", "ToS/robots review is not approved"
            )
        if definition.transport == "html" and not definition.html_allowed:
            return SourcePolicyFailure(
                "html_not_allowed", "HTML collection requires explicit approval"
            )
        return None

    def require_fetchable(self, source_id: str) -> SourceDefinition:
        definition = self.get(source_id)
        failure = self.policy_failure(source_id)
        if failure is not None:
            raise ValueError(f"{failure.code}: {failure.detail}")
        return definition


def build_source_registry(definitions: tuple[SourceDefinition, ...]) -> SourceRegistry:
    """Build a registry while rejecting duplicate identifiers deterministically."""

    registry = SourceRegistry()
    for definition in definitions:
        registry.add(definition)
    return registry


# --- Connector scheduling ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectorSchedule:
    source_id: str
    interval_seconds: int
    max_backoff_seconds: int = 3600


@dataclass(frozen=True, slots=True)
class ConnectorState:
    source_id: str
    next_run_at: datetime
    consecutive_failures: int = 0
    paused_until: datetime | None = None
    last_error: str | None = None


class ConnectorScheduler:
    """Track due work without ever increasing pressure after a failure."""

    def __init__(
        self,
        schedules: tuple[ConnectorSchedule, ...],
        *,
        now: datetime,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("scheduler timestamps must be timezone-aware")
        self._schedules = {item.source_id: item for item in schedules}
        if len(self._schedules) != len(schedules):
            raise ValueError("connector source ids must be unique")
        timestamp = now.astimezone(UTC)
        self._states = {
            item.source_id: ConnectorState(item.source_id, timestamp) for item in schedules
        }

    def due(self, source_id: str, *, now: datetime) -> bool:
        state = self._state(source_id)
        timestamp = self._normalize(now)
        if state.paused_until is not None and timestamp < state.paused_until:
            return False
        return timestamp >= state.next_run_at

    def mark_success(self, source_id: str, *, now: datetime) -> ConnectorState:
        schedule = self._schedule(source_id)
        timestamp = self._normalize(now)
        updated = ConnectorState(
            source_id=source_id,
            next_run_at=timestamp + timedelta(seconds=schedule.interval_seconds),
        )
        self._states[source_id] = updated
        return updated

    def mark_failure(
        self,
        source_id: str,
        *,
        now: datetime,
        error: str,
        retry_after_seconds: int | None = None,
    ) -> ConnectorState:
        schedule = self._schedule(source_id)
        timestamp = self._normalize(now)
        previous = self._state(source_id)
        failures = previous.consecutive_failures + 1
        exponential = min(
            schedule.max_backoff_seconds,
            schedule.interval_seconds * (2 ** min(failures - 1, 8)),
        )
        delay = max(exponential, retry_after_seconds or 0)
        updated = ConnectorState(
            source_id=source_id,
            next_run_at=timestamp + timedelta(seconds=delay),
            consecutive_failures=failures,
            last_error=error[:255],
        )
        self._states[source_id] = updated
        return updated

    def pause(self, source_id: str, *, until: datetime, reason: str) -> ConnectorState:
        timestamp = self._normalize(until)
        previous = self._state(source_id)
        updated = ConnectorState(
            source_id=source_id,
            next_run_at=max(previous.next_run_at, timestamp),
            consecutive_failures=previous.consecutive_failures,
            paused_until=timestamp,
            last_error=reason[:255],
        )
        self._states[source_id] = updated
        return updated

    def state(self, source_id: str) -> ConnectorState:
        return self._state(source_id)

    def restore(self, state: ConnectorState) -> ConnectorState:
        """Restore a previously persisted state after a process restart.

        Restoration is intentionally explicit and bounded to a schedule that
        was configured in the current process. Unknown source IDs are rejected
        instead of silently creating work outside the allowlist.
        """

        self._schedule(state.source_id)
        normalized_next = self._normalize(state.next_run_at)
        normalized_pause = (
            self._normalize(state.paused_until) if state.paused_until is not None else None
        )
        if state.consecutive_failures < 0:
            raise ValueError("scheduler failure count cannot be negative")
        restored = ConnectorState(
            source_id=state.source_id,
            next_run_at=normalized_next,
            consecutive_failures=state.consecutive_failures,
            paused_until=normalized_pause,
            last_error=state.last_error[:255] if state.last_error else None,
        )
        self._states[state.source_id] = restored
        return restored

    def states(self) -> tuple[ConnectorState, ...]:
        return tuple(self._states.values())

    def _schedule(self, source_id: str) -> ConnectorSchedule:
        try:
            return self._schedules[source_id]
        except KeyError as exc:
            raise KeyError(f"connector is not scheduled: {source_id}") from exc

    def _state(self, source_id: str) -> ConnectorState:
        try:
            return self._states[source_id]
        except KeyError as exc:
            raise KeyError(f"connector is not scheduled: {source_id}") from exc

    @staticmethod
    def _normalize(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduler timestamps must be timezone-aware")
        return value.astimezone(UTC)
