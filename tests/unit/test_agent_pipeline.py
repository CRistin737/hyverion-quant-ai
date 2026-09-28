from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from trading_bot.core.agent_pipeline import SpecialistAgentPipeline
from trading_bot.data.features import FeatureEngine
from trading_bot.providers.base import ProviderError
from trading_bot.schemas.trading import MarketSnapshot, StrategyOutput

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


def _snapshot(event_time: datetime = NOW) -> MarketSnapshot:
    return MarketSnapshot(
        symbol="QQQ",
        bid=Decimal("99"),
        ask=Decimal("100"),
        last=Decimal("100"),
        session_volume=Decimal("1000"),
        session_dollar_volume=Decimal("100000"),
        event_time=event_time,
        received_time=event_time,
        processed_time=event_time,
    )


def _features():
    return FeatureEngine().compute(
        "QQQ", tuple(Decimal(str(value)) for value in (96, 97, 98, 99, 100))
    )


class _FailingRuntime:
    calls: list[str]

    def __init__(self, error: ProviderError) -> None:
        self.error = error
        self.calls = []

    async def invoke(self, *, agent_id: str, **_: Any) -> Any:
        self.calls.append(agent_id)
        raise self.error


def test_strategy_output_requires_a_typed_decision() -> None:
    with pytest.raises(ValidationError):
        StrategyOutput(decision="TRADE")
    with pytest.raises(ValidationError):
        StrategyOutput(decision="NO_TRADE")


@pytest.mark.asyncio
async def test_specialist_pipeline_fails_closed_on_provider_error() -> None:
    runtime = _FailingRuntime(ProviderError("provider_timeout", retryable=True))
    pipeline = SpecialistAgentPipeline(runtime)  # type: ignore[arg-type]

    result = await pipeline.run(snapshot=_snapshot(), features=_features(), now=NOW)

    assert result.status == "FAILED"
    assert result.failure_code == "provider_timeout"
    assert result.why_not_trade == ("specialist_pipeline_failed",)
    assert runtime.calls == ["market"]


@pytest.mark.asyncio
async def test_specialist_pipeline_rejects_stale_context_before_provider_call() -> None:
    runtime = _FailingRuntime(ProviderError("should_not_run", retryable=False))
    pipeline = SpecialistAgentPipeline(runtime)  # type: ignore[arg-type]

    result = await pipeline.run(
        snapshot=_snapshot(event_time=NOW - timedelta(seconds=31)),
        features=_features(),
        now=NOW,
    )

    assert result.status == "FAILED"
    assert result.failure_code == "context_assembly_failed"
    assert runtime.calls == []
