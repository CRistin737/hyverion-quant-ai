from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from conftest import make_critic, make_proposal

from trading_bot.core.agent_pipeline import SpecialistAgentPipeline
from trading_bot.core.clock import FixedClock
from trading_bot.data.features import FeatureEngine
from trading_bot.db import AuditRepository, Database
from trading_bot.memory import MemoryType, MemoryUsage, SqlMemoryRepository, StrategicMemory
from trading_bot.memory.meta import ATTRIBUTION_TABLE, MetaMemoryAnalyzer, attribution_payload
from trading_bot.schemas.assessments import (
    MarketAssessment,
    NewsAssessment,
    RegimeAssessment,
    SocialAssessment,
    TechnicalAssessment,
)
from trading_bot.schemas.common import MarketRegime, Side
from trading_bot.schemas.trading import MarketSnapshot, StrategyOutput

NOW = datetime(2026, 9, 23, tzinfo=UTC)
_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _setup(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'meta.db'}")
    await database.initialize()
    _OPEN.append(database)
    repository = SqlMemoryRepository(database)
    audit = AuditRepository(database)
    analyzer = MetaMemoryAnalyzer(repository=repository, audit=audit, clock=FixedClock(NOW))
    return repository, audit, analyzer


def _base(agent_id: str) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "agent_version": "1.0.0",
        "asset": "QQQ",
        "assessed_at": NOW,
        "confidence": Decimal("0.7"),
    }


def _market(structure: str = "bullish") -> MarketAssessment:
    return MarketAssessment(
        **_base("market"),
        spread_bps=Decimal("1"),
        liquidity_usd=Decimal("100000"),
        volatility_percent=Decimal("1"),
        data_fresh=True,
        structure=structure,  # type: ignore[arg-type]
    )


def _regime(regime: MarketRegime = MarketRegime.TRENDING_DOWN) -> RegimeAssessment:
    return RegimeAssessment(**_base("regime"), regime=regime)


def _news(sentiment: str = "neutral") -> NewsAssessment:
    return NewsAssessment(
        **_base("news"),
        status="available",
        sentiment=sentiment,  # type: ignore[arg-type]
        severity=Decimal("0.1"),
        stale_items=0,
        duplicate_items=0,
    )


async def _evaluation(audit: AuditRepository, trade_id: str, pnl: str, *, shadow=False) -> None:
    payload: dict[str, object] = {
        "trade_id": trade_id,
        "realized_net_pnl": "0" if shadow else pnl,
        "evaluated_at": NOW.isoformat(),
    }
    if shadow:
        payload["shadow_comparison"] = {"shadow_trade_pnl_usd": pnl}
    await audit.append("trade_evaluations", payload, created_at=NOW, asset="QQQ")


async def _attribution(
    audit: AuditRepository, decision_id: str, stances: dict[str, str], *, side: str = "buy"
) -> None:
    await audit.append(
        ATTRIBUTION_TABLE,
        {
            "decision_id": decision_id,
            "side": side,
            "market_regime": "RANGING",
            "stances": {agent: {"stance": stance} for agent, stance in stances.items()},
        },
        created_at=NOW,
        asset="QQQ",
    )


async def _memory(repository: SqlMemoryRepository, memory_id: str, category: MemoryType) -> None:
    memory = StrategicMemory.model_validate(
        {
            "id": memory_id,
            "knowledge_id": f"KNOW-{memory_id}",
            "title": f"memory {memory_id}",
            "summary": "summary",
            "category": category,
            "symbol": "QQQ",
            "confidence": Decimal("0.6"),
            "reliability": Decimal("0.6"),
            "importance": Decimal("0.5"),
            "valid_from": NOW - timedelta(days=1),
            "created_at": NOW - timedelta(days=1),
            "updated_at": NOW - timedelta(days=1),
        }
    )
    await repository.create_strategic(
        memory, content="body", created_by="curator", change_reason="x"
    )


async def _shown(repository: SqlMemoryRepository, memory_id: str, decision_id: str) -> None:
    await repository.record_usage(
        MemoryUsage(
            id=str(uuid4()),
            memory_id=memory_id,
            decision_id=decision_id,
            retrieval_score=Decimal("0.5"),
            used_in_prompt=True,
            created_at=NOW,
        )
    )


def test_attribution_records_directional_stances_only() -> None:
    payload = attribution_payload(
        proposal=make_proposal(NOW, proposal_id="op-1"),
        critic=make_critic(NOW, proposal_id="op-1", verdict="REVISE"),
        market=_market("bullish"),
        regime=_regime(MarketRegime.TRENDING_DOWN),
        news=_news("mixed"),
        social=None,
    )

    assert payload["decision_id"] == "op-1"
    assert payload["side"] == Side.BUY.value
    assert payload["market_regime"] == "TRENDING_DOWN"
    assert {agent: entry["stance"] for agent, entry in payload["stances"].items()} == {
        "strategy": "LONG",
        "critic": "REVISE",
        "market": "LONG",
        "regime": "SHORT",
        "news": "NEUTRAL",
    }


