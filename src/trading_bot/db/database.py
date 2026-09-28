from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from trading_bot.db.models import metadata


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        if url.startswith("sqlite"):
            self._ensure_sqlite_parent(url)
            self._configure_sqlite()

    @staticmethod
    def _ensure_sqlite_parent(url: str) -> None:
        marker = "///"
        if marker not in url:
            return
        database_path = url.split(marker, maxsplit=1)[1]
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    def _configure_sqlite(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    async def initialize(self) -> None:
        retrying = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.1, max=1),
            retry=retry_if_exception_type(OperationalError),
            reraise=True,
        )
        async for attempt in retrying:
                with attempt:
                    async with self.engine.begin() as connection:
                        await connection.run_sync(metadata.create_all)
                        if self.url.startswith("sqlite"):
                            await connection.run_sync(_ensure_sqlite_compatibility)

    async def healthcheck(self) -> tuple[bool, str]:
        try:
            async with self.engine.connect() as connection:
                if self.url.startswith("sqlite"):
                    result = await connection.execute(text("PRAGMA integrity_check"))
                    status = str(result.scalar_one())
                    return status == "ok", status
                await connection.execute(text("SELECT 1"))
            return True, "ok"
        except Exception as exc:  # boundary must convert DB errors to safe health state
            return False, type(exc).__name__

    async def close(self) -> None:
        await self.engine.dispose()


def _ensure_sqlite_compatibility(connection: Connection) -> None:
    """Apply additive control-plane columns to pre-migration local databases.

    Desktop users can have a SQLite file created by an older bundle while the
    new bundle's SQLAlchemy metadata already knows about a column. ``create_all``
    does not alter existing tables, so a read-only snapshot would otherwise
    fail before the operator can run Alembic manually. Only fixed, additive
    columns are handled here; destructive/schema-changing work still belongs
    in versioned Alembic migrations.
    """

    inspector = inspect(connection)
    additions = {
        "model_usage": {
            "attempted_providers": "JSON",
        },
        "memory_candidates": {
            "confidence_components": "JSON",
        },
    }
    for table_name, columns in additions.items():
        existing = {str(column["name"]) for column in inspector.get_columns(table_name)}
        for column_name, declaration in columns.items():
            if column_name not in existing:
                connection.execute(
                    text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" '
                        f"{declaration}"
                    )
                )
