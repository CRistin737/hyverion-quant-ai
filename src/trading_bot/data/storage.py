from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]


class ParquetMarketStore:
    """Append analytical batches; raw high-volume records do not enter SQLite."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write_batch(
        self, dataset: str, records: list[dict[str, Any]], event_time: datetime
    ) -> Path:
        if not records:
            raise ValueError("cannot write an empty parquet batch")
        utc_time = event_time.astimezone(UTC)
        partition = self._root / dataset / f"date={utc_time.date().isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        path = partition / f"batch-{utc_time.strftime('%H%M%S%f')}.parquet"
        table = pa.Table.from_pylist(records)
        pq.write_table(table, path, compression="zstd")
        return path

    def query(self, sql: str, parameters: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        forbidden = ("insert", "update", "delete", "drop", "alter", "attach", "copy")
        normalized = sql.strip().lower()
        if not normalized.startswith("select") or any(word in normalized for word in forbidden):
            raise ValueError("analytical store accepts read-only SELECT queries")
        with duckdb.connect(":memory:") as connection:
            return connection.execute(sql, parameters).fetchall()
