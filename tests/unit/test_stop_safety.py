"""Stopping the engine stops the agents: no run stays "analyzing", no CLI lives on."""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from trading_bot.agents.registry import AgentRegistry
from trading_bot.agents.runtime import AgentRuntime
from trading_bot.config import load_settings
from trading_bot.control_api import build_snapshot
from trading_bot.core.agent_pipeline import _gather_or_cancel
from trading_bot.core.clock import FixedClock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.providers.base import ProviderResult
from trading_bot.providers.router import ModelRouter
from trading_bot.providers.subscription_cli import SubscriptionCLIProvider


class Output(BaseModel):
    decision: str


class HangingProvider:
    """A provider whose model never answers, like a CLI mid-analysis."""

    provider_id = "static"
    started = asyncio.Event()

    async def invoke(self, **_: Any) -> ProviderResult:
        type(self).started.set()
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


async def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'stop.db'}")
    await database.initialize()
    return database


async def _statuses(database: Database) -> list[str]:
    rows = await AuditRepository(database).recent("agent_runs", limit=10)
    return [str(row["status"]) for row in rows]


@pytest.mark.asyncio
async def test_a_cancelled_agent_run_is_closed_as_cancelled(tmp_path: Path) -> None:
    database = await _database(tmp_path)
    HangingProvider.started = asyncio.Event()
    runtime = AgentRuntime(
        registry=AgentRegistry(),
        router=ModelRouter([HangingProvider()], {"STANDARD": "m"}),  # type: ignore[list-item]
        repository=AuditRepository(database),
        clock=FixedClock(datetime(2026, 9, 28, 15, 0, tzinfo=UTC)),
    )
    task = asyncio.create_task(
        runtime.invoke(
            agent_id="news", profile="STANDARD", context={"asset": "QQQ"}, output_schema=Output
        )
    )
    await asyncio.wait_for(HangingProvider.started.wait(), 5)
    assert await _statuses(database) == ["RUNNING"]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _statuses(database) == ["CANCELLED"]
    await database.close()


@pytest.mark.asyncio
async def test_leftover_running_rows_are_closed_and_never_shown_as_active(
    tmp_path: Path,
) -> None:
    database = await _database(tmp_path)
    repository = AuditRepository(database)
    now = datetime.now(UTC)
    await repository.start_agent_run(agent_id="technical", agent_version="1", started_at=now)
    await repository.start_agent_run(
        agent_id="news", agent_version="1", started_at=now - timedelta(hours=1)
    )
    settings = load_settings()

    def active(snapshot: dict[str, Any]) -> set[str]:
        return {agent["agent_id"] for agent in snapshot["agents"] if agent["status"] == "ACTIVE"}

    # Engine running: only the fresh run counts; the hour-old one is a leftover.
    assert active(await build_snapshot(database, settings, engine_running=True)) == {"technical"}
    # Engine stopped: nothing is analyzing.
    assert active(await build_snapshot(database, settings, engine_running=False)) == set()
    assert await repository.cancel_running_agent_runs() == 2
    assert set(await _statuses(database)) == {"CANCELLED"}
    await database.close()


@pytest.mark.asyncio
async def test_one_failing_agent_cancels_its_siblings() -> None:
    cancelled: list[str] = []

    async def slow(name: str) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(name)
            raise

    async def failing() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        await _gather_or_cancel(slow("news"), slow("macro"), failing())
    assert sorted(cancelled) == ["macro", "news"]


@pytest.mark.asyncio
async def test_a_cancelled_cli_call_kills_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hanging:
        returncode: int | None = None
        killed = False

        async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
            del input
            await asyncio.sleep(3600)
            return b"", b""

        async def wait(self) -> int:
            self.returncode = -9
            return -9

        def kill(self) -> None:
            self.killed = True

    process = Hanging()
    spawned = asyncio.Event()

    async def create(*_: str, **__: Any) -> Hanging:
        spawned.set()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gemini")
    provider = SubscriptionCLIProvider("gemini")
    task = asyncio.create_task(
        provider.invoke(
            agent_id="news",
            system_spec="spec",
            context={},
            model="default",
            output_schema=Output,
            timeout_seconds=600,
        )
    )
    await asyncio.wait_for(spawned.wait(), 5)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed


@pytest.mark.asyncio
async def test_sigterm_cancels_the_engine_and_closes_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_bot.main as main

    reasons: list[str] = []

    async def record(reason: str) -> None:
        reasons.append(reason)

    monkeypatch.setattr(main, "_close_leftover_agent_runs", record)
    unwound = asyncio.Event()

    async def engine() -> None:
        try:
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            unwound.set()
            raise

    await asyncio.wait_for(main._until_stopped(engine()), 5)
    assert unwound.is_set()
    assert reasons == ["engine_restarted", "engine_stopped"]
    asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)
    asyncio.get_running_loop().remove_signal_handler(signal.SIGINT)
