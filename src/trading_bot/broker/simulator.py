from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from trading_bot.broker.base import BrokerCapabilities, BrokerHealth
from trading_bot.broker.models import Fill, OrderResult
from trading_bot.core.clock import Clock
from trading_bot.schemas.trading import ExecutionIntent

SIMULATOR_CAPABILITIES = BrokerCapabilities(
    fractional_shares=True,
    shorting=False,
    extended_hours=False,
    bracket_orders=False,
    trailing_stop=False,
    paper=True,
    live=False,
    order_types=frozenset({"market", "limit"}),
    quantity_step=Decimal("0.000001"),
    fractional_order_types=frozenset({"market", "limit"}),
)


class SimulatedBroker:
    """Deterministic, idempotent in-process broker. It never holds credentials.

    Fills are simulated at the limit price plus slippage; stops and targets are
    enforced by the deterministic position loop, not by the simulator.
    """

    provider = "simulator"
    simulated = True
    capabilities = SIMULATOR_CAPABILITIES

    def __init__(
        self,
        clock: Clock,
        *,
        fee_bps: Decimal = Decimal("10"),
        slippage_bps: Decimal = Decimal("5"),
        fill_fraction: Decimal = Decimal("1"),
    ) -> None:
        if not Decimal("0") < fill_fraction <= Decimal("1"):
            raise ValueError("fill_fraction must be within (0, 1]")
        self._clock = clock
        self._fee_bps = fee_bps
        self._slippage_bps = slippage_bps
        self._fill_fraction = fill_fraction
        self._orders: dict[str, OrderResult] = {}

    async def health(self) -> BrokerHealth:
        return BrokerHealth(
            provider=self.provider,
            environment="paper",
            connected=True,
            detail="In-process paper simulator.",
        )

    async def lookup(self, client_order_id: str) -> OrderResult | None:
        return self._orders.get(client_order_id)

    async def submit(self, intent: ExecutionIntent) -> OrderResult:
        existing = self._orders.get(intent.client_order_id)
        if existing is not None:
            return existing
        direction = Decimal("1") if intent.side.value == "buy" else Decimal("-1")
        fill_price = intent.limit_price * (
            Decimal("1") + direction * self._slippage_bps / Decimal("10000")
        )
        filled_quantity = intent.quantity * self._fill_fraction
        fee = fill_price * filled_quantity * self._fee_bps / Decimal("10000")
        fill = Fill(
            fill_id=str(uuid4()),
            price=fill_price,
            quantity=filled_quantity,
            fee_usd=fee,
            filled_at=self._clock.now(),
        )
        status = "FILLED" if filled_quantity == intent.quantity else "PARTIALLY_FILLED"
        result = OrderResult(
            order_id=str(uuid4()),
            client_order_id=intent.client_order_id,
            mode=intent.mode,
            asset=intent.asset,
            side=intent.side,
            requested_quantity=intent.quantity,
            filled_quantity=filled_quantity,
            status=status,
            fills=(fill,),
            protective_stop_active=True,
            created_at=self._clock.now(),
        )
        self._orders[intent.client_order_id] = result
        return result

    async def cancel_replace(
        self,
        *,
        previous_client_order_id: str,
        intent: ExecutionIntent,
    ) -> OrderResult:
        """Replace an open paper order only after the previous ID is known."""

        previous = self._orders.get(previous_client_order_id)
        if previous is None:
            raise ValueError("previous paper order was not found")
        if previous.status not in {"OPEN", "PARTIALLY_FILLED"}:
            raise ValueError("only an open or partially filled paper order can be replaced")
        self._orders.pop(previous_client_order_id, None)
        return await self.submit(intent)
