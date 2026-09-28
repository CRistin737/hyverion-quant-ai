"""Market-data ports, separate from the broker (§64 of the QQQ plan).

A provider returns validated ``MarketSnapshot``/``Candle`` objects that carry
their lineage (provider, feed, delayed or not). Missing credentials or an
outage raise :class:`MarketDataUnavailable`; callers fail closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import Candle, MarketSnapshot

TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")


class ComponentQuote(StrictSchema):
    """Today's state of one observed stock (Nasdaq-100 component or sensor ETF)."""

    symbol: str
    last: Decimal
    previous_close: Decimal
    session_open: Decimal | None = None
    session_vwap: Decimal | None = None
    session_volume: Decimal = Decimal("0")
    as_of: datetime

    @property
    def change(self) -> Decimal:
        return self.last / self.previous_close - 1


class NewsArticle(StrictSchema):
    """A news item as published by a wire. Untrusted text: data, never instructions."""

    article_id: str
    headline: str
    summary: str = ""
    source: str
    url: str | None = None
    symbols: tuple[str, ...] = ()
    created_at: datetime
    updated_at: datetime | None = None


class OptionQuote(StrictSchema):
    contract: str
    underlying: str
    expiration: str
    option_type: str  # "call" | "put"
    strike: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    implied_volatility: Decimal | None = None
    delta: Decimal | None = None
    as_of: datetime | None = None


class MarketDataUnavailable(RuntimeError):
    """The provider cannot serve data now. ``code`` is a stable, translatable id."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


class EquityMarketDataProvider(Protocol):
    provider: str
    feed: str | None

    async def fetch_snapshot(self, symbol: str) -> MarketSnapshot: ...

    async def fetch_candles(
        self, symbol: str, *, interval: str = "1m", limit: int = 20
    ) -> tuple[Candle, ...]: ...

    async def fetch_candle_range(
        self, symbol: str, *, start: datetime, end: datetime, interval: str = "1m"
    ) -> tuple[Candle, ...]: ...

    def stream_snapshots(
        self, symbol: str, *, max_reconnect_delay_seconds: int, stop_event: asyncio.Event
    ) -> AsyncIterator[MarketSnapshot]: ...
