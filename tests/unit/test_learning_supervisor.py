from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_bot.db import AuditRepository, Database
from trading_bot.learning import LearningSupervisor

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _kwargs() -> dict[str, object]:
    return {
        "agent": "optimizer",
        "current_version": "weights-v1",
        "candidate_version": "weights-v2",
        "reason": "The challenger improves OOS expectancy without higher drawdown.",
        "evidence": ("experiment:123", "replay:oos:456"),
        "affected_rules": ("signal_weights",),
        "expected_improvement": "Higher expectancy after fees.",
        "risk": "Candidate could reduce signal precision in low liquidity.",
        "candidate_spec": {
            "type": "signal_weights",
            "files": ["config/strategies.yaml"],
            "tests": ["tests/replay/test_weights.py"],
        },
        "created_at": NOW,
    }


def test_supervisor_rejects_sensitive_or_unbounded_candidate_spec(clock) -> None:
    supervisor = LearningSupervisor(repository=None, clock=clock)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sensitive field"):
        supervisor.build_proposal(**{**_kwargs(), "candidate_spec": {"api_token": "x"}})
    with pytest.raises(ValueError, match="candidate_version"):
        supervisor.build_proposal(**{**_kwargs(), "candidate_version": "weights-v1"})


async def test_supervisor_persists_proposal_and_transition(tmp_path, clock) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'learning.db'}")
    await database.initialize()
    supervisor = LearningSupervisor(repository=AuditRepository(database), clock=clock)

    proposal = await supervisor.propose(**_kwargs())
    assert proposal.status == "PROPOSED"
    testing = await supervisor.transition(proposal, "TESTING")
    assert testing.status == "TESTING"

    rows = await AuditRepository(database).recent("change_proposals", limit=10)
    assert len(rows) == 2
    transitioned = next(
        row for row in rows if row["payload"].get("transition_to") == "TESTING"
    )
    assert transitioned["payload"]["candidate_spec"]["files"] == [
        "config/strategies.yaml"
    ]
    await database.close()
