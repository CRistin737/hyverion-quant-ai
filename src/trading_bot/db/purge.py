"""Remove the crypto era (BTC/USDT, ETH/USDT...) from the control-plane database.

Hyverion trades QQQ only; the rows left from the crypto prototype are not part
of the campaign and never will be traded again (owner decision, 2026-09-28).

A crypto row is recognised by its ``asset``: every crypto instrument was a
pair ("BTC/USDT"), and no equity symbol contains "/". Rows with other labels
(provider ids, source ids, "research") are left alone. The purge always makes
a verified backup first, and a dry run only counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Table, delete, func, insert, select

from trading_bot.db.backup import create_sqlite_backup, sqlite_path
from trading_bot.db.database import Database
from trading_bot.db.models import TABLES, operation_events, operations, system_events

PURGE_EVENT = "LEGACY_PURGED"


@dataclass(slots=True)
class PurgeReport:
    applied: bool
    counts: dict[str, int] = field(default_factory=dict)
    backup_path: Path | None = None

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _audit_tables() -> list[Table]:
    return [table for table in TABLES.values() if "asset" in table.c and "payload" in table.c]


def _is_legacy(table: Table):  # type: ignore[no-untyped-def]
    return table.c.asset.like("%/%")


async def purge_legacy_assets(
    database: Database, *, apply: bool, backup_dir: Path | None = None
) -> PurgeReport:
    """Count (dry run) or delete every crypto-era row; backup first when applying."""

    report = PurgeReport(applied=apply)
    async with database.engine.connect() as connection:
        for table in [*_audit_tables(), operations]:
            count = await connection.scalar(
                select(func.count()).select_from(table).where(_is_legacy(table))
            )
            if count:
                report.counts[table.name] = int(count)
        children = await connection.scalar(
            select(func.count())
            .select_from(operation_events)
            .where(
                operation_events.c.operation_id.in_(
                    select(operations.c.id).where(_is_legacy(operations))
                )
            )
        )
        if children:
            report.counts[operation_events.name] = int(children)
    if not apply or not report.counts:
        return report

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder = backup_dir or sqlite_path(database.url).parent.parent / "backups"
    backup = await create_sqlite_backup(database, folder / f"before-legacy-purge-{stamp}.db")
    report.backup_path = backup.path

    now = datetime.now(UTC)
    async with database.engine.begin() as connection:
        # Children first: operation_events reference operations.
        await connection.execute(
            delete(operation_events).where(
                operation_events.c.operation_id.in_(
                    select(operations.c.id).where(_is_legacy(operations))
                )
            )
        )
        await connection.execute(delete(operations).where(_is_legacy(operations)))
        for table in _audit_tables():
            await connection.execute(delete(table).where(_is_legacy(table)))
        await connection.execute(
            insert(system_events).values(
                id=str(uuid4()),
                asset=None,
                payload={
                    "status": PURGE_EVENT,
                    "counts": report.counts,
                    "backup": backup.path.name,
                },
                created_at=now,
            )
        )
    return report
