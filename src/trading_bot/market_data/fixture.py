"""Deterministic synthetic QQQ data for tests, demos and offline development.

Prices are a pure function of the minute timestamp, so every call agrees with
every other call. Data is always labelled ``provider="fixture"`` and must never
be mistaken for a real feed.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trading_bot.core.clock import Clock
from trading_bot.market_data.base import TIMEFRAMES, MarketDataUnavailable
from trading_bot.schemas.trading import Candle, MarketSnapshot

_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440}
_CENT = Decimal("0.01")


def fixture_price(moment: datetime, base: Decimal = Decimal("480")) -> Decimal:
    minute = int(moment.timestamp() // 60)
    wave = 0.004 * math.sin(minute / 97) + 0.0015 * math.sin(minute / 13)
    return (base * Decimal(str(1 + wave))).quantize(_CENT)


class FixtureMarketData:
    provider = "fixture"
    feed: str | None = "synthetic"

    def __init__(self, clock: Clock, *, base_price: Decimal = Decimal("480")) -> None:
        self._clock = clock
        self._base = base_price

    def _candle(self, symbol: str, start: datetime, minutes: int, interval: str) -> Candle:
        points = [
            fixture_price(start + timedelta(minutes=i), self._base) for i in range(minutes + 1)
        ]
        close_time = start + timedelta(minutes=minutes)
        return Candle(
            symbol=symbol,
            interval=interval,
            open=points[0],
            high=max(points),
            low=min(points),
            close=points[-1],
            volume=Decimal(2500 * minutes),
            vwap=(sum(points, Decimal("0")) / len(points)).quantize(_CENT),
            trades=40 * minutes,
            event_time=close_time,
            received_time=close_time,
            processed_time=close_time,
        )

    async def fetch_snapshot(self, symbol: str) -> MarketSnapshot:
        now = self._clock.now().astimezone(UTC)
        last = fixture_price(now, self._base)
        volume = Decimal("1200000")
        return MarketSnapshot(
            symbol=symbol,
            bid=last - _CENT,
            ask=last,
            last=last,
            session_volume=volume,
            session_dollar_volume=volume * last,
            event_time=now,
            received_time=now,
            processed_time=now,
            provider=self.provider,
            feed=self.feed,
            is_delayed=False,
        )

    async def fetch_candles(
        self, symbol: str, *, interval: str = "1m", limit: int = 20
    ) -> tuple[Candle, ...]:
        if interval not in TIMEFRAMES:
            raise MarketDataUnavailable("unsupported_interval", interval)
        step = _MINUTES[interval]
        end = self._clock.now().astimezone(UTC).replace(second=0, microsecond=0)
        first = end - timedelta(minutes=step * limit)
        return tuple(
            self._candle(symbol, first + timedelta(minutes=step * i), step, interval)
            for i in range(limit)
        )

    async def fetch_candle_range(
        self, symbol: str, *, start: datetime, end: datetime, interval: str = "1m"
    ) -> tuple[Candle, ...]:
        step = _MINUTES[interval]
        count = max(0, int((end - start).total_seconds() // (60 * step)))
        return tuple(
            self._candle(symbol, start + timedelta(minutes=step * i), step, interval)
            for i in range(count)
        )

    async def stream_snapshots(
        self, symbol: str, *, max_reconnect_delay_seconds: int, stop_event: asyncio.Event
    ) -> AsyncIterator[MarketSnapshot]:
        while not stop_event.is_set():
            yield await self.fetch_snapshot(symbol)
            await asyncio.sleep(1)
