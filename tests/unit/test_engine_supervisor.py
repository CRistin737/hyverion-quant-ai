from __future__ import annotations

import asyncio
import json
import sys
import time

import pytest
from conftest import control_client

from trading_bot.config import load_settings
from trading_bot.config.models import Settings
from trading_bot.control_api import create_control_api
from trading_bot.db import AuditRepository, Database
from trading_bot.engine_supervisor import (
    EngineStartError,
    EngineSupervisor,
    engine_command,
    engine_lock_held,
    engine_lock_path,
    hold_engine_lock,
)

# Stand-in engine: exits on stdin EOF, like the real `run` under the API watchdog.
SLEEPER = [sys.executable, "-c", "import sys; sys.stdin.read()"]


def _settings(tmp_path, *, broker: str = "simulator", mode: str = "paper") -> Settings:
    settings = load_settings()
    public = settings.public.model_copy(
        update={
            "database": settings.public.database.model_copy(
                update={"url": f"sqlite+aiosqlite:///{tmp_path / 'engine.db'}"}
            ),
            "trading": settings.public.trading.model_copy(
                update={"mode": mode, "live_trading": False}
            ),
            "broker": settings.public.broker.model_copy(update={"provider": broker}),
        }
    )
    return settings.model_copy(update={"public": public})


def _supervisor(settings: Settings, command: list[str] | None = None) -> EngineSupervisor:
    return EngineSupervisor(settings, command_factory=lambda _interval: command or SLEEPER)


def test_lock_lives_next_to_the_database(tmp_path) -> None:
    assert engine_lock_path(_settings(tmp_path)) == tmp_path / "engine.lock"


def test_engine_command_is_paper_poll_loop() -> None:
    command = engine_command(30)
    assert command[-4:] == ["run", "--interval-seconds", "30", "--poll"]
    assert "live" not in command


def test_start_and_stop_child(tmp_path) -> None:
    supervisor = _supervisor(_settings(tmp_path))
    assert supervisor.status().state == "stopped"
    started = supervisor.start(15)
    assert started.state == "running"
    assert started.pid is not None
    assert started.interval_seconds == 15
    # A second start is idempotent: never two engines.
    assert supervisor.start(15).pid == started.pid
    stopped = supervisor.stop()
    assert stopped.state == "stopped"
    assert stopped.pid is None


def test_crashed_child_reports_exited_with_code(tmp_path) -> None:
    supervisor = _supervisor(_settings(tmp_path), [sys.executable, "-c", "raise SystemExit(7)"])
    supervisor.start()
    deadline = time.monotonic() + 10
    while supervisor.status().state == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    status = supervisor.status()
    assert status.state == "exited"
    assert status.exit_code == 7


def test_external_engine_blocks_second_start(tmp_path) -> None:
    settings = _settings(tmp_path)
    supervisor = _supervisor(settings)
    with hold_engine_lock(settings):
        assert engine_lock_held(settings)
        assert supervisor.status().state == "external"
        with pytest.raises(EngineStartError) as exc:
            supervisor.start()
        assert exc.value.code == "engine_already_running"
        with pytest.raises(EngineStartError):
            with hold_engine_lock(settings):
                pass
    assert not engine_lock_held(settings)


def test_start_fails_closed_without_a_connected_broker(tmp_path) -> None:
    # Alpaca Paper without stored keys: there is no equity to size from.
    supervisor = _supervisor(_settings(tmp_path, broker="alpaca"))
    with pytest.raises(EngineStartError) as exc:
        supervisor.start()
    assert exc.value.code == "broker_not_connected"
    assert supervisor.status().state == "stopped"


def test_start_refuses_non_paper_mode(tmp_path) -> None:
    supervisor = _supervisor(_settings(tmp_path, mode="live"))
    with pytest.raises(EngineStartError) as exc:
        supervisor.start()
    assert exc.value.code == "live_not_allowed"


