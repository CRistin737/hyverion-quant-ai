"""Alpaca PAPER trading adapter. Only ExecutionEngine may call its mutations.

Verified against the official Trading API docs (docs.alpaca.markets,
2026-09-27): ``POST /v2/orders`` (``client_order_id`` ≤ 128 chars; fractional
quantities for market/limit/stop/stop_limit with ``time_in_force=day``; no
fractional short sales), ``GET /v2/orders:by_client_order_id``,
``DELETE /v2/orders/{id}``, ``GET /v2/account``, ``GET /v2/positions`` and
``GET /v2/orders?status=...``. Paper base URL ``https://paper-api.alpaca.markets``.

Safety properties:
* the host is pinned to the paper endpoint and intents must be PAPER, so this
  class can never reach a live account;
* a timeout on a submission raises :class:`SubmissionStateUnknown`; the engine
  then looks the order up by ``client_order_id`` before any retry;
* every filled entry immediately gets a native protective stop at the broker
  (``<client_order_id>-stop``, good-till-cancelled on whole shares), so
  protection does not depend on the engine, the AI or the time of day: it
  survives the close and an engine stop. Exits cancel that stop first, then sell.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import Any

import httpx

from trading_bot.broker.base import (
    BrokerAccount,
    BrokerCapabilities,
    BrokerHealth,
    BrokerPosition,
    BrokerUnavailable,
    SubmissionStateUnknown,
)
from trading_bot.broker.models import Fill, OrderResult
from trading_bot.broker.reconciliation import ReconciliationSnapshot
from trading_bot.core.clock import Clock
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import ExecutionIntent
from trading_bot.security.credentials import AlpacaPaperCredentials

PAPER_URL = "https://paper-api.alpaca.markets"
PAPER_HOST = "paper-api.alpaca.markets"
STOP_SUFFIX = "-stop"
# Order formatting precision (Alpaca accepts up to 9 decimals; exits of any
# existing fractional position must still be expressible).
QUANTITY_STEP = Decimal("0.000001")
# New entries are sized in whole shares: Alpaca accepts good-till-cancelled
# (GTC) orders only for whole quantities, and the protective stop must survive
# the close and an engine stop (see _place_stop).
ENTRY_QUANTITY_STEP = Decimal("1")
PRICE_STEP = Decimal("0.01")

ALPACA_PAPER_CAPABILITIES = BrokerCapabilities(
    fractional_shares=True,
    shorting=False,  # Hyverion is long-only; Alpaca also forbids fractional shorts.
    extended_hours=False,  # not used: regular session only
    bracket_orders=False,  # brackets need whole shares; a separate native stop is used
    trailing_stop=False,
    paper=True,
    live=False,
    order_types=frozenset({"market", "limit", "stop", "stop_limit"}),
    quantity_step=ENTRY_QUANTITY_STEP,
    fractional_order_types=frozenset({"market", "limit", "stop", "stop_limit"}),
)

_OPEN = {"new", "accepted", "pending_new", "accepted_for_bidding", "held", "calculated",
         "pending_cancel", "pending_replace"}
_CANCELED = {"canceled", "expired", "done_for_day", "replaced"}
_REJECTED = {"rejected", "suspended", "stopped"}


def _dec(value: Any) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal("0")


def _time(value: Any) -> datetime:
    text = str(value).replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        digits = tail[: len(tail) - len(tail.lstrip("0123456789"))]
        text = f"{head}.{digits[:6]}{tail[len(digits):]}"
    parsed = datetime.fromisoformat(text)
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _mask(identifier: str) -> str:
    return f"{identifier[:2]}…{identifier[-4:]}" if len(identifier) > 6 else "…"


class AlpacaPaperBroker:
    provider = "alpaca"
    simulated = False
    capabilities = ALPACA_PAPER_CAPABILITIES

    def __init__(
        self,
        credentials: AlpacaPaperCredentials,
        clock: Clock,
        *,
        base_url: str = PAPER_URL,
        timeout_seconds: float = 10.0,
        fill_wait_seconds: float = 10.0,
        poll_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if httpx.URL(base_url).host != PAPER_HOST:
            # Fail closed: this adapter must never be pointed at a live account.
            raise BrokerUnavailable("broker_live_host_forbidden", base_url)
        self._credentials = credentials
        self._clock = clock
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._fill_wait = fill_wait_seconds
        self._poll = poll_seconds
        self._sleep = sleep
        self._transport = transport

    # -- HTTP ---------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._credentials.headers(),
            timeout=self._timeout,
            transport=self._transport,
        )

    async def _read(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            async with self._client() as client:
                response = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise BrokerUnavailable("broker_unreachable", type(exc).__name__) from exc
        if response.status_code == 404:
            return None
        self._raise_for_auth(response)
        if response.status_code >= 400:
            raise BrokerUnavailable("broker_error", str(response.status_code))
        return response.json()

    @staticmethod
    def _raise_for_auth(response: httpx.Response) -> None:
        if response.status_code == 401:
            raise BrokerUnavailable("broker_auth_failed", "401")

    async def _post_order(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            async with self._client() as client:
                response = await client.post("/v2/orders", json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # The request may have reached Alpaca: never retry blindly.
            raise SubmissionStateUnknown(type(exc).__name__) from exc
        except httpx.HTTPError as exc:
            raise SubmissionStateUnknown(type(exc).__name__) from exc
        self._raise_for_auth(response)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response.status_code, payload if isinstance(payload, dict) else {}

    async def _cancel(self, order_id: str) -> bool:
        """Request a cancel; True if Alpaca accepted it. 404/422 mean it is already done."""

        try:
            async with self._client() as client:
                response = await client.delete(f"/v2/orders/{order_id}")
        except httpx.HTTPError as exc:
            raise BrokerUnavailable("broker_unreachable", type(exc).__name__) from exc
        self._raise_for_auth(response)
        if response.status_code in {404, 422}:
            return False
        if response.status_code >= 400:
            raise BrokerUnavailable("broker_error", str(response.status_code))
        return True

    async def _raw_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        payload = await self._read(
            "/v2/orders:by_client_order_id", {"client_order_id": client_order_id}
        )
        return payload if isinstance(payload, dict) else None

    # -- Mapping --------------------------------------------------------------

    def _result(self, raw: dict[str, Any], intent_mode: TradingMode) -> OrderResult:
        status_text = str(raw.get("status") or "").lower()
        filled = _dec(raw.get("filled_qty"))
        if status_text == "filled":
            status = "FILLED"
        elif status_text == "partially_filled" or (status_text in _CANCELED and filled > 0):
            status = "PARTIALLY_FILLED" if status_text == "partially_filled" else "CANCELED"
        elif status_text in _OPEN:
            status = "OPEN"
        elif status_text in _CANCELED:
            status = "CANCELED"
        elif status_text in _REJECTED:
            status = "REJECTED"
        else:
            status = "UNKNOWN"
        fills: tuple[Fill, ...] = ()
        average = _dec(raw.get("filled_avg_price"))
        if filled > 0 and average > 0:
            fills = (
                Fill(
                    # One aggregated fill per order state; the same id is rebuilt
                    # from the broker during reconciliation.
                    fill_id=f"{raw['id']}:{filled}",
                    price=average,
                    quantity=filled,
                    fee_usd=Decimal("0"),
                    filled_at=_time(raw.get("filled_at") or raw.get("updated_at")),
                ),
            )
        return OrderResult(
            order_id=str(raw["id"]),
            client_order_id=str(raw.get("client_order_id") or ""),
            mode=intent_mode,
            asset=str(raw.get("symbol") or ""),
            side=Side(str(raw.get("side") or "buy").lower()),
            requested_quantity=_dec(raw.get("qty")) or filled or Decimal("1"),
            filled_quantity=filled,
            status=status,
            fills=fills,
            protective_stop_active=False,
            created_at=_time(raw.get("created_at") or self._clock.now().isoformat()),
        )

    # -- ExecutionResultPort -------------------------------------------------

    async def lookup(self, client_order_id: str) -> OrderResult | None:
        raw = await self._raw_by_client_id(client_order_id)
        if raw is None:
            return None
        result = self._result(raw, TradingMode.PAPER)
        if not client_order_id.endswith(STOP_SUFFIX) and result.filled_quantity > 0:
            stop = await self._raw_by_client_id(client_order_id + STOP_SUFFIX)
            active = stop is not None and str(stop.get("status")).lower() in _OPEN
            result = result.model_copy(update={"protective_stop_active": active})
        return result

    async def submit(self, intent: ExecutionIntent) -> OrderResult:
        if intent.mode not in {TradingMode.PAPER, TradingMode.SHADOW}:
            raise BrokerUnavailable("broker_paper_only", intent.mode.value)
        if intent.side == Side.SELL and not intent.reduce_only:
            raise BrokerUnavailable("broker_shorting_disabled")
        quantity = intent.quantity.quantize(QUANTITY_STEP, rounding=ROUND_DOWN)
        if quantity <= 0:
            raise BrokerUnavailable("QUANTITY_BELOW_MINIMUM")
        if intent.reduce_only:
            return await self._exit(intent, quantity)
        return await self._entry(intent, quantity)

    async def _entry(self, intent: ExecutionIntent, quantity: Decimal) -> OrderResult:
        status_code, raw = await self._post_order(
            {
                "symbol": intent.asset,
                "qty": str(quantity),
                "side": "buy",
                "type": "limit",
                "time_in_force": "day",
                "limit_price": str(intent.limit_price.quantize(PRICE_STEP, rounding=ROUND_DOWN)),
                "client_order_id": intent.client_order_id,
            }
        )
        if status_code >= 400 or "id" not in raw:
            return self._rejected(intent, quantity)
        raw = await self._await_fill(raw)
        result = self._result(raw, intent.mode)
        if result.filled_quantity <= 0:
            return result
        protected = await self._place_stop(intent, result.filled_quantity)
        return result.model_copy(update={"protective_stop_active": protected})

    async def _await_fill(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Wait briefly for the limit order; cancel whatever did not fill (day order)."""

        waited = 0.0
        while str(raw.get("status")).lower() in _OPEN | {"partially_filled"}:
            if waited >= self._fill_wait:
                await self._cancel(str(raw["id"]))
                refreshed = await self._raw_by_client_id(str(raw["client_order_id"]))
                return refreshed or raw
            await self._sleep(self._poll)
            waited += self._poll
            refreshed = await self._raw_by_client_id(str(raw["client_order_id"]))
            if refreshed is None:
                break
            raw = refreshed
        return raw

    async def _place_stop(self, intent: ExecutionIntent, quantity: Decimal) -> bool:
        try:
            status_code, raw = await self._post_order(
                {
                    "symbol": intent.asset,
                    "qty": str(quantity),
                    "side": "sell",
                    "type": "stop",
                    # GTC: the stop stays at Alpaca overnight and while the engine
                    # is stopped; exits cancel it before selling. Fractional
                    # quantities only allow "day", so they keep it.
                    "time_in_force": (
                        "gtc" if quantity == quantity.to_integral_value() else "day"
                    ),
                    # A sell stop protects a long: round up (closer to entry),
                    # so rounding can only reduce the risk RiskEngine approved.
                    "stop_price": str(intent.stop_price.quantize(PRICE_STEP, rounding=ROUND_UP)),
                    "client_order_id": intent.client_order_id + STOP_SUFFIX,
                }
            )
        except SubmissionStateUnknown:
            existing = await self._raw_by_client_id(intent.client_order_id + STOP_SUFFIX)
            return existing is not None and str(existing.get("status")).lower() in _OPEN
        # False = no native stop: the position manager exits it immediately (fail closed).
        return status_code < 400 and str(raw.get("status", "")).lower() in _OPEN

    async def _exit(self, intent: ExecutionIntent, quantity: Decimal) -> OrderResult:
        """Cancel the native stop, confirm it did not fill, then sell.

        Check-then-act is not atomic at the broker: the stop can fill between
        our read and our cancel. So after cancelling we read it again; any stop
        fill is returned as the exit (never a second sale of the same shares).
        """

        entry = await self._read(f"/v2/orders/{intent.position_id}") if intent.position_id else None
        if isinstance(entry, dict) and entry.get("client_order_id"):
            stop_id = str(entry["client_order_id"]) + STOP_SUFFIX
            stop = await self._raw_by_client_id(stop_id)
            if stop is not None and str(stop.get("status")).lower() in _OPEN | {"partially_filled"}:
                await self._cancel(str(stop["id"]))
                stop = await self._raw_by_client_id(stop_id)
                if stop is None or str(stop.get("status")).lower() in _OPEN | {"partially_filled"}:
                    # Still working at the broker: selling now could double-sell.
                    raise BrokerUnavailable("protective_stop_cancel_unconfirmed")
            if stop is not None and _dec(stop.get("filled_qty")) > 0:
                # The broker's stop already sold (all or part). Report that fill;
                # any remainder is left unprotected so the next cycle exits it.
                return self._result(stop, intent.mode)
        status_code, raw = await self._post_order(
            {
                "symbol": intent.asset,
                "qty": str(quantity),
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": intent.client_order_id,
            }
        )
        if status_code >= 400 or "id" not in raw:
            return self._rejected(intent, quantity)
        # The native stop is gone: an unfilled remainder is unprotected (False),
        # so the deterministic manager exits it on the next cycle (fail closed).
        return self._result(await self._await_fill(raw), intent.mode)

    def _rejected(self, intent: ExecutionIntent, quantity: Decimal) -> OrderResult:
        return OrderResult(
            order_id=f"rejected:{intent.client_order_id}",
            client_order_id=intent.client_order_id,
            mode=intent.mode,
            asset=intent.asset,
            side=intent.side,
            requested_quantity=quantity,
            filled_quantity=Decimal("0"),
            status="REJECTED",
            protective_stop_active=False,
            created_at=self._clock.now(),
        )

    # -- Read surface ----------------------------------------------------------

    async def health(self) -> BrokerHealth:
        try:
            account = await self._read("/v2/account")
        except BrokerUnavailable as exc:
            return BrokerHealth(
                provider=self.provider, environment="paper", connected=False, detail=exc.code
            )
        connected = isinstance(account, dict) and str(account.get("status")).upper() == "ACTIVE"
        return BrokerHealth(
            provider=self.provider,
            environment="paper",
            connected=connected,
            detail="Alpaca Paper account active." if connected else "account_not_active",
        )

    async def get_account(self) -> BrokerAccount:
        raw = await self._read("/v2/account")
        if not isinstance(raw, dict):
            raise BrokerUnavailable("broker_error", "account")
        return BrokerAccount(
            provider=self.provider,
            environment="paper",
            account_label=_mask(str(raw.get("account_number") or "")),
            currency=str(raw.get("currency") or "USD"),
            status=str(raw.get("status") or "UNKNOWN"),
            equity=_dec(raw.get("equity")),
            cash=_dec(raw.get("cash")),
            buying_power=_dec(raw.get("buying_power")),
        )

    async def get_positions(self) -> tuple[BrokerPosition, ...]:
        rows = await self._read("/v2/positions") or []
        return tuple(
            BrokerPosition(
                symbol=str(row["symbol"]),
                quantity=_dec(row.get("qty")),
                average_entry_price=_dec(row.get("avg_entry_price")),
                market_value=_dec(row.get("market_value")),
                unrealized_pnl=_dec(row.get("unrealized_pl")),
            )
            for row in rows
            if isinstance(row, dict) and _dec(row.get("avg_entry_price")) > 0
        )

    async def get_open_orders(self) -> tuple[OrderResult, ...]:
        rows = await self._read("/v2/orders", {"status": "open", "limit": 500}) or []
        return tuple(self._result(row, TradingMode.PAPER) for row in rows if isinstance(row, dict))

    async def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        """Broker side of a restart reconciliation (positions, orders, fills).

        Balances are not compared: the paper account's size is set at Alpaca and
        differs from Hyverion's configured risk capital. Protective stops are
        Hyverion's own and are tracked as protection, not as working orders.
        """

        positions = await self.get_positions()
        open_orders = await self.get_open_orders()
        closed = await self._read("/v2/orders", {"status": "closed", "limit": 500}) or []
        fills = {
            fill.fill_id
            for row in closed
            if isinstance(row, dict)
            for fill in self._result(row, TradingMode.PAPER).fills
        }
        return ReconciliationSnapshot(
            balances={},
            open_order_ids=frozenset(
                order.order_id
                for order in open_orders
                if not order.client_order_id.endswith(STOP_SUFFIX)
            ),
            position_ids=frozenset(f"equity:{position.symbol}" for position in positions),
            recent_fill_ids=frozenset(fills),
        )
