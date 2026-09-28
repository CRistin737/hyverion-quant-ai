from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.broker.base import SubmissionStateUnknown
from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.models import OrderResult
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.core.clock import FixedClock
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent, RiskDecision


def make_intent(now: datetime) -> ExecutionIntent:
    return ExecutionIntent(
        intent_id="intent-1",
        decision_id="decision-1",
        proposal_id="proposal-1",
        client_order_id="client-1",
        mode=TradingMode.PAPER,
        asset="QQQ",
        side=Side.BUY,
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
        stop_price=Decimal("99"),
        target_price=Decimal("102"),
        created_at=now,
    )


def make_decision(now: datetime, verdict: str = "ALLOW") -> RiskDecision:
    return RiskDecision.model_validate(
        {
            "decision_id": "decision-1",
            "proposal_id": "proposal-1",
            "verdict": verdict,
            "reasons": [],
            "level": 0,
            "risk_multiplier": 1,
            "base_risk_usd": 10,
            "allowed_risk_usd": 10,
            "candidate_worst_case_loss_usd": 2,
            "protected_profit_floor_usd": 0,
            "live_trading_allowed": False,
            "shadow_trading": True,
            "decided_at": now,
            "approved_asset": "QQQ",
            "approved_side": "buy",
            "approved_quantity": "1",
            "approved_notional_usd": "100",
        }
    )


def make_exit_intent(now: datetime) -> ExecutionIntent:
    return make_intent(now).model_copy(
        update={
            "decision_id": "exit-decision",
            "proposal_id": "position-1",
            "client_order_id": "exit-client-1",
            "side": Side.SELL,
            "reduce_only": True,
            "position_id": "position-1",
            "exit_reason": "target_reached",
        }
    )


async def test_duplicate_client_order_is_idempotent(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))
    intent = make_intent(now)
    decision = make_decision(now)

    first = await engine.execute(intent, decision)
    second = await engine.execute(intent, decision)

    assert first.order_id == second.order_id
    assert first.fills[0].fill_id == second.fills[0].fill_id


async def test_partial_fill_is_reported(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock, fill_fraction=Decimal("0.4")))

    result = await engine.execute(make_intent(now), make_decision(now))

    assert result.status == "PARTIALLY_FILLED"
    assert result.filled_quantity == Decimal("0.4")
    assert result.protective_stop_active is True


async def test_execution_rejects_non_allow_decision(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))

    with pytest.raises(PermissionError):
        await engine.execute(make_intent(now), make_decision(now, "DENY"))


async def test_reduce_only_exit_accepts_exit_only_decision(
    clock: FixedClock, now: datetime
) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))
    intent = make_exit_intent(now)
    decision = make_decision(now, "EXIT_ONLY").model_copy(
        update={"decision_id": "exit-decision", "proposal_id": "position-1"}
    )

    result = await engine.execute(intent, decision)

    assert result.status == "FILLED"


async def test_timeout_after_submission_reconciles_before_retry(
    clock: FixedClock, now: datetime
) -> None:
    paper = SimulatedBroker(clock)

    class AmbiguousAdapter:
        def __init__(self) -> None:
            self.submissions = 0

        async def lookup(self, client_order_id: str) -> OrderResult | None:
            return await paper.lookup(client_order_id)

        async def submit(self, intent: ExecutionIntent) -> OrderResult:
            self.submissions += 1
            await paper.submit(intent)
            raise SubmissionStateUnknown

    adapter = AmbiguousAdapter()
    result = await ExecutionEngine(adapter).execute(make_intent(now), make_decision(now))

    assert result.status == "FILLED"
    assert adapter.submissions == 1


