from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise

import pytest

from trading_bot.broker.lifecycle import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    OperationState,
    plan_transition,
    state_for_order,
)
from trading_bot.broker.models import OrderResult
from trading_bot.schemas.common import Side, TradingMode

NOW = datetime(2026, 9, 23, tzinfo=UTC)

HAPPY_PATH = (
    OperationState.PROPOSED,
    OperationState.CRITIC_REVIEWED,
    OperationState.RISK_APPROVED,
    OperationState.EXECUTION_PENDING,
    OperationState.SUBMITTED,
    OperationState.ACKNOWLEDGED,
    OperationState.PARTIALLY_FILLED,
    OperationState.OPEN,
    OperationState.EXIT_PENDING,
    OperationState.CLOSING,
    OperationState.CLOSED,
    OperationState.RECONCILED,
    OperationState.EVALUATED,
)


def _order(status: str, filled: str = "0") -> OrderResult:
    return OrderResult.model_validate(
        {
            "order_id": "order-1",
            "client_order_id": "client-1",
            "mode": TradingMode.PAPER,
            "asset": "QQQ",
            "side": Side.BUY,
            "requested_quantity": Decimal("1"),
            "filled_quantity": Decimal(filled),
            "status": status,
            "protective_stop_active": True,
            "created_at": NOW,
        }
    )


def test_every_state_has_a_transition_rule() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(OperationState)


def test_documented_happy_path_is_legal() -> None:
    for current, target in pairwise(HAPPY_PATH):
        result = plan_transition(current, target, reason="step")
        assert result.to_state is target
        assert result.legal


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OperationState.PROPOSED, OperationState.EXECUTION_PENDING),
        (OperationState.PROPOSED, OperationState.OPEN),
        (OperationState.CRITIC_REVIEWED, OperationState.SUBMITTED),
        (OperationState.OPEN, OperationState.CLOSED),
        (OperationState.CLOSED, OperationState.EVALUATED),
        (OperationState.OPEN, OperationState.PROPOSED),
    ],
)
def test_illegal_transition_fails_closed_to_recovery_required(
    current: OperationState, target: OperationState
) -> None:
    result = plan_transition(current, target, reason="skip")

    assert not result.legal
    assert result.to_state is OperationState.RECOVERY_REQUIRED
    assert result.reason == f"illegal_transition:{current.value}->{target.value}"


def test_terminal_states_accept_no_transition() -> None:
    for terminal in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[terminal] == frozenset()
        result = plan_transition(terminal, OperationState.SAFE_MODE, reason="late")
        assert not result.legal
        # A terminal record is never reopened, even into recovery.
        assert result.to_state is terminal


def test_safe_mode_and_recovery_are_reachable_from_every_active_state() -> None:
    for state in OperationState:
        if state in TERMINAL_STATES:
            continue
        assert plan_transition(state, OperationState.SAFE_MODE, reason="mismatch").legal
        assert plan_transition(
            state, OperationState.RECOVERY_REQUIRED, reason="operator"
        ).legal


def test_timeout_is_unknown_and_never_implies_rejection() -> None:
    assert plan_transition(
        OperationState.EXECUTION_PENDING, OperationState.UNKNOWN, reason="timeout"
    ).legal
    # UNKNOWN may only resolve after an exchange lookup; it may still be filled.
    assert plan_transition(OperationState.UNKNOWN, OperationState.OPEN, reason="lookup").legal
    assert OperationState.UNKNOWN not in TERMINAL_STATES


@pytest.mark.parametrize(
    ("status", "filled", "expected"),
    [
        ("FILLED", "1", OperationState.OPEN),
        ("PARTIALLY_FILLED", "0.5", OperationState.PARTIALLY_FILLED),
        ("OPEN", "0", OperationState.ACKNOWLEDGED),
        ("REJECTED", "0", OperationState.REJECTED),
        ("CANCELED", "0", OperationState.CANCELED),
        ("CANCELED", "0.5", OperationState.OPEN),
        ("UNKNOWN", "0", OperationState.UNKNOWN),
    ],
)
def test_order_status_maps_to_lifecycle_state(
    status: str, filled: str, expected: OperationState
) -> None:
    assert state_for_order(_order(status, filled)) is expected
