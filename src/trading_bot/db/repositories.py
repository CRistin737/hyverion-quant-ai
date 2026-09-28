from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from trading_bot.db.database import Database
from trading_bot.db.models import TABLES, agent_runs, model_usage


class AuditRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def append(
        self,
        table_name: str,
        payload: BaseModel | dict[str, Any],
        *,
        created_at: datetime,
        asset: str | None = None,
        event_time: datetime | None = None,
        received_time: datetime | None = None,
        processed_time: datetime | None = None,
        record_id: str | None = None,
    ) -> str:
        table = TABLES.get(table_name)
        if table is None or "payload" not in table.c:
            raise ValueError(f"table {table_name!r} is not an audit payload table")
        serialized = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        identifier = record_id or str(uuid4())
        statement = insert(table).values(
            id=identifier,
            asset=asset,
            event_time=event_time,
            received_time=received_time,
            processed_time=processed_time,
            payload=serialized,
            created_at=created_at,
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(statement)
        return identifier

    async def recent(
        self, table_name: str, limit: int = 20, *, asset: str | None = None
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        table = TABLES.get(table_name)
        if table is None:
            raise ValueError(f"unknown table: {table_name}")
        # Audit/event tables use ``created_at``; control-plane snapshots such as
        # daily PnL use ``updated_at`` or a session date.  Keep one bounded
        # repository method for both shapes so the native dashboard can read
        # them without bespoke SQL or unsafe dynamic fragments.
        order_column = next(
            (
                table.c[column]
                for column in ("created_at", "updated_at", "session_date", "id")
                if column in table.c
            ),
            table.c.id,
        )
        statement = select(table).order_by(order_column.desc()).limit(limit)
        if asset is not None:
            if "asset" not in table.c:
                raise ValueError(f"table {table_name!r} has no asset column")
            statement = statement.where(table.c.asset == asset)
        async with self._database.engine.connect() as connection:
            result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]

    async def with_status(
        self,
        table_name: str,
        statuses: Iterable[str],
        *,
        since: datetime | None = None,
        limit: int = 1,
    ) -> list[dict[str, Any]]:
        """Newest rows whose ``payload.status`` is one of ``statuses``.

        Unlike ``recent`` this is not a fixed window over every row, so a busy
        event table (hundreds of rows an hour) can never hide the last broker
        sync, reconciliation or routine record.
        """

        table = TABLES.get(table_name)
        if table is None or "payload" not in table.c or "created_at" not in table.c:
            raise ValueError(f"unknown or unsupported table: {table_name}")
        wanted = tuple(statuses)
        statement = (
            select(table)
            .where(table.c.payload["status"].as_string().in_(wanted))
            .order_by(table.c.created_at.desc())
            .limit(limit)
        )
        if since is not None:
            statement = statement.where(table.c.created_at >= since)
        async with self._database.engine.connect() as connection:
            result = await connection.execute(statement)
        return [dict(row) for row in result.mappings().all()]

    async def between(
        self,
        table_name: str,
        *,
        start: datetime | None,
        end: datetime,
        page_size: int = 5000,
    ) -> list[dict[str, Any]]:
        """Every row with ``created_at`` in ``[start, end]``, oldest first.

        Read in pages ordered by (created_at, id), so a year of equity
        snapshots or position marks is never silently truncated.
        """

        table = TABLES.get(table_name)
        if table is None or "created_at" not in table.c:
            raise ValueError(f"unknown or untimed table: {table_name}")
        rows: list[dict[str, Any]] = []
        cursor: tuple[datetime, str] | None = None
        async with self._database.engine.connect() as connection:
            while True:
                statement = select(table).where(table.c.created_at <= end)
                if start is not None:
                    statement = statement.where(table.c.created_at >= start)
                if cursor is not None:
                    at, identifier = cursor
                    statement = statement.where(
                        (table.c.created_at > at)
                        | ((table.c.created_at == at) & (table.c.id > identifier))
                    )
                statement = statement.order_by(
                    table.c.created_at.asc(), table.c.id.asc()
                ).limit(page_size)
                page = [dict(row) for row in (await connection.execute(statement)).mappings()]
                rows.extend(page)
                if len(page) < page_size:
                    return rows
                cursor = (page[-1]["created_at"], str(page[-1]["id"]))

    async def start_agent_run(
        self,
        *,
        agent_id: str,
        agent_version: str,
        started_at: datetime,
        run_id: str | None = None,
    ) -> str:
        """Create a RUNNING record before any provider call begins.

        The control plane deliberately stores only metadata here.  Model output is
        persisted separately through ``agent_outputs`` so secrets and prompts never
        get mixed into a typed status row.
        """

        identifier = run_id or str(uuid4())
        statement = insert(agent_runs).values(
            id=identifier,
            agent_id=agent_id,
            agent_version=agent_version,
            status="RUNNING",
            created_at=started_at,
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(statement)
        return identifier

    async def finish_agent_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: datetime,
        provider: str | None = None,
        model: str | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        """Close a run without changing its immutable creation timestamp."""

        del finished_at
        statement = (
            update(agent_runs)
            .where(agent_runs.c.id == run_id)
            .values(
                status=status,
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                error_code=error_code,
            )
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(statement)

    async def cancel_running_agent_runs(self, *, reason: str = "engine_stopped") -> int:
        """Close every run still marked RUNNING; returns how many were closed.

        Called when the engine starts and when it stops: no agent can be
        running while there is no engine, so a RUNNING row is a leftover of a
        crash or a hard kill.
        """

        statement = (
            update(agent_runs)
            .where(agent_runs.c.status == "RUNNING")
            .values(status="CANCELLED", error_code=reason)
        )
        async with self._database.engine.begin() as connection:
            result = await connection.execute(statement)
        return int(result.rowcount or 0)

    async def record_model_usage(
        self,
        *,
        agent_run_id: str,
        provider: str,
        model: str,
        billing_mode: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal | None,
        created_at: datetime,
        fallback_reason: str | None = None,
        attempted_providers: tuple[str, ...] = (),
    ) -> str:
        identifier = str(uuid4())
        statement = insert(model_usage).values(
            id=identifier,
            agent_run_id=agent_run_id,
            provider=provider,
            model=model,
            billing_mode=billing_mode,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            attempted_providers=list(attempted_providers),
            fallback_reason=fallback_reason,
            created_at=created_at,
        )
        async with self._database.engine.begin() as connection:
            await connection.execute(statement)
        return identifier

    async def daily_model_cost(self, *, now: datetime) -> Decimal:
        """Return API-billed model spend for the UTC session containing ``now``.

        The router is instantiated by each PAPER cycle, so an in-memory budget
        alone would reset at every cycle/restart and could exceed the daily
        cap.  Cost is read from the durable usage table before creating a new
        router. Subscription rows have a null cost and therefore contribute
        zero without pretending they have a dollar price.
        """

        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("usage timestamp must be timezone-aware")
        session_date = now.astimezone(UTC).date().isoformat()
        statement = select(func.coalesce(func.sum(model_usage.c.cost_usd), 0)).where(
            func.date(model_usage.c.created_at) == session_date
        )
        async with self._database.engine.connect() as connection:
            value: object = (await connection.execute(statement)).scalar_one()
        return Decimal(str(value or 0))