async def test_cancel_replace_requires_known_open_order(clock: FixedClock, now: datetime) -> None:
    paper = SimulatedBroker(clock, fill_fraction=Decimal("0.4"))
    engine = ExecutionEngine(paper)
    first = await engine.execute(make_intent(now), make_decision(now))
    replacement_intent = make_intent(now).model_copy(
        update={"client_order_id": "client-replacement", "limit_price": Decimal("101")}
    )
    with pytest.raises(PermissionError, match="notional exceeds"):
        await engine.cancel_replace(
            previous_client_order_id=first.client_order_id,
            intent=replacement_intent,
            decision=make_decision(now),
        )
    wider = make_decision(now).model_copy(update={"approved_notional_usd": Decimal("101")})
    replaced = await engine.cancel_replace(
        previous_client_order_id=first.client_order_id,
        intent=replacement_intent,
        decision=wider,
    )
    assert replaced.client_order_id == "client-replacement"
    assert await paper.lookup(first.client_order_id) is None

    with pytest.raises(ValueError, match="previous order was not found"):
        await engine.cancel_replace(
            previous_client_order_id="missing",
            intent=replacement_intent,
            decision=wider,
        )


async def test_exchange_disconnect_with_unknown_state_fails_closed(
    clock: FixedClock, now: datetime
) -> None:
    del clock

    class DisconnectedAdapter:
        async def lookup(self, client_order_id: str) -> OrderResult | None:
            return None

        async def submit(self, intent: ExecutionIntent) -> OrderResult:
            raise SubmissionStateUnknown

    with pytest.raises(SubmissionStateUnknown):
        await ExecutionEngine(DisconnectedAdapter()).execute(make_intent(now), make_decision(now))


async def test_entry_larger_than_approved_is_refused(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))
    oversize = make_intent(now).model_copy(update={"quantity": Decimal("1.01")})
    with pytest.raises(PermissionError, match="quantity exceeds"):
        await engine.execute(oversize, make_decision(now))


async def test_entry_for_other_asset_or_side_is_refused(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))
    with pytest.raises(PermissionError, match="asset/side"):
        await engine.execute(
            make_intent(now).model_copy(update={"side": Side.SELL}), make_decision(now)
        )


@pytest.mark.parametrize("symbol", ["NVDA", "AAPL", "SPY", "TSLA", "BTC/USDT"])
async def test_non_whitelisted_symbol_never_reaches_the_broker(
    clock: FixedClock, now: datetime, symbol: str
) -> None:
    """QQQ is the only executable symbol, even with a matching risk approval."""

    broker = SimulatedBroker(clock)
    engine = ExecutionEngine(broker)
    decision = make_decision(now).model_copy(update={"approved_asset": symbol})
    with pytest.raises(PermissionError, match="SYMBOL_NOT_EXECUTION_WHITELISTED"):
        await engine.execute(make_intent(now).model_copy(update={"asset": symbol}), decision)
    assert await broker.lookup(make_intent(now).client_order_id) is None


async def test_allow_decision_without_approval_fields_is_refused(
    clock: FixedClock, now: datetime
) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))
    bare = make_decision(now).model_copy(update={"approved_quantity": None})
    with pytest.raises(PermissionError, match="does not state"):
        await engine.execute(make_intent(now), bare)


async def test_expired_decision_is_refused(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(
        SimulatedBroker(clock), clock=clock, max_decision_age=timedelta(seconds=60)
    )
    stale = make_decision(now - timedelta(seconds=61))
    with pytest.raises(PermissionError, match="expired"):
        await engine.execute(make_intent(now), stale)
    fresh = make_decision(now - timedelta(seconds=30))
    assert (await engine.execute(make_intent(now), fresh)).status == "FILLED"


async def test_decision_authorizes_only_one_order(clock: FixedClock, now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(clock))
    decision = make_decision(now)
    await engine.execute(make_intent(now), decision)
    # Same approval, brand-new client order id: must not open a second position.
    second = make_intent(now).model_copy(update={"client_order_id": "client-2"})
    with pytest.raises(PermissionError, match="already used"):
        await engine.execute(second, decision)
    # An idempotent retry of the very same order still returns the original result.
    again = await engine.execute(make_intent(now), decision)
    assert again.client_order_id == "client-1"
