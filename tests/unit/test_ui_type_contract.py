"""Contract between the hand-written TypeScript types (app/src/api/types.ts) and the core.

The desktop app types its API responses by hand. These tests fail when a field is
renamed or added on either side, so a drift is caught in CI instead of in the UI.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import control_client
from test_engine_supervisor import _settings, _supervisor

from trading_bot.config.manager import ConfigPatch
from trading_bot.control_api import create_control_api
from trading_bot.engine_supervisor import EngineStatus
from trading_bot.memory.models import MemoryConflict
from trading_bot.providers.login_flow import LoginSession
from trading_bot.schemas.learning import ChangeProposalTransitionRequest
from trading_bot.schemas.trading import OperationResolutionRequest

TYPES = (Path(__file__).parents[2] / "app" / "src" / "api" / "types.ts").read_text()


def ts_fields(interface: str) -> dict[str, bool]:
    """Field name -> optional, for one exported interface (index signatures ignored)."""

    match = re.search(rf"export interface {interface} \{{\n(.*?)\n\}}", TYPES, re.S)
    assert match, f"interface {interface} not found in types.ts"
    fields: dict[str, bool] = {}
    for line in match.group(1).splitlines():
        field = re.match(r"\s{2}(\w+)(\??):", line)
        if field:
            fields[field.group(1)] = field.group(2) == "?"
    return fields


def test_config_patch_fields_exist_in_the_core_model() -> None:
    ui = ts_fields("ConfigPatch")
    core = set(ConfigPatch.model_fields)
    assert set(ui) <= core, f"UI sends fields the core rejects: {set(ui) - core}"
    assert all(ui.values()), "every ConfigPatch field is optional (partial patch)"


def test_engine_status_matches_the_core_payload(tmp_path) -> None:
    body = EngineStatus("stopped", None, None, None, None, None).as_dict()
    body["flatten_pending"] = False  # added by the API endpoint
    assert set(ts_fields("EngineStatus")) == set(body)
    settings = _settings(tmp_path)
    with control_client(create_control_api(settings, engine=_supervisor(settings))) as client:
        assert set(client.get("/api/v1/engine").json()) == set(body)


def test_login_session_matches_the_core_payload() -> None:
    assert set(ts_fields("LoginSession")) == set(LoginSession("anthropic").as_dict())


def test_memory_conflict_matches_the_core_model() -> None:
    assert set(ts_fields("MemoryConflict")) == set(MemoryConflict.model_fields)


@pytest.mark.asyncio
async def test_memory_candidate_matches_the_projection(tmp_path) -> None:
    from trading_bot.core.clock import FixedClock
    from trading_bot.db.database import Database
    from trading_bot.memory.models import MemoryCandidate, MemoryType
    from trading_bot.memory.operations import MemoryOperations
    from trading_bot.memory.repository import SqlMemoryRepository

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    await database.initialize()
    try:
        now = datetime(2026, 9, 26, tzinfo=UTC)
        await SqlMemoryRepository(database).add_candidate(
            MemoryCandidate(
                id="cand-1",
                memory_type=MemoryType.HYPOTHESIS,
                title="t",
                summary="s",
                content="c",
                source_type="trade_review",
                source_ids=("op-1",),
                confidence=Decimal("0.5"),
                importance=Decimal("0.5"),
                novelty=Decimal("0.5"),
                evidence_strength=Decimal("0.5"),
                created_at=now,
            )
        )
        rows = await MemoryOperations(database=database, clock=FixedClock(now)).candidates()
    finally:
        await database.close()
    assert rows and set(ts_fields("MemoryCandidate")) == set(rows[0])


def test_setup_status_matches_the_endpoint(tmp_path) -> None:
    settings = _settings(tmp_path)
    with control_client(create_control_api(settings, engine=_supervisor(settings))) as client:
        body = client.get("/api/v1/setup/status").json()
    assert set(ts_fields("SetupStatus")) == set(body)


def test_request_bodies_the_ui_sends_are_accepted() -> None:
    # Shapes built in app/src (Trading resolve, Aprendizaje transition).
    OperationResolutionRequest.model_validate({"target": "CLOSED", "reason": "cerrada en venue"})
    ChangeProposalTransitionRequest.model_validate({"target": "TESTING", "reason": "probar"})


def test_snapshot_interface_matches_the_pinned_keys() -> None:
    import json

    pinned = json.loads((Path(__file__).parents[2] / "app/src/api/snapshot-keys.json").read_text())
    # snapshot-keys.json is itself compared with the live endpoint in test_control_api.
    assert set(ts_fields("Snapshot")) == set(pinned)


def test_trade_row_matches_the_projection() -> None:
    from trading_bot.core.trade_view import build_trades, rejected_entries

    row = build_trades([{"id": "op", "state": "OPEN", "asset": "QQQ"}], [], [])[0]
    assert set(ts_fields("Trade")) == set(row)
    rejected = rejected_entries([{"id": "op", "state": "REJECTED", "asset": "QQQ"}], {}, {})[0]
    assert set(ts_fields("RejectedEntry")) == set(rejected)


def test_learning_and_version_payloads_match_the_core(tmp_path) -> None:
    from test_versions_and_optimizer import _strategy_spec

    settings = _settings(tmp_path)
    request = {
        "agent": "trend_pullback",
        "current_version": "1.0.0",
        "candidate_version": "1.1.0",
        "reason": "Pierde en lateral.",
        "evidence": ["e"],
        "affected_rules": ["No operar en mercado lateral"],
        "expected_improvement": "Menos pérdidas.",
        "risk": "Opera menos.",
        "candidate_spec": _strategy_spec("trend_pullback", blocked_regimes=["ranging"]),
    }
    with control_client(create_control_api(settings, engine=_supervisor(settings))) as client:
        proposal_id = client.post("/api/v1/learning/proposals", json=request).json()["id"]
        item = client.get("/api/v1/learning/timeline").json()[0]
        components = client.get("/api/v1/components").json()
        history = client.get("/api/v1/components/trend_pullback/history").json()
        optimizer = client.get("/api/v1/learning/optimizer").json()
        spec = client.get("/api/v1/agents/news/spec").json()
    assert proposal_id == item["id"]
    ui_change = ts_fields("ChangeItem")
    assert {k for k, optional in ui_change.items() if not optional} == set(item)
    assert set(ts_fields("ReviewStep")) == set(item["history"][0])
    assert set(ts_fields("ComponentVersion")) == set(components[0])
    assert set(ts_fields("VersionEntry")) == set(history[0])
    assert set(ts_fields("VersionResults")) == set(history[0]["results"])
    assert set(ts_fields("StrategyParams")) == set(history[0]["params"])
    assert set(ts_fields("OptimizerStatus")) == set(optimizer)
    assert set(ts_fields("AgentSpec")) == set(spec)


def test_market_session_matches_the_core_payload(tmp_path) -> None:
    from trading_bot.config import load_settings

    settings = load_settings()
    public = settings.public.model_copy(
        update={
            "database": settings.public.database.model_copy(
                update={"url": f"sqlite+aiosqlite:///{tmp_path / 's.db'}"}
            )
        }
    )
    app = create_control_api(settings.model_copy(update={"public": public}))
    with control_client(app) as client:
        body = client.get("/api/v1/market/session").json()
    assert set(ts_fields("MarketSession")) == set(body)
    assert body["timezone"] == "America/New_York"


def test_broker_status_matches_the_core_payload(tmp_path) -> None:
    from trading_bot.config import load_settings

    settings = load_settings()
    public = settings.public.model_copy(
        update={
            "database": settings.public.database.model_copy(
                update={"url": f"sqlite+aiosqlite:///{tmp_path / 'b.db'}"}
            )
        }
    )
    app = create_control_api(settings.model_copy(update={"public": public}))
    with control_client(app) as client:
        body = client.get("/api/v1/broker/status").json()
    assert set(ts_fields("BrokerStatus")) == set(body)
