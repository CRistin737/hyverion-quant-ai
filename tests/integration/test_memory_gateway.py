from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from trading_bot.config import load_settings
from trading_bot.core.agent_pipeline import SpecialistAgentPipeline
from trading_bot.core.context import AssessmentBundle
from trading_bot.data.features import FeatureEngine
from trading_bot.db import Database
from trading_bot.memory import (
    CandidateStatus,
    MemoryProposal,
    MemoryType,
    SqlMemoryRepository,
    StrategicMemory,
)
from trading_bot.memory.gateway import UNTRUSTED_NOTICE, MemoryGateway
from trading_bot.memory.maintenance import MemoryConfigurationError, build_memory_gateway
from trading_bot.memory.retrieval import MemoryRetrievalEngine
from trading_bot.memory.vector import NullVectorBackend
from trading_bot.schemas.trading import MarketSnapshot

NOW = datetime(2026, 9, 23, tzinfo=UTC)


class _FakeVector:
    available = True

    def __init__(self, similarities: dict[str, float]) -> None:
        self._similarities = similarities

    async def upsert(self, memory_id: str, embedding: Sequence[float], *, model: str) -> None:
        return None

    async def nearest(self, embedding: Sequence[float], *, limit: int) -> list[tuple[str, float]]:
        return []

    async def similarities(
        self, embedding: Sequence[float], memory_ids: Sequence[str]
    ) -> dict[str, float]:
        return {key: value for key, value in self._similarities.items() if key in memory_ids}


class _FakeEmbedder:
    model = "fake"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class _BrokenRetrieval:
    async def retrieve(self, *_: Any, **__: Any) -> Any:
        raise ConnectionError("db down")


_OPEN: list[Database] = []


@pytest.fixture(autouse=True)
async def _close_databases():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _repository(tmp_path) -> SqlMemoryRepository:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'gateway.db'}")
    await database.initialize()
    _OPEN.append(database)
    return SqlMemoryRepository(database)


async def _store(
    repository: SqlMemoryRepository, memory_id: str, *, created: datetime = NOW, **updates: Any
) -> None:
    values: dict[str, Any] = {
        "id": memory_id,
        "knowledge_id": f"KNOW-{memory_id}",
        "title": f"Title {memory_id}",
        "summary": f"Summary {memory_id}",
        "category": MemoryType.PATTERN,
        "symbol": "QQQ",
        "market_regime": "RANGING",
        "confidence": Decimal("0.6"),
        "reliability": Decimal("0.6"),
        "importance": Decimal("0.5"),
        "valid_from": created,
        "created_at": created,
        "updated_at": created,
    }
    values.update(updates)
    await repository.create_strategic(
        StrategicMemory.model_validate(values),
        content=memory_id,
        created_by="curator",
        change_reason="test",
    )


def _gateway(repository: SqlMemoryRepository, **engine: Any) -> MemoryGateway:
    retrieval = MemoryRetrievalEngine(
        repository=repository, vector=engine.pop("vector", NullVectorBackend()), **engine
    )
    return MemoryGateway(repository=repository, retrieval=retrieval)


