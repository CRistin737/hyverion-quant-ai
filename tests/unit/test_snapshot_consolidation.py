"""The snapshot and the learning timeline consolidate append-only rows.

Positions and candles are appended on every cycle and proposals on every review
transition, so readers must keep one row per entity and the right latest state.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from conftest import control_client

from trading_bot.config import load_settings
from trading_bot.config.models import ExternalDataConfig
from trading_bot.control_api import create_control_api
from trading_bot.data.sources import default_source_statuses
from trading_bot.db import AuditRepository, Database

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _settings(tmp_path, name: str):
    base = load_settings()
    return base.model_copy(
        update={
            "public": base.public.model_copy(
                update={
                    "database": base.public.database.model_copy(
                        update={"url": f"sqlite+aiosqlite:///{tmp_path / name}"}
                    )
                }
            )
        }
    )


def _seed(settings, rows: list[tuple[str, dict, str | None, datetime]]) -> None:
    async def run() -> None:
        database = Database(settings.public.database.url)
        await database.initialize()
        repository = AuditRepository(database)
        for table, payload, asset, at in rows:
            await repository.append(
                table,
                payload,
                created_at=at,
                asset=asset,
                event_time=at,
                received_time=at,
                processed_time=at,
            )
        await database.close()

    asyncio.run(run())


def _position(position_id: str, status: str, state_at: datetime, price: str) -> dict:
    return {
        "position_id": position_id,
        "asset": "QQQ",
        "side": "BUY",
        "status": status,
        "entry_price": "100",
        "current_price": price,
        "quantity": "1",
        "state_at": state_at.isoformat(),
    }


def test_snapshot_keeps_one_row_per_position_and_hides_closed_ones(tmp_path) -> None:
    settings = _settings(tmp_path, "positions.db")
    t1, t2, t3 = NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2)
    _seed(
        settings,
        [
            ("positions", _position("p-closed", "OPEN", t1, "100"), "QQQ", t1),
            ("positions", _position("p-closed", "OPEN", t2, "101"), "QQQ", t2),
            ("positions", _position("p-closed", "CLOSED", t3, "102"), "QQQ", t3),
            ("positions", _position("p-open", "OPEN", t1, "100"), "QQQ", t1),
            ("positions", _position("p-open", "OPEN", t3, "103"), "QQQ", t3),
        ],
    )
    with control_client(create_control_api(settings)) as client:
        body = client.get("/api/v1/snapshot").json()

    assert [row["position_id"] for row in body["positions"]] == ["p-open"]
    assert body["positions"][0]["current_price"] == "103"
    history = {row["position_id"]: row["status"] for row in body["position_history"]}
    assert history == {"p-open": "OPEN", "p-closed": "CLOSED"}
    assert len(body["position_history"]) == 2


def test_snapshot_candles_are_unique_per_close_time(tmp_path) -> None:
    settings = _settings(tmp_path, "candles.db")
    first, second = NOW, NOW + timedelta(minutes=1)
    rows: list[tuple[str, dict, str | None, datetime]] = [
        ("market_snapshots", {"symbol": "QQQ", "last": "100"}, "QQQ", second),
    ]
    # The engine re-appends the latest closed candles on every cycle.
    for cycle in range(3):
        for close_time, close in ((first, "100"), (second, "101")):
            rows.append(
                (
                    "candles",
                    {
                        "symbol": "QQQ",
                        "close": close,
                        "event_time": close_time.isoformat(),
                    },
                    "QQQ",
                    NOW + timedelta(seconds=cycle),
                )
            )
    _seed(settings, rows)
    with control_client(create_control_api(settings)) as client:
        market = client.get("/api/v1/snapshot").json()["markets"][0]

    assert [candle["close"] for candle in market["candles"]] == ["100", "101"]


def test_proposal_review_chain_keeps_state_and_timeline(tmp_path) -> None:
    settings = _settings(tmp_path, "learning.db")
    request = {
        "agent": "strategy",
        "current_version": "1.0.0",
        "candidate_version": "1.1.0",
        "reason": "Pierde en mercado lateral.",
        "evidence": ["experiment:1"],
        "affected_rules": ["blocked_regimes"],
        "expected_improvement": "Menos entradas perdedoras.",
        "risk": "Opera menos veces.",
        "candidate_spec": {"blocked_regimes": ["range"]},
    }
    with control_client(create_control_api(settings)) as client:
        proposal_id = client.post("/api/v1/learning/proposals", json=request).json()["id"]
        # Every step reads the state written by the previous transition, which
        # carries a transition_reason that the proposal schema does not accept.
        for target in ("TESTING", "READY_FOR_REVIEW", "APPROVED"):
            response = client.post(
                f"/api/v1/learning/proposals/{proposal_id}/transition",
                json={"target": target, "reason": f"paso {target.lower()}"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == target
        timeline = client.get("/api/v1/learning/timeline").json()

    assert len(timeline) == 1
    item = timeline[0]
    assert item["status"] == "APPROVED"
    assert [step["status"] for step in item["history"]] == [
        "PROPOSED",
        "TESTING",
        "READY_FOR_REVIEW",
        "APPROVED",
    ]
    assert item["history"][-1]["reason"] == "paso approved"


def test_social_source_reflects_the_configured_official_api() -> None:
    config = load_settings().public.external_data

    def social(cfg: ExternalDataConfig):
        return next(s for s in default_source_statuses(cfg) if s.category == "social")

    assert social(config.model_copy(update={"social_provider": "disabled"})).enabled is False
    enabled = social(config.model_copy(update={"social_provider": "reddit"}))
    assert enabled.enabled is True
    assert enabled.last_error is None
