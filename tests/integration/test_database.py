from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from trading_bot.db import AuditRepository, Database, create_sqlite_backup, verify_sqlite_file
from trading_bot.monitoring.retention import apply_retention, export_audit_bundle


async def test_sqlite_initialization_and_audit_round_trip(tmp_path) -> None:
    path = tmp_path / "control.db"
    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    healthy, detail = await database.healthcheck()

    identifier = await AuditRepository(database).append(
        "system_events",
        {"event": "boot"},
        created_at=datetime.now(UTC),
    )
    records = await AuditRepository(database).recent("system_events")
    await database.close()

    assert healthy is True
    assert detail == "ok"
    assert records[0]["id"] == identifier
    assert records[0]["payload"] == {"event": "boot"}


async def test_corrupt_database_fails_healthcheck_without_replacing_file(tmp_path) -> None:
    path = tmp_path / "corrupt.db"
    original = b"not-a-sqlite-database"
    path.write_bytes(original)
    database = Database(f"sqlite+aiosqlite:///{path}")

    healthy, detail = await database.healthcheck()
    await database.close()

    assert healthy is False
    assert detail
    assert path.read_bytes() == original


async def test_sqlite_initialization_adds_new_audit_columns_to_existing_file(tmp_path) -> None:
    path = tmp_path / "pre-migration.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE model_usage ("
            "id VARCHAR(36) PRIMARY KEY, agent_run_id VARCHAR(36), provider VARCHAR(80) NOT NULL, "
            "model VARCHAR(120) NOT NULL, billing_mode VARCHAR(40) NOT NULL, "
            "input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, "
            "cost_usd NUMERIC, fallback_reason VARCHAR(255), created_at DATETIME NOT NULL"
            ")"
        )

    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    async with database.engine.connect() as connection:
        result = await connection.exec_driver_sql("PRAGMA table_info(model_usage)")
        columns = {str(row[1]) for row in result.fetchall()}
    await database.close()

    assert "attempted_providers" in columns


async def test_sqlite_online_backup_is_verified_without_replacing_source(tmp_path) -> None:
    source_path = tmp_path / "control.db"
    backup_path = tmp_path / "backup" / "control-copy.db"
    database = Database(f"sqlite+aiosqlite:///{source_path}")
    await database.initialize()
    await AuditRepository(database).append(
        "system_events",
        {"event": "backup-proof"},
        created_at=datetime.now(UTC),
    )
    result = await create_sqlite_backup(database, backup_path)
    source_healthy, source_detail = await database.healthcheck()
    source_records = await AuditRepository(database).recent("system_events")
    await database.close()

    assert result.verified is True
    assert result.path == backup_path.resolve()
    assert result.size_bytes > 0
    assert verify_sqlite_file(backup_path) is True
    assert source_healthy is True
    assert source_detail == "ok"
    assert source_records[0]["payload"] == {"event": "backup-proof"}


async def test_retention_export_has_manifest_and_dry_run_does_not_delete(tmp_path) -> None:
    path = tmp_path / "control.db"
    destination = tmp_path / "archive" / "audit.jsonl"
    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    now = datetime.now(UTC)
    repository = AuditRepository(database)
    await repository.append(
        "system_events",
        {"event": "old"},
        created_at=now - timedelta(days=400),
    )
    await repository.append("system_events", {"event": "new"}, created_at=now)

    exported = await export_audit_bundle(database, destination, tables=("system_events",))
    dry_run = await apply_retention(
        database,
        now=now,
        retention_days={"system_events": 365},
        dry_run=True,
    )
    records_before = await repository.recent("system_events", limit=10)
    await database.close()

    assert exported.rows == 2
    assert exported.data_path == destination.resolve()
    assert exported.manifest_path.is_file()
    assert len(exported.sha256) == 64
    assert dry_run.dry_run is True
    assert dry_run.eligible_rows == 1
    assert dry_run.deleted_rows == 0
    assert len(records_before) == 2


async def test_retention_apply_deletes_only_expired_allowlisted_rows(tmp_path) -> None:
    path = tmp_path / "control.db"
    database = Database(f"sqlite+aiosqlite:///{path}")
    await database.initialize()
    now = datetime.now(UTC)
    repository = AuditRepository(database)
    await repository.append(
        "market_snapshots",
        {"event": "old"},
        created_at=now - timedelta(days=20),
    )
    await repository.append("market_snapshots", {"event": "new"}, created_at=now)
    result = await apply_retention(
        database,
        now=now,
        retention_days={"market_snapshots": 14},
        dry_run=False,
    )
    remaining = await repository.recent("market_snapshots", limit=10)
    await database.close()

    assert result.dry_run is False
    assert result.eligible_rows == 1
    assert result.deleted_rows == 1
    assert [row["payload"] for row in remaining] == [{"event": "new"}]
