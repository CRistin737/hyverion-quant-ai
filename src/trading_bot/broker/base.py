"""Broker ports. Agents never see these; only ExecutionEngine holds an adapter.

``ExecutionResultPort`` is the narrow idempotent contract ExecutionEngine uses
(submit once per client order id, look it up before any retry). ``BrokerAdapter``
is the full read/reconcile surface a real broker adapter implements (§9 of the
QQQ migration plan). Each adapter declares its capabilities; Hyverion asks
instead of assuming (fractional shares, bracket orders, extended hours…).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field

from trading_bot.broker.models import OrderResult
from trading_bot.broker.reconciliation import ReconciliationSnapshot
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import ExecutionIntent

OrderType = Literal["market", "limit", "stop", "stop_limit"]


class SubmissionStateUnknown(RuntimeError):
    """The request may have reached the broker; blind retry is forbidden."""


class BrokerUnavailable(RuntimeError):
    """The broker cannot be used (missing credentials, wrong environment, outage)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class BrokerCapabilities(StrictSchema):
    fractional_shares: bool
    shorting: bool
    extended_hours: bool
    bracket_orders: bool
    trailing_stop: bool
    paper: bool
    live: bool
    order_types: frozenset[OrderType]
    # Smallest quantity step the broker accepts (1 = whole shares only).
    quantity_step: Decimal = Field(gt=0)
    # Fractional orders are often restricted to a subset of order types.
    fractional_order_types: frozenset[OrderType] = frozenset()


class BrokerHealth(StrictSchema):
    provider: str
    environment: Literal["paper", "live"]
    connected: bool
    detail: str


class BrokerAccount(StrictSchema):
    provider: str
    environment: Literal["paper", "live"]
    # Masked, e.g. "PA3…9F2K". Never the full identifier, never a credential.
    account_label: str
    currency: str
    status: str
    equity: Decimal
    cash: Decimal
    buying_power: Decimal


class BrokerPosition(StrictSchema):
    symbol: str
    quantity: Decimal
    average_entry_price: Decimal = Field(gt=0)
    market_value: Decimal
    unrealized_pnl: Decimal


class ExecutionResultPort(Protocol):
    async def submit(self, intent: ExecutionIntent) -> OrderResult: ...

    async def lookup(self, client_order_id: str) -> OrderResult | None: ...


class CancelReplacePort(Protocol):
    """Optional mutation boundary for an explicit, reconciled replacement."""

    async def cancel_replace(
        self,
        *,
        previous_client_order_id: str,
        intent: ExecutionIntent,
    ) -> OrderResult: ...


class BrokerAdapter(ExecutionResultPort, Protocol):
    """Full broker surface. Order mutations stay behind ExecutionEngine."""

    provider: str
    capabilities: BrokerCapabilities

    async def health(self) -> BrokerHealth: ...

    async def get_account(self) -> BrokerAccount: ...

    async def get_positions(self) -> tuple[BrokerPosition, ...]: ...

    async def get_open_orders(self) -> tuple[OrderResult, ...]: ...

    async def reconciliation_snapshot(self) -> ReconciliationSnapshot: ...


def round_quantity(quantity: Decimal, capabilities: BrokerCapabilities) -> Decimal:
    """Floor a quantity to the broker's step. Never rounds up (never more risk)."""

    if quantity <= 0:
        return Decimal("0")
    step = capabilities.quantity_step
    return (quantity // step) * step
