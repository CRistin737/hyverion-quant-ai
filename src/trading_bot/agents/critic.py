from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from trading_bot.core.clock import Clock
from trading_bot.data.features import FeatureSet
from trading_bot.intelligence.evidence import build_evidence, confluence, data_quality
from trading_bot.schemas.assessments import CriticAssessment
from trading_bot.schemas.trading import MarketSnapshot, TradeProposal

# Below this, the evidence leans against a long entry (50 = no edge).
MIN_CONFLUENCE = Decimal("45")


class DeterministicCritic:
    agent_id = "critic"
    version = "1.0.0"

    def __init__(self, clock: Clock, max_spread_bps: Decimal) -> None:
        self._clock = clock
        self._max_spread_bps = max_spread_bps

    def review(
        self,
        proposal: TradeProposal,
        snapshot: MarketSnapshot,
        *,
        intelligence: Mapping[str, Any] | None = None,
        features: FeatureSet | None = None,
    ) -> CriticAssessment:
        conflicts: list[str] = []
        weak: list[str] = []
        revisions: list[str] = []
        if intelligence is not None and features is not None:
            self._intelligence_checks(
                proposal, snapshot, intelligence, features, conflicts, weak, revisions
            )
        if snapshot.spread_bps > self._max_spread_bps:
            conflicts.append("spread_exceeds_limit")
        if proposal.expected_r < Decimal("1.5"):
            conflicts.append("reward_risk_too_low")
        if proposal.expected_net_value_usd <= 0:
            conflicts.append("non_positive_net_expectancy")
        if proposal.contradictory_evidence:
            weak.append("proposal_contains_contradictory_evidence")
        verdict = "REJECT" if conflicts else ("REVISE" if revisions else "APPROVE")
        return CriticAssessment(
            agent_id=self.agent_id,
            agent_version=self.version,
            asset=proposal.asset,
            assessed_at=self._clock.now(),
            confidence=Decimal("100") if not conflicts else Decimal("90"),
            evidence=(),
            limitations=("Crítico determinista; la inteligencia externa puede faltar.",),
            proposal_id=proposal.proposal_id,
            verdict=verdict,
            critical_conflicts=tuple(conflicts),
            weak_assumptions=tuple(weak),
            required_revisions=tuple(conflicts) + tuple(revisions),
        )

    def _intelligence_checks(
        self,
        proposal: TradeProposal,
        snapshot: MarketSnapshot,
        intelligence: Mapping[str, Any],
        features: FeatureSet,
        conflicts: list[str],
        weak: list[str],
        revisions: list[str],
    ) -> None:
        """§41: breadth, mega-caps, macro, rates, volatility, news, data quality."""

        now = self._clock.now()
        graph = build_evidence(features, snapshot.last, intelligence, now=now)
        score = confluence(graph)
        quality = data_quality(
            intelligence,
            now=now,
            market_data_age=now - snapshot.event_time,
            session_open=True,
        )
        conflicts.extend(f"data_quality:{item}" for item in quality.blocking)
        if score.available_weight >= Decimal("0.5") and score.score < MIN_CONFLUENCE:
            # Soft: REVISE. Supervised mode blocks on it; paper_autonomous records it.
            revisions.append(f"low_confluence:{score.score}")
        contradicted = [n.family for n in graph.nodes if n.stance == "contradicts"]
        if len(contradicted) >= 3:
            revisions.append("evidence_contradicts:" + ",".join(contradicted))
        breadth = intelligence.get("breadth") or {}
        for divergence in breadth.get("divergences") or ():
            if divergence in {"qqq_up_breadth_weak", "megacap_only_rally"}:
                revisions.append(f"breadth_divergence:{divergence}")
        macro_node = graph.node("MACRO")
        if macro_node and macro_node.stance == "contradicts":
            weak.append("macro_event_near:" + ";".join(macro_node.reasons))
        megacaps = {row.get("symbol") for row in breadth.get("megacaps") or ()}
        today = now.date().isoformat()
        for event in intelligence.get("earnings") or ():
            if event.get("symbol") in megacaps and event.get("date") == today:
                weak.append(f"megacap_earnings_today:{event.get('symbol')}")
        if features.vwap_distance_atr is not None and features.vwap_distance_atr > Decimal("3"):
            revisions.append("qqq_extended_above_vwap")
