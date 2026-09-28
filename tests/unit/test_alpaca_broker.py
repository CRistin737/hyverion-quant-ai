"""Alpaca PAPER broker adapter against mocked official endpoints, plus its guards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from conftest import control_client, make_context, make_critic, make_proposal

from trading_bot.broker.alpaca import PAPER_URL, AlpacaPaperBroker
from trading_bot.broker.base import (
    BrokerCapabilities,
    BrokerUnavailable,
    SubmissionStateUnknown,
    round_quantity,
)
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.config import load_settings
from trading_bot.control_api import create_control_api
from trading_bot.core.clock import FixedClock
from trading_bot.db import Database, TradingStateRepository
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent, RiskDecision
from trading_bot.security.credentials import AlpacaPaperCredentials

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)
CREDS = AlpacaPaperCredentials(key_id="PKTEST", secret_key="paper-secret")  # noqa: S106


async def no_sleep(_seconds: float) -> None:
    return None


def broker(**kwargs: Any) -> AlpacaPaperBroker:
    return AlpacaPaperBroker(CREDS, FixedClock(NOW), sleep=no_sleep, poll_seconds=1,
                             fill_wait_seconds=2, **kwargs)


def order(coid: str, status: str, *, filled: str = "0", qty: str = "2.5", side: str = "buy",
          order_id: str | None = None, price: str | None = None) -> dict:
    return {
        "id": order_id or f"id-{coid}",
        "client_order_id": coid,
        "symbol": "QQQ",
        "side": side,
        "qty": qty,
        "filled_qty": filled,
        "filled_avg_price": price,
        "status": status,
        "created_at": "2026-09-28T15:00:00Z",
        "filled_at": "2026-09-28T15:00:01Z" if price else None,
    }


def intent(**updates: Any) -> ExecutionIntent:
    values: dict[str, Any] = {
        "intent_id": "i-1",
        "decision_id": "d-1",
        "proposal_id": "p-1",
        "client_order_id": "hyverion-p-1",
        "mode": TradingMode.PAPER,
        "asset": "QQQ",
        "side": Side.BUY,
        "quantity": Decimal("2.5"),
        "limit_price": Decimal("481.25"),
        "stop_price": Decimal("478.30"),
        "target_price": Decimal("487.10"),
        "created_at": NOW,
    }
    values.update(updates)
    return ExecutionIntent.model_validate(values)


def test_adapter_refuses_any_host_but_the_paper_endpoint() -> None:
    with pytest.raises(BrokerUnavailable) as caught:
        AlpacaPaperBroker(CREDS, FixedClock(NOW), base_url="https://api.alpaca.markets")
    assert caught.value.code == "broker_live_host_forbidden"


@pytest.mark.asyncio
async def test_live_intents_and_short_sales_are_refused() -> None:
    with pytest.raises(BrokerUnavailable) as live:
        await broker().submit(intent(mode=TradingMode.LIVE))
    assert live.value.code == "broker_paper_only"
    with pytest.raises(BrokerUnavailable) as short:
        await broker().submit(intent(side=Side.SELL))
    assert short.value.code == "broker_shorting_disabled"


@pytest.mark.asyncio
@respx.mock
async def test_filled_entry_gets_a_native_protective_stop() -> None:
    posted: list[dict] = []

    def create(request: httpx.Request) -> httpx.Response:
        body = request.read()
        import json

        payload = json.loads(body)
        posted.append(payload)
        if payload["type"] == "stop":
            return httpx.Response(200, json=order(payload["client_order_id"], "new", side="sell"))
        return httpx.Response(200, json=order(payload["client_order_id"], "new"))

    respx.post(f"{PAPER_URL}/v2/orders").mock(side_effect=create)
    respx.get(f"{PAPER_URL}/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(
            200, json=order("hyverion-p-1", "filled", filled="2.5", price="481.20")
        )
    )
    result = await broker().submit(intent())

    entry, stop = posted
    assert entry == {
        "symbol": "QQQ", "qty": "2.500000", "side": "buy", "type": "limit",
        "time_in_force": "day", "limit_price": "481.25", "client_order_id": "hyverion-p-1",
    }
    assert stop["type"] == "stop" and stop["side"] == "sell"
    assert stop["client_order_id"] == "hyverion-p-1-stop"
    assert stop["stop_price"] == "478.30" and stop["qty"] == "2.5"
    # Fractional quantities only allow a day order; whole shares get GTC below.
    assert stop["time_in_force"] == "day"
    assert result.status == "FILLED" and result.protective_stop_active
    assert result.fills[0].price == Decimal("481.20")


@pytest.mark.asyncio
@respx.mock
async def test_unfilled_remainder_is_canceled_after_the_wait() -> None:
    respx.post(f"{PAPER_URL}/v2/orders").mock(
        return_value=httpx.Response(200, json=order("hyverion-p-1", "new"))
    )
    states = iter(
        [
            order("hyverion-p-1", "partially_filled", filled="1", price="481.2"),
            order("hyverion-p-1", "partially_filled", filled="1", price="481.2"),
            order("hyverion-p-1", "canceled", filled="1", price="481.2"),
        ]
    )
    respx.get(f"{PAPER_URL}/v2/orders:by_client_order_id").mock(
        side_effect=lambda request: httpx.Response(200, json=next(states))
    )
    cancel = respx.delete(f"{PAPER_URL}/v2/orders/id-hyverion-p-1").mock(
        return_value=httpx.Response(204)
    )
    result = await broker().submit(intent())
    assert cancel.called
    assert result.filled_quantity == Decimal("1")
    assert result.status == "CANCELED"  # remainder canceled; the filled part is protected


@pytest.mark.asyncio
@respx.mock
async def test_timeout_is_unknown_and_the_engine_looks_up_before_retrying(clock) -> None:
    respx.post(f"{PAPER_URL}/v2/orders").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(SubmissionStateUnknown):
        await broker().submit(intent())

    # The order did reach Alpaca: the engine finds it instead of sending another.
    respx.get(f"{PAPER_URL}/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(
            200, json=order("hyverion-p-1", "filled", filled="2.5", price="481.2")
        )
    )
    engine = ExecutionEngine(broker(), clock=FixedClock(NOW))
    decision = RiskDecision(
        decision_id="d-1", proposal_id="p-1", verdict="ALLOW", reasons=(), level=0,
        risk_multiplier=Decimal("1"), base_risk_usd=Decimal("10"),
        allowed_risk_usd=Decimal("10"), candidate_worst_case_loss_usd=Decimal("7"),
        protected_profit_floor_usd=Decimal("0"), live_trading_allowed=False,
        shadow_trading=True, decided_at=NOW, approved_asset="QQQ", approved_side=Side.BUY,
        approved_quantity=Decimal("2.5"), approved_notional_usd=Decimal("1300"),
    )
    found = await engine.execute(intent(), decision)
    assert found.status == "FILLED"
    assert not engine.simulated_venue


def _exit_venue(stop_after_cancel: dict, calls: list[str]) -> None:
    """Entry `entry-1`, its stop working until cancelled, then `stop_after_cancel`."""

    stop_states = {"now": order("hyverion-p-1-stop", "new", side="sell")}
    respx.get(f"{PAPER_URL}/v2/orders/entry-1").mock(
        return_value=httpx.Response(200, json=order("hyverion-p-1", "filled", order_id="entry-1"))
    )

    def by_client_id(request: httpx.Request) -> httpx.Response:
        coid = request.url.params["client_order_id"]
        if coid.endswith("-stop"):
            return httpx.Response(200, json=stop_states["now"])
        return httpx.Response(
            200, json=order(coid, "filled", filled="2.5", side="sell", price="483.0")
        )

    def cancel(request: httpx.Request) -> httpx.Response:
        calls.append("cancel-stop")
        stop_states["now"] = stop_after_cancel
        return httpx.Response(204)

    def sell(request: httpx.Request) -> httpx.Response:
        calls.append("sell")
        return httpx.Response(200, json=order("hyverion-exit-1", "new", side="sell"))

    respx.get(f"{PAPER_URL}/v2/orders:by_client_order_id").mock(side_effect=by_client_id)
    respx.delete(f"{PAPER_URL}/v2/orders/id-hyverion-p-1-stop").mock(side_effect=cancel)
    respx.post(f"{PAPER_URL}/v2/orders").mock(side_effect=sell)


def exit_intent() -> ExecutionIntent:
    return intent(client_order_id="hyverion-exit-1", side=Side.SELL, reduce_only=True,
                  position_id="entry-1")


@pytest.mark.asyncio
@respx.mock
async def test_exit_cancels_the_protective_stop_before_selling() -> None:
    calls: list[str] = []
    _exit_venue(order("hyverion-p-1-stop", "canceled", side="sell"), calls)
    result = await broker().submit(exit_intent())
    assert calls == ["cancel-stop", "sell"]
    assert result.status == "FILLED"
    assert result.protective_stop_active is False  # nothing left to protect


@pytest.mark.asyncio
@respx.mock
async def test_stop_that_fills_during_the_cancel_is_the_exit_no_double_sale() -> None:
    calls: list[str] = []
    _exit_venue(
        order("hyverion-p-1-stop", "filled", filled="2.5", side="sell", price="478.3"), calls
    )
    result = await broker().submit(exit_intent())
    assert calls == ["cancel-stop"]  # no market sell after the stop already sold
    assert result.filled_quantity == Decimal("2.5")
    assert result.fills[0].price == Decimal("478.3")


@pytest.mark.asyncio
@respx.mock
async def test_unconfirmed_stop_cancel_refuses_to_sell() -> None:
    calls: list[str] = []
    _exit_venue(order("hyverion-p-1-stop", "new", side="sell"), calls)  # cancel did not stick
    with pytest.raises(BrokerUnavailable) as caught:
        await broker().submit(exit_intent())
    assert caught.value.code == "protective_stop_cancel_unconfirmed"
    assert "sell" not in calls


@pytest.mark.asyncio
@respx.mock
async def test_rejected_order_is_reported_not_raised() -> None:
    respx.post(f"{PAPER_URL}/v2/orders").mock(
        return_value=httpx.Response(403, json={"message": "insufficient buying power"})
    )
    result = await broker().submit(intent())
    assert result.status == "REJECTED" and result.filled_quantity == 0


@pytest.mark.asyncio
@respx.mock
async def test_reconciliation_snapshot_ignores_own_stops_and_balances() -> None:
    respx.get(f"{PAPER_URL}/v2/positions").mock(
        return_value=httpx.Response(200, json=[{"symbol": "QQQ", "qty": "2.5",
                                                "avg_entry_price": "481.2",
                                                "market_value": "1203", "unrealized_pl": "0"}])
    )
    respx.get(f"{PAPER_URL}/v2/orders", params={"status": "open"}).mock(
        return_value=httpx.Response(200, json=[order("hyverion-p-1-stop", "new", side="sell")])
    )
    respx.get(f"{PAPER_URL}/v2/orders", params={"status": "closed"}).mock(
        return_value=httpx.Response(
            200, json=[order("hyverion-p-1", "filled", filled="2.5", price="481.2")]
        )
    )
    snapshot = await broker().reconciliation_snapshot()
    assert snapshot.balances == {}
    assert snapshot.open_order_ids == frozenset()
    assert snapshot.position_ids == frozenset({"equity:QQQ"})
    assert snapshot.recent_fill_ids == frozenset({"id-hyverion-p-1:2.5"})


@pytest.mark.asyncio
@respx.mock
async def test_account_label_is_masked_and_auth_failure_is_explicit() -> None:
    respx.get(f"{PAPER_URL}/v2/account").mock(
        return_value=httpx.Response(200, json={"account_number": "PA3ABCD9F2K", "status": "ACTIVE",
                                               "currency": "USD", "cash": "100000",
                                               "equity": "100000", "buying_power": "200000"})
    )
    account = await broker().get_account()
    assert account.account_label == "PA…9F2K"
    assert "PA3ABCD9F2K" not in account.model_dump_json()
    respx.get(f"{PAPER_URL}/v2/account").mock(return_value=httpx.Response(401, json={}))
    health = await broker().health()
    assert not health.connected and health.detail == "broker_auth_failed"


def test_quantities_round_down_to_the_broker_step() -> None:
    whole = BrokerCapabilities(
        fractional_shares=False, shorting=False, extended_hours=False, bracket_orders=True,
        trailing_stop=False, paper=True, live=False, order_types=frozenset({"limit"}),
        quantity_step=Decimal("1"),
    )
    assert round_quantity(Decimal("0.96"), whole) == 0
    assert round_quantity(Decimal("2.99"), whole) == 2


@pytest.mark.asyncio
async def test_entries_wait_for_a_clean_reconciliation_with_an_external_broker(
    tmp_path, now, risk_config
) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'reco.db'}")
    await database.initialize()
    state = TradingStateRepository(database)

    async def context():
        return await state.risk_context(
            mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=now,
            require_reconciliation=True,
        )

    assert (await context()).reconciliation_ok is False  # never reconciled yet
    await state.record_reconciliation_error("broker_unreachable", now=now)
    blocked = RiskEngine(risk_config, FixedClock(now)).evaluate_entry(
        make_proposal(now), make_critic(now), await context()
    )
    assert "exchange_reconciliation_failed" in blocked.reasons
    from trading_bot.broker.reconciliation import ReconciliationSnapshot

    await state.reconcile(
        ReconciliationSnapshot(balances={}, open_order_ids=frozenset(),
                               position_ids=frozenset(), recent_fill_ids=frozenset()),
        now=now + timedelta(seconds=1), compare_balances=False, source="alpaca",
    )
    assert (await context()).reconciliation_ok is True
    # The in-process simulator never needs one.
    assert make_context().reconciliation_ok is True
    await database.close()


def test_broker_endpoints_are_read_only_and_explain_the_simulator(tmp_path) -> None:
    settings = load_settings()
    public = settings.public.model_copy(
        update={"database": settings.public.database.model_copy(
            update={"url": f"sqlite+aiosqlite:///{tmp_path / 'b.db'}"})}
    )
    with control_client(create_control_api(settings.model_copy(update={"public": public}))) as c:
        status = c.get("/api/v1/broker/status").json()
        assert status["provider"] == "simulator" and status["connected"] is True
        assert c.post("/api/v1/broker/reconcile").json()["status"] == "SIMULATOR"
    alpaca = public.model_copy(
        update={"broker": public.broker.model_copy(update={"provider": "alpaca"})}
    )
    with control_client(create_control_api(settings.model_copy(update={"public": alpaca}))) as c:
        status = c.get("/api/v1/broker/status").json()
        assert status["connected"] is False
        assert status["detail"] == "broker_credentials_missing"
        assert c.post("/api/v1/broker/reconcile").json()["status"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_partial_fills_are_recorded_and_an_unprotected_remainder_is_flagged(
    tmp_path, now
) -> None:
    """CANCELED-with-fill must count, and an external broker's remainder is unprotected."""

    from trading_bot.broker.models import Fill, OrderResult

    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'partial.db'}")
    await database.initialize()
    state = TradingStateRepository(database)
    await state.ensure_account(equity=Decimal("10000"), now=now)

    def result(coid: str, side: Side, status: str, filled: str, protected: bool) -> OrderResult:
        return OrderResult(
            order_id="entry-1" if side == Side.BUY else "exit-1", client_order_id=coid,
            mode=TradingMode.PAPER, asset="QQQ", side=side, requested_quantity=Decimal("10"),
            filled_quantity=Decimal(filled), status=status,
            fills=(Fill(fill_id=f"{coid}:{filled}", price=Decimal("480"),
                        quantity=Decimal(filled), fee_usd=Decimal("0"), filled_at=now),),
            protective_stop_active=protected, created_at=now,
        )

    entry = intent(quantity=Decimal("10"), limit_price=Decimal("480"),
                   stop_price=Decimal("478"), target_price=Decimal("484"))
    # Day limit order: 6 filled, remainder cancelled -> status CANCELED, still a position.
    await state.record_execution(
        intent=entry, order=result("hyverion-p-1", Side.BUY, "CANCELED", "6", True), now=now
    )
    [position] = await state.open_positions()
    assert Decimal(str(position["quantity"])) == Decimal("6")
    assert position["protective_stop_active"] is True

    exit_ = intent(client_order_id="hyverion-exit-1", side=Side.SELL, reduce_only=True,
                   position_id="entry-1", quantity=Decimal("6"), limit_price=Decimal("481"))
    await state.record_execution(
        intent=exit_, order=result("hyverion-exit-1", Side.SELL, "CANCELED", "4", False), now=now
    )
    [remainder] = await state.open_positions()
    assert Decimal(str(remainder["quantity"])) == Decimal("2")
    # The native stop was cancelled for the exit: the remainder is not protected.
    assert remainder["protective_stop_active"] is False
    recovery = await state.protective_stop_recovery()
    assert not recovery.ok and recovery.unprotected_position_ids
    await database.close()


@pytest.mark.asyncio
@respx.mock
async def test_whole_share_stop_survives_the_close_and_an_engine_stop() -> None:
    posted: list[dict] = []

    def create(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.read())
        posted.append(payload)
        side = "sell" if payload["type"] == "stop" else "buy"
        return httpx.Response(200, json=order(payload["client_order_id"], "new", side=side))

    respx.post(f"{PAPER_URL}/v2/orders").mock(side_effect=create)
    respx.get(f"{PAPER_URL}/v2/orders:by_client_order_id").mock(
        return_value=httpx.Response(
            200, json=order("hyverion-p-1", "filled", filled="3", qty="3", price="481.20")
        )
    )
    await broker().submit(intent(quantity=Decimal("3")))
    stop = posted[-1]
    assert stop["type"] == "stop" and stop["time_in_force"] == "gtc"


def test_new_entries_are_sized_in_whole_shares() -> None:
    from trading_bot.broker.alpaca import ALPACA_PAPER_CAPABILITIES

    assert round_quantity(Decimal("104.9"), ALPACA_PAPER_CAPABILITIES) == Decimal("104")