def test_start_rejects_short_interval(tmp_path) -> None:
    with pytest.raises(EngineStartError) as exc:
        _supervisor(_settings(tmp_path)).start(1)
    assert exc.value.code == "invalid_interval"


def test_child_does_not_inherit_api_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CONTROL_API_TOKEN", "secret-token-value")
    out = tmp_path / "env.txt"
    script = (
        "import os, pathlib, sys; "
        f"pathlib.Path({str(out)!r}).write_text("
        "repr((os.getenv('CONTROL_API_TOKEN'), os.getenv('TRADING_MODE'), "
        "os.getenv('HYVERION_SHELL_STDIN_WATCH')))); sys.stdin.read()"
    )
    supervisor = _supervisor(_settings(tmp_path), [sys.executable, "-c", script])
    supervisor.start()
    deadline = time.monotonic() + 10
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    supervisor.stop()
    assert out.read_text() == repr((None, "paper", "1"))


def test_engine_endpoints_require_token_and_control_child(tmp_path) -> None:
    settings = _settings(tmp_path)
    supervisor = _supervisor(settings)
    app = create_control_api(settings, engine=supervisor)
    with control_client(app) as client:
        client.headers.pop("Authorization", None)
        assert client.get("/api/v1/engine").status_code == 401
        assert client.post("/api/v1/engine/start", json={}).status_code == 401
    with control_client(app) as client:
        assert client.get("/api/v1/engine").json()["state"] == "stopped"
        assert client.post("/api/v1/engine/start", json={"interval_seconds": 2}).status_code == 422
        assert client.post("/api/v1/engine/start", json={"mode": "live"}).status_code == 422
        started = client.post("/api/v1/engine/start", json={"interval_seconds": 30})
        assert started.status_code == 200
        assert started.json()["state"] == "running"
        assert started.json()["mode"] == "PAPER"
        assert client.get("/api/v1/snapshot").json()["engine"]["state"] == "running"
        stopped = client.post("/api/v1/engine/stop")
        assert stopped.json()["state"] == "stopped"
    assert {"ENGINE_STARTED", "ENGINE_STOPPED"} <= asyncio.run(_event_statuses(settings))


async def _event_statuses(settings: Settings) -> set[str]:
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        rows = await AuditRepository(database).recent("system_events", limit=10)
    finally:
        await database.close()
    payloads = (
        json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
        for row in rows
    )
    return {str(payload.get("status")) for payload in payloads}


def test_engine_start_conflict_uses_stable_code(tmp_path) -> None:
    settings = _settings(tmp_path, broker="alpaca")
    app = create_control_api(settings, engine=_supervisor(settings))
    with control_client(app) as client:
        response = client.post("/api/v1/engine/start", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "broker_not_connected"}


def test_api_shutdown_stops_running_engine(tmp_path) -> None:
    settings = _settings(tmp_path)
    supervisor = _supervisor(settings)
    with control_client(create_control_api(settings, engine=supervisor)) as client:
        client.post("/api/v1/engine/start", json={})
        assert supervisor.status().state == "running"
    assert supervisor.status().state == "stopped"


def test_flatten_endpoint_needs_running_engine_and_leaves_only_a_flag(tmp_path) -> None:
    from trading_bot.engine_supervisor import flatten_pending

    settings = _settings(tmp_path)
    app = create_control_api(settings, engine=_supervisor(settings))
    with control_client(app) as client:
        refused = client.post("/api/v1/engine/flatten")
        assert refused.status_code == 409
        assert refused.json()["detail"] == {"code": "engine_not_running"}
        client.post("/api/v1/engine/start", json={})
        ok = client.post("/api/v1/engine/flatten")
        assert ok.status_code == 200 and ok.json()["flatten_pending"] is True
        assert flatten_pending(settings)
        assert client.get("/api/v1/snapshot").json()["engine"]["flatten_pending"] is True
        cancelled = client.post("/api/v1/engine/flatten/cancel")
        assert cancelled.json()["flatten_pending"] is False
        client.post("/api/v1/engine/stop")