async def test_metadata_ranking_is_degraded_and_time_aware(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await _store(repository, "reliable", reliability=Decimal("0.9"))
    await _store(repository, "weak", reliability=Decimal("0.3"))
    await _store(repository, "global", symbol=None, market_regime=None, reliability=Decimal("0.9"))
    await _store(repository, "future", created=NOW + timedelta(days=5), reliability=Decimal("1"))

    capsule = await _gateway(repository).context(
        agent_id="strategy",
        query="breakout",
        as_of=NOW + timedelta(hours=1),
        symbol="QQQ",
        market_regime="RANGING",
    )

    assert [item.memory_id for item in capsule.items] == ["reliable", "global", "weak"]
    assert capsule.degraded and not capsule.semantic
    assert capsule.reason == "semantic_index_unavailable"
    context = capsule.to_context()
    assert context["notice"] == UNTRUSTED_NOTICE
    assert all(item["memory_id"] != "future" for item in context["items"])


async def test_semantic_similarity_reranks_but_cannot_add_candidates(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await _store(repository, "similar")
    await _store(repository, "dissimilar")
    await _store(repository, "unindexed")
    await _store(repository, "other-asset", symbol="SPY")
    vector = _FakeVector({"similar": 0.9, "dissimilar": 0.1, "other-asset": 1.0})

    capsule = await _gateway(repository, vector=vector, embedder=_FakeEmbedder()).context(
        agent_id="critic", query="x", as_of=NOW, symbol="QQQ"
    )

    assert capsule.semantic and not capsule.degraded
    assert [item.memory_id for item in capsule.items] == ["similar", "unindexed", "dissimilar"]


async def test_budgets_least_privilege_and_sanitization(tmp_path) -> None:
    repository = await _repository(tmp_path)
    for index in range(8):
        await _store(
            repository,
            f"m{index}",
            title="Line one\nline​ two\x07",
            summary="S" * 450,
        )
    gateway = _gateway(repository)

    news = await gateway.context(agent_id="news", query="x", as_of=NOW)
    assert news.items == () and news.reason == "agent_not_authorized"

    regime = await gateway.context(agent_id="regime", query="x", as_of=NOW)
    assert len(regime.items) == 3
    assert regime.items[0].title == "Line one line two"
    assert len(regime.items[0].summary) == 280

    strategy = await gateway.context(agent_id="strategy", query="x", as_of=NOW)
    # 5 items would exceed the 1800-character budget with 280-char summaries.
    assert sum(len(i.title) + len(i.summary) for i in strategy.items) <= 1800
    assert 1 <= len(strategy.items) < 8


async def test_retrieval_failure_degrades_without_raising(tmp_path) -> None:
    repository = await _repository(tmp_path)
    gateway = MemoryGateway(repository=repository, retrieval=_BrokenRetrieval())  # type: ignore[arg-type]

    capsule = await gateway.context(agent_id="strategy", query="x", as_of=NOW)

    assert capsule.degraded and capsule.items == ()
    assert capsule.reason == "retrieval_failed:ConnectionError"


async def test_usage_is_recorded_for_every_item_shown(tmp_path) -> None:
    from sqlalchemy import func, select

    from trading_bot.db.models import memory_usage

    repository = await _repository(tmp_path)
    await _store(repository, "a")
    await _store(repository, "b")
    await _gateway(repository).context(
        agent_id="strategy", query="x", as_of=NOW, decision_id="decision-1"
    )

    async with repository._database.engine.connect() as connection:
        count = (
            await connection.execute(select(func.count()).select_from(memory_usage))
        ).scalar_one()
    assert count == 2


async def test_agent_proposals_become_pending_candidates(tmp_path) -> None:
    repository = await _repository(tmp_path)
    proposal = MemoryProposal(
        memory_type=MemoryType.OBSERVATION,
        title="Spread widened\nbefore stop",
        summary="Observed twice.",
        content="details",
        agent_id="critic",
        source_type="operation",
        source_ids=("op-1", "op-2"),
        symbol="QQQ",
        created_at=NOW,
    )

    candidate = await _gateway(repository).propose(proposal)

    stored = await repository.get_candidate(candidate.id)
    assert stored is not None
    assert stored.status is CandidateStatus.PENDING
    assert stored.title == "Spread widened before stop"
    assert stored.confidence == Decimal("0")
    assert [link.source_id for link in await repository.evidence(candidate.id)] == [
        "op-1",
        "op-2",
    ]


async def test_factory_is_degraded_on_sqlite_and_strict_in_production(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'factory.db'}")
    settings = load_settings()
    gateway = await build_memory_gateway(settings, database)
    assert isinstance(gateway, MemoryGateway)

    production = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "app": settings.public.app.model_copy(update={"environment": "production"})
                }
            )
        }
    )
    with pytest.raises(MemoryConfigurationError):
        await build_memory_gateway(production, database)


class _CapturingRuntime:
    def __init__(self) -> None:
        self.contexts: dict[str, dict[str, Any]] = {}

    async def invoke(self, *, agent_id: str, context: dict[str, Any], **_: Any) -> Any:
        self.contexts[agent_id] = context
        return SimpleNamespace(result=SimpleNamespace(output=None))


@pytest.mark.asyncio
async def test_pipeline_gives_capsules_only_to_budgeted_agents(tmp_path) -> None:
    repository = await _repository(tmp_path)
    await _store(repository, "lesson")
    runtime = _CapturingRuntime()
    pipeline = SpecialistAgentPipeline(
        runtime,  # type: ignore[arg-type]
        memory_gateway=_gateway(repository),
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
    for agent_id in ("market", "strategy"):
        await pipeline._invoke(agent_id, snapshot, features, AssessmentBundle(), NOW, dict, None)

    assert "memory" not in runtime.contexts["market"]
    memory = runtime.contexts["strategy"]["memory"]
    assert memory["notice"] == UNTRUSTED_NOTICE
    assert [item["memory_id"] for item in memory["items"]] == ["lesson"]


async def test_embedding_model_is_loaded_once_per_process(tmp_path, monkeypatch) -> None:
    import trading_bot.memory.maintenance as factory

    loads: list[object] = []

    class _Provider:
        model = "fake"

        def __init__(self, model_dir: object) -> None:
            loads.append(model_dir)

    monkeypatch.setattr(factory, "LocalEmbeddingProvider", _Provider)
    monkeypatch.setattr(factory, "_PROVIDERS", {})
    for _ in range(3):
        await factory._embedding_provider(tmp_path)
    assert len(loads) == 1
