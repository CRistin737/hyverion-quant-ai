"""Read model for the append-only ``change_proposals`` audit table.

Every proposal is stored as one row when created and one row per review
transition. Readers need the latest state of each proposal plus its timeline;
this module rebuilds both from the raw rows without touching storage.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_bot.schemas.learning import ChangeProposal

_TRANSITION_KEYS = {"transition_from", "transition_to", "transition_reason"}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _event_time(row: dict[str, Any]) -> str:
    for key in ("processed_time", "event_time", "created_at"):
        value = row.get(key)
        if isinstance(value, datetime):
            aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
            return aware.astimezone(UTC).isoformat()
        if value:
            return str(value)
    return ""


def _events_by_proposal(
    rows: list[dict[str, Any]],
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for row in rows:
        payload = _payload(row)
        proposal_id = str(payload.get("id") or "")
        if proposal_id:
            grouped.setdefault(proposal_id, []).append((_event_time(row), payload))
    for events in grouped.values():
        # Creation first, then transitions in the order they happened.
        events.sort(key=lambda item: (item[1].get("transition_to") is not None, item[0]))
    return grouped


def _to_proposal(payload: dict[str, Any]) -> ChangeProposal | None:
    try:
        return ChangeProposal.model_validate(
            {key: value for key, value in payload.items() if key not in _TRANSITION_KEYS}
        )
    except ValueError:
        return None


def latest_proposal(rows: list[dict[str, Any]], proposal_id: str) -> ChangeProposal | None:
    """Latest valid state of one proposal, or ``None`` when it is unknown."""

    for _, payload in reversed(_events_by_proposal(rows).get(proposal_id, [])):
        proposal = _to_proposal(payload)
        if proposal is not None:
            return proposal
    return None


def proposal_timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Latest state of every proposal with its review history, newest first."""

    items: list[dict[str, Any]] = []
    for events in _events_by_proposal(rows).values():
        latest = next(
            (p for _, payload in reversed(events) if (p := _to_proposal(payload))), None
        )
        if latest is None:
            continue
        history = [
            {
                "status": str(payload.get("transition_to") or payload.get("status") or ""),
                "from_status": payload.get("transition_from"),
                "reason": payload.get("transition_reason"),
                "at": at,
            }
            for at, payload in events
        ]
        item = latest.model_dump(mode="json")
        item["history"] = history
        item["updated_at"] = history[-1]["at"] if history else item["created_at"]
        items.append(item)
    items.sort(key=lambda item: str(item["updated_at"]), reverse=True)
    return items
