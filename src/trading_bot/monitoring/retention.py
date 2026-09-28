"""Bounded audit export and retention primitives.

The control plane must be recoverable without retaining unbounded market or
external-text rows in SQLite.  This module deliberately keeps retention
explicit and dry-run by default: callers must opt into deletion after an
export has been verified.  It never handles credentials and never mutates
business tables such as orders, fills, positions, accounts or daily PnL.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from trading_bot.db.database import Database
from trading_bot.db.models import TABLES

# These are intentionally bounded and conservative.  Immutable trading and
# account projections are excluded from purge; they are needed to reconstruct
# why an order or risk decision happened.
DEFAULT_RETENTION_DAYS: Mapping[str, int] = {
    "market_snapshots": 14,
    "candles": 90,
    "features": 30,
    "news_items": 90,
    "social_items": 30,
    "agent_outputs": 180,
    "signals": 180,
    "critic_reviews": 180,
    "risk_decisions": 365,
    "system_events": 365,
    "alerts": 365,
    "source_runs": 180,
}


@dataclass(frozen=True, slots=True)
class RetentionTableResult:
    table: str
    cutoff: datetime
    eligible_rows: int
    deleted_rows: int


@dataclass(frozen=True, slots=True)
class RetentionRunResult:
    dry_run: bool
    generated_at: datetime
    tables: tuple[RetentionTableResult, ...]

    @property
    def eligible_rows(self) -> int:
        return sum(item.eligible_rows for item in self.tables)

    @property
    def deleted_rows(self) -> int:
        return sum(item.deleted_rows for item in self.tables)


@dataclass(frozen=True, slots=True)
class RetentionExportResult:
    data_path: Path
    manifest_path: Path
    rows: int
    sha256: str
    tables: tuple[str, ...]


async def export_audit_bundle(
    database: Database,
    destination: Path,
    *,
    tables: tuple[str, ...] = tuple(DEFAULT_RETENTION_DAYS),
    max_rows_per_table: int = 10_000,
) -> RetentionExportResult:
    """Export bounded, JSON-safe audit rows and a tamper-evident manifest.

    The destination is never overwritten.  The payload is written to a
    temporary file in the same directory and atomically renamed only after all
    rows have been serialized.  The manifest contains counts and a SHA-256 of
    the data file, but no source path or secret material.
    """

    if max_rows_per_table < 1 or max_rows_per_table > 100_000:
        raise ValueError("max_rows_per_table must be between 1 and 100000")
    selected = _validate_tables(tables)
    destination, manifest_path = _prepare_destination(destination)
    healthy, detail = await database.healthcheck()
    if not healthy:
        raise RuntimeError(f"database healthcheck failed: {detail}")

    rows = 0
    table_counts: dict[str, int] = {}
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, prefix=".retention-", delete=False
        ) as handle:
            temporary_name = handle.name
            async with database.engine.connect() as connection:
                for table_name in selected:
                    table = TABLES[table_name]
                    statement = select(table).order_by(table.c.created_at.asc()).limit(
                        max_rows_per_table
                    )
                    result = await connection.execute(statement)
                    count = 0
                    for row in result.mappings():
                        record = {
                            "table": table_name,
                            "id": row.get("id"),
                            "asset": row.get("asset"),
                            "event_time": _json_value(row.get("event_time")),
                            "received_time": _json_value(row.get("received_time")),
                            "processed_time": _json_value(row.get("processed_time")),
                            "created_at": _json_value(row.get("created_at")),
                            "payload": _json_value(row.get("payload")),
                        }
                        handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
                        handle.write("\n")
                        count += 1
                        rows += 1
                    table_counts[table_name] = count
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
        temporary_name = None
        digest = _sha256(destination)
        manifest = {
            "schema_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "format": "jsonl",
            "rows": rows,
            "tables": table_counts,
            "sha256": digest,
        }
        manifest_temp: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=manifest_path.parent,
                prefix=".retention-manifest-",
                delete=False,
            ) as manifest_handle:
                manifest_temp = manifest_handle.name
                json.dump(manifest, manifest_handle, indent=2, sort_keys=True)
                manifest_handle.write("\n")
                manifest_handle.flush()
                os.fsync(manifest_handle.fileno())
            os.replace(manifest_temp, manifest_path)
            manifest_temp = None
        finally:
            if manifest_temp:
                _unlink_if_exists(manifest_temp)
    finally:
        if temporary_name:
            _unlink_if_exists(temporary_name)

    return RetentionExportResult(
        data_path=destination,
        manifest_path=manifest_path,
        rows=rows,
        sha256=digest,
        tables=selected,
    )


async def apply_retention(
    database: Database,
    *,
    now: datetime | None = None,
    retention_days: Mapping[str, int] = DEFAULT_RETENTION_DAYS,
    dry_run: bool = True,
) -> RetentionRunResult:
    """Count or delete expired high-volume audit rows.

    ``dry_run=True`` is the default and performs no mutation.  Even when
    deletion is enabled, only the allowlisted audit tables above are eligible;
    order/fill/position/account/PnL tables are never touched here.
    """

    current = _aware_utc(now or datetime.now(UTC))
    selected = _validate_retention_days(retention_days)
    results: list[RetentionTableResult] = []
    async with database.engine.begin() as connection:
        for table_name, days in selected.items():
            table = TABLES[table_name]
            cutoff = current - timedelta(days=days)
            predicate = table.c.created_at < cutoff
            count_result = await connection.execute(
                select(func.count()).select_from(table).where(predicate)
            )
            eligible = int(count_result.scalar_one())
            deleted = 0
            if eligible and not dry_run:
                delete_result = await connection.execute(delete(table).where(predicate))
                deleted = int(delete_result.rowcount or 0)
            results.append(
                RetentionTableResult(
                    table=table_name,
                    cutoff=cutoff,
                    eligible_rows=eligible,
                    deleted_rows=deleted,
                )
            )
    return RetentionRunResult(dry_run=dry_run, generated_at=current, tables=tuple(results))


def _validate_tables(tables: tuple[str, ...]) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(tables))
    if not selected:
        raise ValueError("at least one retention table is required")
    unknown = set(selected).difference(DEFAULT_RETENTION_DAYS)
    if unknown:
        raise ValueError(f"table is not eligible for retention: {sorted(unknown)[0]}")
    return selected


def _validate_retention_days(retention_days: Mapping[str, int]) -> dict[str, int]:
    selected = dict(retention_days)
    _validate_tables(tuple(selected))
    for table_name, days in selected.items():
        if not isinstance(days, int) or days < 1 or days > 3650:
            raise ValueError(f"retention days for {table_name} must be between 1 and 3650")
    return selected


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retention timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        # SQLite may return DateTime columns without tzinfo even when the
        # application inserted an aware UTC value.  Treat that legacy storage
        # representation as UTC; never invent a local timezone.
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return normalized.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_destination(destination: Path) -> tuple[Path, Path]:
    expanded = Path(os.path.expanduser(os.fspath(destination)))
    manifest = expanded.with_suffix(expanded.suffix + ".manifest.json")
    if os.path.exists(expanded) or os.path.exists(manifest):
        raise FileExistsError("retention export destination already exists")
    expanded.parent.mkdir(parents=True, exist_ok=True)
    return expanded, manifest


def _unlink_if_exists(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
