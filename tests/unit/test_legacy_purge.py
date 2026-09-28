"""The crypto era is removed for good; QQQ and non-asset rows are untouched."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from trading_bot.db.database import Database
from trading_bot.db.models import operation_events, operations
from trading_bot.db.purge import PURGE_EVENT, purge_legacy_assets
from trading_bot.db.repositories import AuditRepository

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)


async def _seed(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'data' / 'bot.db'}")
    await database.initialize()
    audit = AuditRepository(database)
    await audit.append(
        "trade_proposals", {"status": "X"}, asset="BTC/USDT", created_at=NOW
    )
    await audit.append(
        "trade_proposals", {"status": "X"}, asset="QQQ", created_at=NOW
    )
    await audit.append(
        "system_events", {"status": "X"}, asset="ETH/USDT", created_at=NOW
    )
    await audit.append(
        "system_events", {"status": "X"}, asset="anthropic", created_at=NOW
    )
    await audit.append(
        "candles", {"status": "X"}, asset="BTC/USDT", created_at=NOW
    )
    async with database.engine.begin() as connection:
        await connection.execute(
            insert(operations).values(
                id="op-btc",
                proposal_id="p",
                asset="BTC/USDT",
                mode="paper",
                state="REJECTED",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await connection.execute(
            insert(operation_events).values(
                id="ev-1",
                operation_id="op-btc",
                sequence=1,
                to_state="REJECTED",
                requested_state="REJECTED",
                legal=True,
                reason="r",
                details={},
                occurred_at=NOW,
            )
        )
    return database


@pytest.mark.asyncio
async def test_dry_run_only_counts(tmp_path: Path) -> None:
    database = await _seed(tmp_path)
    report = await purge_legacy_assets(database, apply=False)
    assert report.counts == {
        "trade_proposals": 1,
        "system_events": 1,
        "candles": 1,
        "operations": 1,
        "operation_events": 1,
    }
    assert report.backup_path is None
    assert len(await AuditRepository(database).recent("trade_proposals", limit=10)) == 2
    await database.close()


@pytest.mark.asyncio
async def test_apply_backs_up_then_keeps_only_the_current_era(tmp_path: Path) -> None:
    database = await _seed(tmp_path)
    report = await purge_legacy_assets(database, apply=True)
    assert report.backup_path is not None and report.backup_path.exists()
    audit = AuditRepository(database)
    assert [row["asset"] for row in await audit.recent("trade_proposals", limit=10)] == ["QQQ"]
    assert await audit.recent("candles", limit=10) == []
    events = await audit.recent("system_events", limit=10)
    assert {row["asset"] for row in events} == {"anthropic", None}
    assert any(row["payload"].get("status") == PURGE_EVENT for row in events)
    async with database.engine.connect() as connection:
        assert (await connection.execute(select(operations))).first() is None
        assert (await connection.execute(select(operation_events))).first() is None
    # Nothing left: a second run is a no-op without a new backup.
    again = await purge_legacy_assets(database, apply=True)
    assert again.total == 0 and again.backup_path is None
    await database.close()
