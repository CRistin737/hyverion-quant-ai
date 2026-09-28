"""Persisted, review-only self-improvement workflow.

The supervisor is deliberately not an editor or deployer. It turns bounded
observations into a typed ``ChangeProposal`` and stores every transition as an
immutable audit row. A future worker may run replay/backtest in an isolated
worktree, but this module never writes the active checkout, changes risk, or
promotes a candidate by itself.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import uuid4

from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.learning.optimizer import ChangeProposalWorkflow
from trading_bot.schemas.learning import ChangeProposal

_MAX_CANDIDATE_SPEC_BYTES = 40_000
_SENSITIVE_KEY_FRAGMENTS = ("secret", "token", "password", "api_key", "credential")


class LearningSupervisor:
    """Create and persist reviewable learning proposals without side effects."""

    def __init__(
        self,
        *,
        repository: AuditRepository,
        clock: Clock,
        workflow: ChangeProposalWorkflow | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._workflow = workflow or ChangeProposalWorkflow()

    def build_proposal(
        self,
        *,
        agent: str,
        current_version: str,
        candidate_version: str,
        reason: str,
        evidence: Sequence[str],
        affected_rules: Sequence[str],
        expected_improvement: str,
        risk: str,
        candidate_spec: Mapping[str, Any],
        created_at: datetime | None = None,
    ) -> ChangeProposal:
        """Validate a bounded candidate without touching files or deployment."""

        normalized_agent = agent.strip()
        normalized_current = current_version.strip()
        normalized_candidate = candidate_version.strip()
        if not normalized_agent or not normalized_current or not normalized_candidate:
            raise ValueError("agent and both version identifiers are required")
        if normalized_current == normalized_candidate:
            raise ValueError("candidate_version must differ from current_version")
        clean_evidence = _clean_required_sequence(evidence, "evidence")
        clean_rules = _clean_required_sequence(affected_rules, "affected_rules")
        if not reason.strip() or not expected_improvement.strip() or not risk.strip():
            raise ValueError("reason, expected_improvement and risk are required")
        bounded_spec = _sanitize_candidate_spec(candidate_spec)
        encoded = json.dumps(bounded_spec, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_CANDIDATE_SPEC_BYTES:
            raise ValueError("candidate_spec exceeds the bounded proposal size")
        return ChangeProposal(
            id=str(uuid4()),
            agent=normalized_agent,
            current_version=normalized_current,
            candidate_version=normalized_candidate,
            reason=reason.strip()[:2_000],
            evidence=clean_evidence,
            affected_rules=clean_rules,
            expected_improvement=expected_improvement.strip()[:1_000],
            risk=risk.strip()[:1_000],
            candidate_spec=bounded_spec,
            created_at=created_at or self._clock.now(),
        )

    async def propose(self, **kwargs: Any) -> ChangeProposal:
        """Build and persist a ``PROPOSED`` candidate as an immutable event."""

        proposal = self.build_proposal(**kwargs)
        await self._append(proposal, transition_from=None)
        return proposal

    async def transition(
        self, proposal: ChangeProposal, target: str, *, reason: str | None = None
    ) -> ChangeProposal:
        """Persist a validated workflow transition; never deploy or edit code."""

        updated = self._workflow.transition(proposal, target)
        await self._append(updated, transition_from=proposal.status, reason=reason)
        return updated

    async def _append(
        self,
        proposal: ChangeProposal,
        *,
        transition_from: str | None,
        reason: str | None = None,
    ) -> None:
        payload = proposal.model_dump(mode="json")
        if transition_from is not None:
            payload["transition_from"] = transition_from
            payload["transition_to"] = proposal.status
            payload["transition_reason"] = (reason or "").strip()[:200] or None
        # Each event carries its own time: a transition happens after the
        # proposal was created, and readers order events by these columns.
        recorded_at = proposal.created_at if transition_from is None else self._clock.now()
        await self._repository.append(
            "change_proposals",
            payload,
            created_at=recorded_at,
            asset=proposal.agent,
            event_time=recorded_at,
            received_time=recorded_at,
            processed_time=self._clock.now(),
        )


def _clean_required_sequence(values: Sequence[str], field: str) -> tuple[str, ...]:
    cleaned = tuple(
        value.strip()[:500]
        for value in values
        if isinstance(value, str) and value.strip()
    )
    if not cleaned:
        raise ValueError(f"{field} must contain at least one item")
    return cleaned


def _sanitize_candidate_spec(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("candidate_spec must be a non-empty mapping")

    def sanitize(item: Any, *, depth: int = 0) -> Any:
        if depth > 5:
            raise ValueError("candidate_spec nesting is too deep")
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                key = str(raw_key).strip()[:120]
                if not key:
                    continue
                if any(fragment in key.lower() for fragment in _SENSITIVE_KEY_FRAGMENTS):
                    raise ValueError(f"candidate_spec contains sensitive field: {key}")
                result[key] = sanitize(raw_value, depth=depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            if len(item) > 100:
                raise ValueError("candidate_spec list is too large")
            return [sanitize(child, depth=depth + 1) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return str(item)[:2_000] if isinstance(item, str) else item
        raise ValueError("candidate_spec contains an unsupported value")

    result = sanitize(value)
    if not isinstance(result, dict) or not result:
        raise ValueError("candidate_spec must be a non-empty mapping")
    return result
