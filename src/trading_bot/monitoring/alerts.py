"""Deterministic operational-alert projection for the native terminal.

Alerts are derived from persisted, sanitized system events and protection
state.  They are informational controls only: an alert can stop a cycle via
the existing deterministic safety paths, but an LLM cannot acknowledge,
escalate or clear one and no alert sends an order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from trading_bot.schemas.common import StrictSchema


class OperationalAlert(StrictSchema):
    alert_id: str = Field(min_length=1)
    severity: Literal["INFO", "WARNING", "CRITICAL"]
    source: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=240)
    created_at: datetime
    acknowledged: bool = False


_EVENT_ALERTS: dict[str, tuple[str, str]] = {
    "RECONCILIATION_MISMATCH": ("CRITICAL", "Exchange reconciliation mismatch"),
    "RECONCILIATION_ERROR": ("CRITICAL", "Exchange reconciliation failed"),
    "DATABASE_BACKUP_FAILED": ("CRITICAL", "Control-plane backup failed"),
    "EXTERNAL_COLLECTION_DEGRADED": ("WARNING", "External intelligence collection degraded"),
    "MARKET_STREAM_REJECTED": ("WARNING", "Market stream frame rejected"),
    "MARKET_STREAM_CYCLE_FAILED": ("WARNING", "Market stream cycle failed closed"),
    "PROVIDER_CHAIN_EXHAUSTED": ("CRITICAL", "All configured AI subscriptions failed"),
    "PAPER_CYCLE_FAILED": ("WARNING", "Paper cycle failed closed"),
    "POSITION_PROTECTION_FAILED": ("CRITICAL", "Open position could not be protected"),
    "POSITION_STATE_INVALID": ("CRITICAL", "Open position state is invalid"),
    "EXIT_RECOVERY_REQUIRED": ("CRITICAL", "Position exit requires recovery"),
    "LIFECYCLE_VIOLATION": ("CRITICAL", "Operation lifecycle violation"),
    "EXECUTION_UNKNOWN": ("CRITICAL", "Order state unknown; awaiting reconciliation"),
}


def project_operational_alerts(
    event_rows: Sequence[Mapping[str, Any]],
    persisted_alert_rows: Sequence[Mapping[str, Any]],
    protection_recovery: Mapping[str, Any],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Project bounded, deterministic alerts without mutating persistence."""

    if limit < 1 or limit > 100:
        raise ValueError("alert limit must be between 1 and 100")
    result: list[OperationalAlert] = []
    seen: set[str] = set()
    for row in persisted_alert_rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        alert = _alert_from_payload(payload, row)
        if alert is not None and alert.alert_id not in seen:
            result.append(alert)
            seen.add(alert.alert_id)
    for row in event_rows:
        payload = row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        status = str(payload.get("status") or "").upper()
        rule = _EVENT_ALERTS.get(status)
        if rule is None:
            continue
        row_id = str(row.get("id") or f"{status}:{row.get('created_at')}")
        if row_id in seen:
            continue
        severity, default_message = rule
        detail = str(
            payload.get("detail") or payload.get("error_code") or payload.get("error") or ""
        ).strip()
        message = f"{default_message}: {detail}" if detail else default_message
        result.append(
            OperationalAlert(
                alert_id=row_id,
                severity=severity,
                source="system_events",
                message=message[:240],
                created_at=_timestamp(row.get("created_at")),
            )
        )
        seen.add(row_id)
    if bool(protection_recovery.get("safe_mode")):
        alert_id = "protective-stop-recovery"
        if alert_id not in seen:
            result.append(
                OperationalAlert(
                    alert_id=alert_id,
                    severity="CRITICAL",
                    source="protection_recovery",
                    message="Protective-stop recovery is in SAFE MODE",
                    created_at=datetime.now(UTC),
                )
            )
    result.sort(key=lambda item: item.created_at, reverse=True)
    return [item.model_dump(mode="json") for item in result[:limit]]


def _alert_from_payload(
    payload: Mapping[str, Any], row: Mapping[str, Any]
) -> OperationalAlert | None:
    alert_id = str(payload.get("alert_id") or row.get("id") or "").strip()
    severity = str(payload.get("severity") or "INFO").upper()
    message = str(payload.get("message") or payload.get("detail") or "").strip()
    if not alert_id or severity not in {"INFO", "WARNING", "CRITICAL"} or not message:
        return None
    return OperationalAlert(
        alert_id=alert_id,
        severity=severity,
        source=str(payload.get("source") or "alerts"),
        message=message[:240],
        created_at=_timestamp(payload.get("created_at") or row.get("created_at")),
        acknowledged=bool(payload.get("acknowledged")),
    )


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)
