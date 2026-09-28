from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from trading_bot.agents.registry import AgentRegistry
from trading_bot.agents.runtime import AgentRuntime
from trading_bot.core.clock import FixedClock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.providers.router import ModelRouter, StaticJSONProvider


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str


async def test_registry_loads_all_neutral_specs() -> None:
    descriptors = AgentRegistry().descriptors()
    # 11 since the QQQ migration retired the crypto derivatives agent.
    assert len(descriptors) == 17
    assert {item.agent_id for item in descriptors} >= {"market", "strategy", "critic"}
    assert all(len(item.spec_hash) == 64 for item in descriptors)
    assert all(
        "INPUTS" not in item.role and "CHANGE HISTORY" not in item.objective
        for item in descriptors
    )


async def test_runtime_persists_typed_run_usage_and_output(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    await database.initialize()
    metrics = MetricsRegistry()
    router = ModelRouter(
        [StaticJSONProvider({"decision": "NO_TRADE"})],
        {"STANDARD": "replay-model"},
        metrics=metrics,
    )
    runtime = AgentRuntime(
        registry=AgentRegistry(),
        router=router,
        repository=AuditRepository(database),
        clock=FixedClock(datetime(2026, 9, 14, 12, 0, tzinfo=UTC)),
        metrics=metrics,
    )

    invocation = await runtime.invoke(
        agent_id="strategy",
        profile="STANDARD",
        context={"asset": "QQQ", "external": "data only"},
        output_schema=Output,
    )

    assert invocation.run.status == "SUCCEEDED"
    assert invocation.result.output == Output(decision="NO_TRADE")
    runs = await AuditRepository(database).recent("agent_runs", limit=5)
    outputs = await AuditRepository(database).recent("agent_outputs", limit=5)
    usage = await AuditRepository(database).recent("model_usage", limit=5)
    assert runs[0]["status"] == "SUCCEEDED"
    assert outputs[0]["payload"]["context_digest"]
    assert outputs[0]["payload"]["output"] == {"decision": "NO_TRADE"}
    assert usage[0]["attempted_providers"] == ["static"]
    assert usage[0]["billing_mode"] == "subscription"
    assert await AuditRepository(database).daily_model_cost(
        now=datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    ) == Decimal("0")
    metric_names = {sample.name for sample in metrics.snapshot()}
    assert {
        "agent_runs_started_total",
        "agent_runs_succeeded_total",
        "ai_provider_calls_total",
    } <= metric_names
    await database.close()


async def test_runtime_persists_ordered_subscription_failover_metadata(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'fallback-runtime.db'}")
    await database.initialize()

    class NamedProvider(StaticJSONProvider):
        def __init__(self, provider_id: str, output: object) -> None:
            super().__init__(output)
            self.provider_id = provider_id

    router = ModelRouter(
        [
            NamedProvider("gemini_subscription", RuntimeError("private detail")),
            NamedProvider("codex_subscription", {"decision": "NO_TRADE"}),
        ],
        {"STANDARD": "cli-default-profile"},
        max_schema_retries=0,
    )
    runtime = AgentRuntime(
        registry=AgentRegistry(),
        router=router,
        repository=AuditRepository(database),
        clock=FixedClock(datetime(2026, 9, 14, 12, 0, tzinfo=UTC)),
    )

    await runtime.invoke(
        agent_id="strategy",
        profile="STANDARD",
        context={"asset": "QQQ"},
        output_schema=Output,
    )

    usage = await AuditRepository(database).recent("model_usage", limit=5)
    output_rows = await AuditRepository(database).recent("agent_outputs", limit=5)
    assert usage[0]["attempted_providers"] == ["gemini_subscription", "codex_subscription"]
    assert usage[0]["fallback_reason"] == "gemini_subscription:provider_runtime_error"
    assert output_rows[0]["payload"]["attempted_providers"] == [
        "gemini_subscription",
        "codex_subscription",
    ]
    await database.close()


async def test_daily_model_cost_is_durable_and_utc_scoped(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'daily-cost.db'}")
    await database.initialize()
    repository = AuditRepository(database)
    for run_id in ("run-a", "run-b", "run-c"):
        await repository.start_agent_run(
            agent_id="strategy",
            agent_version="1.0.0",
            started_at=datetime(2026, 9, 14, 12, 0, tzinfo=UTC),
            run_id=run_id,
        )
    await repository.record_model_usage(
        agent_run_id="run-a",
        provider="openai",
        model="api-model",
        billing_mode="api",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.75"),
        created_at=datetime(2026, 9, 14, 23, 59, tzinfo=UTC),
    )
    await repository.record_model_usage(
        agent_run_id="run-b",
        provider="openai",
        model="api-model",
        billing_mode="api",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("1.25"),
        created_at=datetime(2026, 9, 15, 0, 1, tzinfo=UTC),
    )
    await repository.record_model_usage(
        agent_run_id="run-c",
        provider="claude_subscription",
        model="cli",
        billing_mode="subscription",
        input_tokens=0,
        output_tokens=0,
        cost_usd=None,
        created_at=datetime(2026, 9, 15, 0, 2, tzinfo=UTC),
    )

    assert await repository.daily_model_cost(
        now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
    ) == Decimal("1.25")
    assert await repository.daily_model_cost(
        now=datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    ) == Decimal("0.75")
    await database.close()
