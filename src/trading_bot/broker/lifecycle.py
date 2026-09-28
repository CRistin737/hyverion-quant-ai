"""Deterministic operation lifecycle from proposal to post-close evaluation.

The lifecycle is a pure state machine. It never talks to an exchange and never
decides whether to trade; it only records where an operation is so that no open
position can be forgotten when an AI provider, the app or the network fails.
Any transition outside the table fails closed into ``RECOVERY_REQUIRED``.
"""

from __future__ import annotations

from enum import StrEnum

from trading_bot.broker.models import OrderResult
from trading_bot.schemas.common import StrictSchema


class OperationState(StrEnum):
    PROPOSED = "PROPOSED"
    CRITIC_REVIEWED = "CRITIC_REVIEWED"
    RISK_APPROVED = "RISK_APPROVED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    RECONCILED = "RECONCILED"
    EVALUATED = "EVALUATED"
    REJECTED = "REJECTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELED = "CANCELED"
    UNKNOWN = "UNKNOWN"
    SAFE_MODE = "SAFE_MODE"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


S = OperationState

TERMINAL_STATES: frozenset[OperationState] = frozenset({S.EVALUATED, S.REJECTED, S.CANCELED})

# States that can hold exchange exposure (a resting order or a filled quantity).
EXPOSED_STATES: frozenset[OperationState] = frozenset(
    {
        S.SUBMITTED,
        S.ACKNOWLEDGED,
        S.PARTIALLY_FILLED,
        S.OPEN,
        S.EXIT_PENDING,
        S.CLOSING,
        S.CANCEL_PENDING,
        S.UNKNOWN,
        S.SAFE_MODE,
        S.RECOVERY_REQUIRED,
    }
)

_ESCALATION = frozenset({S.SAFE_MODE, S.RECOVERY_REQUIRED})

# The venue state of these operations is unproven; new entries stay blocked.
UNRESOLVED_STATES: frozenset[OperationState] = frozenset(
    {S.UNKNOWN, S.SAFE_MODE, S.RECOVERY_REQUIRED}
)

# UNKNOWN, SAFE_MODE and RECOVERY_REQUIRED resolve only to the state proven by an
# exchange lookup or reconciliation; they never imply a failed order.
_RESOLVED_BY_LOOKUP = frozenset(
    {
        S.SUBMITTED,
        S.ACKNOWLEDGED,
        S.PARTIALLY_FILLED,
        S.OPEN,
        S.CLOSING,
        S.CLOSED,
        S.RECONCILED,
        S.REJECTED,
        S.CANCELED,
    }
)

ALLOWED_TRANSITIONS: dict[OperationState, frozenset[OperationState]] = {
    S.PROPOSED: frozenset({S.CRITIC_REVIEWED, S.REJECTED}) | _ESCALATION,
    S.CRITIC_REVIEWED: frozenset({S.RISK_APPROVED, S.REJECTED}) | _ESCALATION,
    S.RISK_APPROVED: frozenset({S.EXECUTION_PENDING, S.REJECTED}) | _ESCALATION,
    S.EXECUTION_PENDING: frozenset({S.SUBMITTED, S.REJECTED, S.UNKNOWN}) | _ESCALATION,
    S.SUBMITTED: frozenset(
        {
            S.ACKNOWLEDGED,
            S.PARTIALLY_FILLED,
            S.OPEN,
            S.REJECTED,
            S.CANCEL_PENDING,
            S.CANCELED,
            S.UNKNOWN,
        }
    )
    | _ESCALATION,
    S.ACKNOWLEDGED: frozenset(
        {S.PARTIALLY_FILLED, S.OPEN, S.CANCEL_PENDING, S.CANCELED, S.UNKNOWN}
    )
    | _ESCALATION,
    S.PARTIALLY_FILLED: frozenset(
        {S.PARTIALLY_FILLED, S.OPEN, S.CANCEL_PENDING, S.EXIT_PENDING, S.UNKNOWN}
    )
    | _ESCALATION,
    S.OPEN: frozenset({S.EXIT_PENDING, S.UNKNOWN}) | _ESCALATION,
    S.EXIT_PENDING: frozenset({S.CLOSING, S.OPEN, S.UNKNOWN}) | _ESCALATION,
    S.CLOSING: frozenset({S.CLOSED, S.OPEN, S.UNKNOWN}) | _ESCALATION,
    S.CLOSED: frozenset({S.RECONCILED}) | _ESCALATION,
    S.RECONCILED: frozenset({S.EVALUATED}) | _ESCALATION,
    S.CANCEL_PENDING: frozenset({S.CANCELED, S.PARTIALLY_FILLED, S.OPEN, S.UNKNOWN})
    | _ESCALATION,
    S.UNKNOWN: _RESOLVED_BY_LOOKUP | _ESCALATION,
    # Repeated escalation (a second mismatch while already escalated) is idempotent.
    S.SAFE_MODE: _RESOLVED_BY_LOOKUP | _ESCALATION,
    S.RECOVERY_REQUIRED: _RESOLVED_BY_LOOKUP | _ESCALATION,
    S.EVALUATED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELED: frozenset(),
}


class TransitionResult(StrictSchema):
    from_state: OperationState
    to_state: OperationState
    requested_state: OperationState
    legal: bool
    reason: str


def plan_transition(
    current: OperationState,
    target: OperationState,
    *,
    reason: str,
) -> TransitionResult:
    """Return the state to persist; illegal requests fail closed.

    A terminal record is never reopened, so an illegal request against it keeps
    the terminal state and is only audited.
    """

    if target in ALLOWED_TRANSITIONS[current]:
        return TransitionResult(
            from_state=current,
            to_state=target,
            requested_state=target,
            legal=True,
            reason=reason,
        )
    fallback = current if current in TERMINAL_STATES else S.RECOVERY_REQUIRED
    return TransitionResult(
        from_state=current,
        to_state=fallback,
        requested_state=target,
        legal=False,
        reason=f"illegal_transition:{current.value}->{target.value}",
    )


def state_for_order(order: OrderResult) -> OperationState:
    """Project an exchange order status onto the lifecycle."""

    if order.status == "FILLED":
        return S.OPEN
    if order.status == "PARTIALLY_FILLED":
        return S.PARTIALLY_FILLED
    if order.status == "OPEN":
        return S.PARTIALLY_FILLED if order.filled_quantity > 0 else S.ACKNOWLEDGED
    if order.status == "CANCELED":
        # The unfilled remainder is gone; any filled quantity is a live position.
        return S.OPEN if order.filled_quantity > 0 else S.CANCELED
    if order.status == "REJECTED":
        return S.REJECTED
    return S.UNKNOWN
