from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.core.context import AgentContextAssembler, ContextAssemblyError
from trading_bot.data.features import FeatureEngine
from trading_bot.schemas.trading import MarketSnapshot

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _snapshot(symbol: str = "QQQ", event_time: datetime = NOW) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        bid=Decimal("99"),
        ask=Decimal("100"),
        last=Decimal("100"),
        session_volume=Decimal("1000"),
        session_dollar_volume=Decimal("100000"),
        event_time=event_time,
        received_time=event_time,
        processed_time=event_time,
    )


def test_context_assembler_limits_strategy_context_to_prior_assessments() -> None:
    prices = tuple(Decimal(str(x)) for x in (96, 97, 98, 99, 100))
    features = FeatureEngine().compute("QQQ", prices)
    context = AgentContextAssembler().assemble(
        agent_id="strategy",
        snapshot=_snapshot(),
        features=features,
        now=NOW,
    )
    assert context["asset"] == "QQQ"
    assert context["assessments"] == {}
    assert context["evidence_count"] == 0


def test_context_assembler_fails_on_stale_or_mismatched_data() -> None:
    prices = tuple(Decimal(str(x)) for x in (96, 97, 98, 99, 100))
    features = FeatureEngine().compute("QQQ", prices)
    assembler = AgentContextAssembler(stale_after_seconds=30)
    with pytest.raises(ContextAssemblyError, match="stale"):
        assembler.assemble(
            agent_id="market",
            snapshot=_snapshot(event_time=NOW - timedelta(seconds=31)),
            features=features,
            now=NOW,
        )
    mismatched = FeatureEngine().compute("SPY", prices)
    with pytest.raises(ContextAssemblyError, match="different assets"):
        assembler.assemble(
            agent_id="market", snapshot=_snapshot(), features=mismatched, now=NOW
        )
