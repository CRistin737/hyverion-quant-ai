from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_bot.schemas.learning import ChangeProposal

ALLOWED_TRANSITIONS = {
    "PROPOSED": {"TESTING", "REJECTED"},
    "TESTING": {"REJECTED", "READY_FOR_REVIEW"},
    "READY_FOR_REVIEW": {"APPROVED", "REJECTED"},
    "APPROVED": {"DEPLOYED"},
    "DEPLOYED": {"ROLLED_BACK"},
    "REJECTED": set(),
    "ROLLED_BACK": set(),
}


class ChangeProposalWorkflow:
    def transition(self, proposal: ChangeProposal, target: str) -> ChangeProposal:
        if target not in ALLOWED_TRANSITIONS[proposal.status]:
            raise ValueError(f"invalid change proposal transition: {proposal.status} -> {target}")
        return proposal.model_copy(update={"status": target})


@dataclass(frozen=True, slots=True)
class ExperimentMetrics:
    expectancy: Decimal
    max_drawdown: Decimal
    false_positive_rate: Decimal
    sample_size: int


class ChampionChallenger:
    def ready_for_review(
        self,
        champion: ExperimentMetrics,
        challenger: ExperimentMetrics,
        *,
        minimum_samples: int = 100,
    ) -> bool:
        return (
            challenger.sample_size >= minimum_samples
            and challenger.expectancy > champion.expectancy
            and challenger.max_drawdown <= champion.max_drawdown
            and challenger.false_positive_rate <= champion.false_positive_rate
        )