async def test_agent_reliability_is_scored_per_regime_against_outcomes(tmp_path) -> None:
    _, audit, analyzer = await _setup(tmp_path)
    await _evaluation(audit, "op-win", "3")
    await _evaluation(audit, "op-loss", "-2")
    await _attribution(
        audit, "op-win", {"market": "LONG", "critic": "APPROVE", "news": "NEUTRAL"}
    )
    await _attribution(
        audit, "op-loss", {"market": "LONG", "critic": "REJECT", "regime": "SHORT"}
    )
    await _attribution(audit, "op-pending", {"market": "SHORT"})  # never evaluated

    report = await analyzer.run()

    scores = {item.agent_id: (item.samples, item.reliability) for item in report.agents}
    assert scores == {
        "critic": (2, Decimal("0.75")),  # (2 + 1) / (2 + 2)
        "market": (2, Decimal("0.5")),
        "regime": (1, Decimal("0.6667")),  # the short call on a losing long was right
    }
    assert {item.market_regime for item in report.agents} == {"RANGING"}
    events = await audit.recent("system_events", limit=5)
    assert events[0]["payload"]["status"] == "MEMORY_META"


async def test_memory_value_score_and_shadow_counterfactual(tmp_path) -> None:
    repository, audit, analyzer = await _setup(tmp_path)
    await _evaluation(audit, "op-win", "3")
    await _evaluation(audit, "op-loss", "-2")
    await _evaluation(audit, "shadow-1", "-4", shadow=True)
    await _memory(repository, "good", MemoryType.HYPOTHESIS)
    await _memory(repository, "warn", MemoryType.FAILURE)
    await _memory(repository, "bad", MemoryType.HYPOTHESIS)
    await _memory(repository, "unused", MemoryType.HYPOTHESIS)
    await _shown(repository, "good", "op-win")
    await _shown(repository, "warn", "op-loss")
    await _shown(repository, "warn", "shadow-1")
    await _shown(repository, "bad", "op-loss")

    report = await analyzer.run()

    assert [item.memory_id for item in report.memories] == ["warn", "good", "bad"]
    values = {item.memory_id: item for item in report.memories}
    # The warning pointed the right way twice, including a rejected proposal
    # whose shadow replay would have lost 4 USD.
    assert values["warn"].hit_rate == Decimal("0.75")
    assert values["warn"].shadow_uses == 1
    assert values["warn"].avoided_loss_usd == Decimal("4.00")
    assert values["warn"].forgone_profit_usd == Decimal("0.00")
    assert values["good"].lift_usd == Decimal("4.00")  # 3 against a -1 baseline
    assert values["bad"].hit_rate == Decimal("0.3333")
    assert all(Decimal("0") <= item.value_score <= Decimal("1") for item in report.memories)


async def test_meta_memory_is_empty_without_evidence(tmp_path) -> None:
    _, _, analyzer = await _setup(tmp_path)
    report = await analyzer.run()
    assert (report.outcomes_considered, report.agents, report.memories) == (0, [], [])


class _ScriptedRuntime:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self._outputs = outputs

    async def invoke(self, *, agent_id: str, **_: Any) -> Any:
        return SimpleNamespace(result=SimpleNamespace(output=self._outputs[agent_id]))


async def test_ready_pipeline_records_the_attribution(tmp_path) -> None:
    _, audit, _ = await _setup(tmp_path)
    proposal = make_proposal(NOW, proposal_id="op-7")
    outputs = {
        "market": _market("bearish"),
        "technical": TechnicalAssessment(
            **_base("technical"),
            trend_score=Decimal("0.5"),
            momentum_score=Decimal("0.5"),
            mean_reversion_score=Decimal("0.5"),
            volatility_score=Decimal("0.5"),
        ),
        "regime": _regime(MarketRegime.RANGING),
        "news": _news("bullish"),
        "social": SocialAssessment(
            **_base("social"),
            status="insufficient_data",
            sentiment="unknown",
            manipulation_risk=Decimal("0"),
            unusual_activity=False,
        ),
        "strategy": StrategyOutput(decision="TRADE", proposal=proposal),
        "critic": make_critic(NOW, proposal_id="op-7"),
    }
    pipeline = SpecialistAgentPipeline(
        _ScriptedRuntime(outputs),  # type: ignore[arg-type]
        audit=audit,
    )
    snapshot = MarketSnapshot(
        symbol="QQQ",
        bid=Decimal("99"),
        ask=Decimal("100"),
        last=Decimal("100"),
        session_volume=Decimal("1000"),
        session_dollar_volume=Decimal("100000"),
        event_time=NOW,
        received_time=NOW,
        processed_time=NOW,
    )
    features = FeatureEngine().compute(
        "QQQ", tuple(Decimal(str(value)) for value in (96, 97, 98, 99, 100))
    )

    result = await pipeline.run(snapshot=snapshot, features=features, now=NOW)

    assert result.status == "READY"
    rows = await audit.recent(ATTRIBUTION_TABLE, limit=5)
    assert len(rows) == 1
    stances = rows[0]["payload"]["stances"]
    assert rows[0]["payload"]["decision_id"] == "op-7"
    assert stances["market"]["stance"] == "SHORT"
    assert stances["news"]["stance"] == "LONG"
    assert stances["social"]["stance"] == "NEUTRAL"
