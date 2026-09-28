"""Safe, explicit SQLite control-plane backup primitives.

The local desktop uses SQLite/WAL.  The backup API uses SQLite's online backup
mechanism so a running PAPER process can be copied consistently without
reading secrets or deleting the source database.  PostgreSQL/VPS backups still
require the production rehearsal documented in the recovery guide.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading_bot.db.database import Database


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    size_bytes: int
    verified: bool


def sqlite_path(url: str) -> Path:
    """Resolve a supported SQLAlchemy SQLite URL without accepting memory DBs."""

    if not url.startswith("sqlite") or "///" not in url:
        raise ValueError("backup supports only a file-backed SQLite URL")
    raw_path = url.split("///", maxsplit=1)[1]
    if not raw_path or raw_path == ":memory:" or raw_path.startswith("file:"):
        raise ValueError("backup requires a file-backed SQLite database")
    return Path(raw_path).expanduser().resolve()


async def create_sqlite_backup(database: Database, destination: Path) -> BackupResult:
    """Create and integrity-check an online backup without mutating the source."""

    source = sqlite_path(database.url)
    target = await asyncio.to_thread(_resolve_path, destination)
    if source == target:
        raise ValueError("backup destination must differ from the source database")
    healthy, detail = await database.healthcheck()
    if not healthy:
        raise RuntimeError(f"source database is not healthy: {detail}")
    target.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(_backup_file, source, target)
    verified = await asyncio.to_thread(verify_sqlite_file, target)
    if not verified:
        raise RuntimeError("backup integrity check failed")
    return BackupResult(path=target, size_bytes=target.stat().st_size, verified=True)


def verify_sqlite_file(path: Path) -> bool:
    """Return true only when SQLite reports a healthy, readable backup."""

    connection = sqlite3.connect(str(path))
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        return bool(result and result[0] == "ok")
    except sqlite3.DatabaseError:
        return False
    finally:
        connection.close()


def _backup_file(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(str(source))
    target_connection = sqlite3.connect(str(target))
    try:
        source_connection.backup(target_connection, pages=100, sleep=0.05)
        target_connection.commit()
    finally:
        target_connection.close()
        source_connection.close()


def _resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()
