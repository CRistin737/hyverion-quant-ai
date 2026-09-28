from __future__ import annotations

import json

from conftest import control_client
from test_engine_supervisor import _settings

from trading_bot.control_api import create_control_api

SUMMARY_KEYS = {
    "daily_candidates",
    "weekly_candidates",
    "curated",
    "outcomes_recorded",
    "demoted",
    "restored",
    "retired",
    "conflicts",
    "outcomes_considered",
}


def test_api_cycle_returns_the_stable_summary_shape(tmp_path) -> None:
    settings = _settings(tmp_path)
    vault = settings.public.memory.model_copy(update={"vault_path": tmp_path / "vault"})
    settings = settings.model_copy(
        update={"public": settings.public.model_copy(update={"memory": vault})}
    )
    with control_client(create_control_api(settings)) as client:
        response = client.post("/api/v1/memory/cycle")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == SUMMARY_KEYS
    assert all(isinstance(value, int) for value in body.values())


def test_full_report_is_json_safe_and_consistent_with_summary(tmp_path) -> None:
    import asyncio

    from trading_bot.core.clock import SystemClock
    from trading_bot.db.database import Database
    from trading_bot.memory.cycle import run_memory_cycle

    settings = _settings(tmp_path)
    vault = settings.public.memory.model_copy(update={"vault_path": tmp_path / "vault"})
    settings = settings.model_copy(
        update={"public": settings.public.model_copy(update={"memory": vault})}
    )

    async def run():
        database = Database(settings.public.database.url)
        await database.initialize()
        try:
            return await run_memory_cycle(settings, database, SystemClock())
        finally:
            await database.close()

    report = asyncio.run(run())
    full = json.loads(json.dumps(report.as_dict()))
    summary = report.summary()
    assert full["daily_candidates"] == summary["daily_candidates"]
    assert len(full["curated"]) == summary["curated"]
    assert len(full["lifecycle"]["retired"]) == summary["retired"]
    assert len(full["lifecycle"]["conflicts"]) == summary["conflicts"]
