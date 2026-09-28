"""Read-only trade, order and chart views used by the simplified Trading screens."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from conftest import control_client
from test_snapshot_consolidation import _seed, _settings

from trading_bot.broker.lifecycle import OperationState
from trading_bot.control_api import create_control_api
from trading_bot.core.trade_view import build_trades, filter_trades, rejected_entries
from trading_bot.db import Database, OperationLifecycleRepository
from trading_bot.schemas.common import TradingMode
from trading_bot.schemas.trading import Candle

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def test_build_trades_joins_operation_position_and_fills() -> None:
    operations = [
        {"id": "op-open", "asset": "QQQ", "state": "OPEN", "position_id": "p-1",
         "client_order_id": "c-1", "created_at": "2026-09-27T12:00:00+00:00"},
        {"id": "op-closed", "asset": "SPY", "state": "EVALUATED", "position_id": "p-2",
         "client_order_id": "c-2", "created_at": "2026-09-27T11:00:00+00:00"},
        {"id": "op-stuck", "asset": "QQQ", "state": "RECOVERY_REQUIRED",
         "created_at": "2026-09-27T10:00:00+00:00"},
        {"id": "op-rejected", "asset": "QQQ", "state": "REJECTED",
         "created_at": "2026-09-27T09:00:00+00:00"},
    ]
    positions = [
        {"position_id": "p-1", "asset": "QQQ", "side": "BUY", "status": "OPEN",
         "stop_price": "95", "unrealized_pnl": "3.5", "entry_fee_usd": "0.1",
         "opened_at": "2026-09-27T12:00:00+00:00", "client_order_id": "c-1"},
        {"position_id": "p-2", "asset": "SPY", "side": "BUY", "status": "CLOSED",
         "stop_price": "90", "realized_net_pnl": "-2", "entry_fee_usd": "0.1",
         "exit_fee_usd": "0.2", "exit_reason": "TIME_EXIT", "exit_client_order_ids": ["x-2"],
         "opened_at": "2026-09-27T11:00:00+00:00", "client_order_id": "c-2"},
    ]
    fills = [
        {"fill_id": "f-1", "client_order_id": "c-1"},
        {"fill_id": "f-2", "client_order_id": "c-2"},
        {"fill_id": "f-3", "client_order_id": "x-2"},
    ]

    rows = build_trades(operations, positions, fills, unprotected_position_ids=["p-1"])

    by_id = {row["id"]: row for row in rows}
    assert "op-rejected" not in by_id  # never became a trade; shown with orders
    assert by_id["op-open"]["view"] == "open"
    assert by_id["op-open"]["pnl_usd"] == "3.5"
    assert by_id["op-open"]["protected"] is False
    assert by_id["op-closed"]["view"] == "closed"
    assert by_id["op-closed"]["pnl_usd"] == "-2"
    assert Decimal(by_id["op-closed"]["fees_usd"]) == Decimal("0.3")
    assert [fill["fill_id"] for fill in by_id["op-closed"]["fills"]] == ["f-2", "f-3"]
    assert by_id["op-stuck"]["view"] == "attention"
    assert [row["id"] for row in filter_trades(rows, "attention")] == ["op-stuck"]
    assert [row["id"] for row in rows] == ["op-open", "op-closed", "op-stuck"]


def test_rejected_entries_carry_reasons_and_shadow_result() -> None:
    rows = rejected_entries(
        [{"id": "op-9", "proposal_id": "pr-9", "asset": "QQQ", "state": "REJECTED"}],
        {"op-9": {"reasons": ["spread_too_wide"]}},
        {"pr-9": {"net_pnl_usd": "-1.25", "outcome": "LOSS"}},
    )
    assert rows[0]["reasons"] == ["spread_too_wide"]
    assert rows[0]["would_have_net_pnl_usd"] == "-1.25"


def _candle(minute: int) -> Candle:
    at = NOW + timedelta(minutes=minute)
    return Candle(
        symbol="QQQ", interval="5m", open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100.5"), volume=Decimal("1"),
        trades=3, event_time=at, received_time=at,
        processed_time=at,
    )


def test_market_candles_validate_input_and_cache(tmp_path) -> None:
    calls: list[tuple[str, str, int]] = []

    async def chart_source(symbol: str, interval: str, limit: int):
        calls.append((symbol, interval, limit))
        return tuple(_candle(i) for i in range(3))

    settings = _settings(tmp_path, "chart.db")
    api = create_control_api(settings, chart_source=chart_source)
    with control_client(api) as client:
        ok = client.get("/api/v1/market/candles", params={"symbol": "QQQ", "interval": "5m"})
        again = client.get(
            "/api/v1/market/candles", params={"symbol": "QQQ", "interval": "5m"}
        )
        foreign = client.get("/api/v1/market/candles", params={"symbol": "NVDA"})
        interval = client.get(
            "/api/v1/market/candles", params={"symbol": "QQQ", "interval": "7m"}
        )

    assert ok.status_code == 200
    assert ok.json()[0]["close"] == "100.5"
    assert again.json() == ok.json()
    assert calls == [("QQQ", "5m", 120)]  # second call served from cache
    assert foreign.json()["detail"]["code"] == "symbol_not_allowed"
    assert interval.json()["detail"]["code"] == "interval_not_allowed"


def test_market_candles_fail_closed_when_public_data_is_down(tmp_path) -> None:
    async def broken(symbol: str, interval: str, limit: int):
        raise OSError("offline")

    api = create_control_api(_settings(tmp_path, "down.db"), chart_source=broken)
    with control_client(api) as client:
        response = client.get("/api/v1/market/candles", params={"symbol": "QQQ"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "market_data_unavailable"


def test_trades_orders_and_rejections_endpoints(tmp_path) -> None:
    settings = _settings(tmp_path, "trades.db")
    _seed(
        settings,
        [
            ("positions", {"position_id": "p-1", "asset": "QQQ", "side": "BUY",
                           "status": "OPEN", "state_at": NOW.isoformat()}, "QQQ", NOW),
            ("orders", {"order_id": "o-1", "asset": "QQQ", "side": "BUY",
                        "status": "FILLED"}, "QQQ", NOW),
            ("orders", {"order_id": "o-2", "asset": "SPY", "side": "SELL",
                        "status": "FILLED"}, "SPY", NOW),
            ("shadow_trades", {"proposal_id": "pr-r", "net_pnl_usd": "2.00",
                               "outcome": "WIN"}, "QQQ", NOW),
        ],
    )

    async def seed_operations() -> None:
        database = Database(settings.public.database.url)
        await database.initialize()
        lifecycle = OperationLifecycleRepository(database)
        await lifecycle.open(operation_id="op-r", proposal_id="pr-r", asset="QQQ",
                             mode=TradingMode.PAPER, now=NOW)
        await lifecycle.advance("op-r", OperationState.CRITIC_REVIEWED, reason="critic", now=NOW)
        await lifecycle.advance("op-r", OperationState.REJECTED, reason="risk_rejected",
                                now=NOW, details={"reasons": ["daily_loss_cap"]})
        await database.close()

    asyncio.run(seed_operations())
    with control_client(create_control_api(settings)) as client:
        trades = client.get("/api/v1/trades", params={"view": "open"}).json()
        eth = client.get("/api/v1/orders", params={"asset": "SPY"}).json()
        rejected = client.get("/api/v1/orders/rejected").json()

    assert [row["position_id"] for row in trades] == ["p-1"]
    assert [row["order_id"] for row in eth] == ["o-2"]
    assert rejected[0]["reasons"] == ["daily_loss_cap"]
    assert rejected[0]["would_have_net_pnl_usd"] == "2.00"
