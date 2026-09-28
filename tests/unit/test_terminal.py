from __future__ import annotations

import pytest
from rich.console import Console
from test_engine_supervisor import _settings

from trading_bot.db.database import Database
from trading_bot.engine_supervisor import hold_engine_lock
from trading_bot.terminal import _state, render_terminal


def _text(group) -> str:
    console = Console(width=160, record=True, color_system=None)
    console.print(group)
    return console.export_text()


def test_terminal_warns_about_positions_without_engine() -> None:
    text = _text(
        render_terminal(
            {
                "config": {
                    "capital_usd": "1000",
                    "allowed_symbols": ["QQQ"],
                    "live_trading": False,
                },
                "risk": {"level": 0, "risk_multiplier": "1.0", "verdict": "ALLOW"},
                "pnl": {"marked_equity": "1012.5"},
                "markets": [{"symbol": "QQQ", "bid": "100", "ask": "100.01", "last": "100"}],
                "positions": [{"asset": "QQQ", "side": "buy", "quantity": "0.1"}],
                "orders": [],
                "alerts": [{"severity": "CRITICAL", "message": "Exchange reconciliation failed"}],
                "engine_running": False,
            }
        )
    )
    assert "SIMULACIÓN" in text
    assert "nadie vigila stops" in text
    assert "Exchange reconciliation failed" in text
    assert "$1,012.50" in text


@pytest.mark.asyncio
async def test_terminal_state_uses_the_shared_snapshot(tmp_path) -> None:
    settings = _settings(tmp_path)
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        state = await _state(database, settings)
        assert {"config", "risk", "pnl", "positions", "alerts"} <= set(state)
        assert state["engine_running"] is False
        with hold_engine_lock(settings):
            assert (await _state(database, settings))["engine_running"] is True
        assert "Sin posiciones abiertas" in _text(render_terminal(state))
    finally:
        await database.close()
