"""Evidence graph, confluence, data quality and the intelligence-aware critic (§37-§41)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import make_proposal

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.core.clock import FixedClock
from trading_bot.data.features import FeatureSet
from trading_bot.intelligence.evidence import (
    CONFLUENCE_WEIGHTS,
    build_evidence,
    confluence,
    consensus_matrix,
    data_quality,
)
from trading_bot.schemas.trading import MarketSnapshot

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


def _features(**updates: object) -> FeatureSet:
    values: dict[str, object] = {
        "symbol": "QQQ",
        "prices": tuple(Decimal("480") + i for i in range(6)),
        "simple_return_percent": Decimal("1"),
        "fast_sma": Decimal("484"),
        "slow_sma": Decimal("483"),
        "realized_volatility_percent": Decimal("0.2"),
        "momentum_score": Decimal("70"),
        "trend_score": Decimal("75"),
        "session_vwap": Decimal("482"),
        "atr": Decimal("1"),
        "ema_fast": Decimal("484"),
        "ema_slow": Decimal("483"),
        "vwap_distance_atr": Decimal("1"),
        "relative_volume": Decimal("1.3"),
    }
    values.update(updates)
    return FeatureSet(**values)  # type: ignore[arg-type]


BULL = {
    "breadth": {"weighted_breadth": "0.75", "pct_green": "0.68", "top10_breadth": "0.8",
                "top10_contribution_share": "0.5", "divergences": [], "megacaps": []},
    "macro": {"gate": None, "upcoming": []},
    "volatility": {"status": "ok", "vix_close": "14", "skew_25d": "0.03", "term_slope": "0.01"},
    "rates": {"status": "ok", "series": [{"series_id": "DGS10", "change_1d": "0.01"}]},
    "news": [{"qqq_relevance": "0.09", "decay": "0.9", "materiality": "HIGH", "sentiment": 1,
              "symbols": ["NVDA"]}],
}


def test_weights_sum_to_one_and_correlated_families_count_once() -> None:
    assert sum(CONFLUENCE_WEIGHTS.values()) == Decimal("1")
    graph = build_evidence(_features(), Decimal("485"), BULL, now=NOW)
    score = confluence(graph)
    assert score.score > 60
    # Breadth and components read the same stocks: one merged vote.
    assert not ("BREADTH" in score.by_family and "COMPONENTS" in score.by_family)
    assert consensus_matrix(graph).conflict < Decimal("0.5")


def test_macro_gate_and_weak_breadth_pull_confluence_down() -> None:
    bear = {
        **BULL,
        "macro": {"gate": "macro_event_pre_block", "upcoming": []},
        "breadth": {"weighted_breadth": "0.2", "pct_green": "0.3", "top10_breadth": "0.2",
                    "divergences": ["qqq_up_breadth_weak"]},
        "volatility": {
            "status": "ok", "vix_close": "31", "skew_25d": "0.12", "term_slope": "-0.02"
        },
    }
    graph = build_evidence(_features(), Decimal("485"), bear, now=NOW)
    bull = confluence(build_evidence(_features(), Decimal("485"), BULL, now=NOW)).score
    # A clean chart does not outvote a macro block, weak breadth and stressed vol.
    assert confluence(graph).score < 50 < bull - 10
    assert "MACRO" in consensus_matrix(graph).bear
    assert any("macro_event_pre_block" in item for item in graph.contradictions)


def test_missing_data_is_unavailable_not_neutral() -> None:
    graph = build_evidence(_features(), Decimal("485"), None, now=NOW)
    assert graph.node("BREADTH").stance == "unavailable"  # type: ignore[union-attr]
    assert confluence(graph).available_weight < Decimal("0.5")
    quality = data_quality(
        {"macro": {"gate": "macro_calendar_unavailable"}}, now=NOW,
        market_data_age=timedelta(seconds=90), session_open=True,
    )
    assert set(quality.blocking) == {"market_data_stale", "macro_calendar_unavailable"}
    assert "breadth_unavailable" in quality.degraded


def _snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="QQQ", bid=Decimal("484.99"), ask=Decimal("485"), last=Decimal("485"),
        session_volume=Decimal("1000000"), session_dollar_volume=Decimal("485000000"),
        event_time=NOW, received_time=NOW, processed_time=NOW,
    )


def test_critic_rejects_against_the_evidence_and_asks_revision_on_divergence() -> None:
    critic = DeterministicCritic(FixedClock(NOW), Decimal("20"))
    proposal = make_proposal(NOW)
    assert critic.review(proposal, _snapshot()).verdict == "APPROVE"  # no intelligence: unchanged
    approve = critic.review(proposal, _snapshot(), intelligence=BULL, features=_features())
    assert approve.verdict == "APPROVE"
    diverging = {**BULL, "breadth": {**BULL["breadth"], "divergences": ["megacap_only_rally"]}}
    revise = critic.review(proposal, _snapshot(), intelligence=diverging, features=_features())
    assert revise.verdict == "REVISE"
    stale = {**BULL, "macro": {"gate": "macro_calendar_unavailable", "upcoming": []}}
    reject = critic.review(proposal, _snapshot(), intelligence=stale, features=_features())
    assert reject.verdict == "REJECT"
    assert any(c.startswith("data_quality:") for c in reject.critical_conflicts)
