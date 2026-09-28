"""Apply or undo a human-approved change proposal.

The optimizer only writes proposals. This deterministic deployer runs when the
owner presses "Aprobar y aplicar" (or "Deshacer") in the app: it validates the
candidate against the allowlist in ``versions.parse_change``, records the review
transitions and opens a new component version. The engine reads active
versions at the start of each cycle, so a change takes effect on the next one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from trading_bot.agents.registry import AgentRegistry
from trading_bot.config.models import StrategiesConfig
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.learning.proposals import latest_proposal
from trading_bot.learning.supervisor import LearningSupervisor
from trading_bot.learning.versions import (
    ApplicableChange,
    ChangeNotApplicable,
    StrategyParams,
    VersionRepository,
    parse_change,
)
from trading_bot.strategies.registry import exit_defaults, strategy_version

_APPLICABLE_FROM = {"READY_FOR_REVIEW", "APPROVED"}


def baselines(
    strategies: StrategiesConfig, registry: AgentRegistry | None = None
) -> list[ApplicableChange]:
    """First version of every enabled strategy and every bundled agent spec."""

    items = [
        ApplicableChange(
            component_id=strategy_id,
            kind="strategy",
            version=strategy_version(strategy_id),
            params=StrategyParams.from_exit(exit_defaults(strategy_id)).model_dump(mode="json"),
            spec_hash=None,
        )
        for strategy_id in strategies.enabled
    ]
    items.extend(
        ApplicableChange(
            component_id=descriptor.agent_id,
            kind="agent",
            version=descriptor.version,
            params={"source": "bundle"},
            spec_hash=descriptor.spec_hash,
        )
        for descriptor in (registry or AgentRegistry()).descriptors()
    )
    return items


class ChangeDeployer:
    def __init__(
        self,
        *,
        database: Database,
        clock: Clock,
        strategies: Iterable[str],
        agents: Iterable[str],
    ) -> None:
        self._audit = AuditRepository(database)
        self._versions = VersionRepository(database)
        self._supervisor = LearningSupervisor(repository=self._audit, clock=clock)
        self._clock = clock
        self._strategies = tuple(strategies)
        self._agents = tuple(agents)

    async def apply(self, proposal_id: str, *, reason: str) -> dict[str, Any]:
        rows = await self._audit.recent("change_proposals", limit=500)
        proposal = latest_proposal(rows, proposal_id)
        if proposal is None:
            raise ChangeNotApplicable("proposal_not_found")
        if proposal.status not in _APPLICABLE_FROM:
            raise ChangeNotApplicable("invalid_state")
        # Validate before any write: an invalid candidate leaves no trace but the error.
        change = parse_change(proposal, strategies=self._strategies, agents=self._agents)
        if proposal.status == "READY_FOR_REVIEW":
            proposal = await self._supervisor.transition(proposal, "APPROVED", reason=reason)
        proposal = await self._supervisor.transition(proposal, "DEPLOYED", reason=reason)
        await self._versions.activate(
            change, proposal_id=proposal.id, reason=reason, now=self._clock.now()
        )
        return {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "component_id": change.component_id,
            "version": change.version,
        }

    async def rollback(self, component_id: str, *, reason: str) -> dict[str, Any]:
        undone = await self._versions.rollback(
            component_id, reason=reason, now=self._clock.now()
        )
        proposal_id = undone.get("proposal_id")
        if proposal_id:
            rows = await self._audit.recent("change_proposals", limit=500)
            proposal = latest_proposal(rows, str(proposal_id))
            if proposal is not None and proposal.status == "DEPLOYED":
                await self._supervisor.transition(proposal, "ROLLED_BACK", reason=reason)
        active = (await self._versions.active()).get(component_id, {})
        return {
            "component_id": component_id,
            "undone_version": undone.get("version"),
            "active_version": active.get("version"),
        }
