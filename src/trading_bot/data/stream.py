"""Safe public-market stream coordination and replay-gap detection.

The public WebSocket is an ingestion transport, not an authorization path.  A
frame is accepted only after its event timestamp has passed the deterministic
replay/staleness gate.  Gaps and out-of-order frames are surfaced to the
caller so the caller can persist an audit event and skip the cycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Protocol

from trading_bot.schemas.trading import MarketSnapshot

ReplayStatus = Literal["OK", "DUPLICATE", "OUT_OF_ORDER", "GAP", "STALE"]


@dataclass(frozen=True, slots=True)
class ReplayAssessment:
    """Deterministic admission result for one public market frame."""

    symbol: str
    status: ReplayStatus
    event_time: datetime
    previous_event_time: datetime | None
    gap_seconds: Decimal
    age_seconds: Decimal
    detail: str

    @property
    def accepted(self) -> bool:
        return self.status == "OK"


class PublicStreamPort(Protocol):
    def stream_snapshots(
        self, symbol: str, *, max_reconnect_delay_seconds: int, stop_event: asyncio.Event
    ) -> AsyncIterator[MarketSnapshot]: ...


class ReplayGapMonitor:
    """Track per-symbol ordering and bounded event-time continuity."""

    def __init__(
        self,
        *,
        max_gap_seconds: Decimal = Decimal("30"),
        stale_after_seconds: Decimal = Decimal("10"),
    ) -> None:
        if max_gap_seconds <= 0 or stale_after_seconds <= 0:
            raise ValueError("replay and stale thresholds must be positive")
        self._max_gap = max_gap_seconds
        self._stale_after = stale_after_seconds
        self._last_event: dict[str, datetime] = {}

    def observe(self, snapshot: MarketSnapshot, *, now: datetime) -> ReplayAssessment:
        current = _utc(snapshot.event_time)
        received_now = _utc(now)
        previous = self._last_event.get(snapshot.symbol)
        age = Decimal(str(max(0.0, (received_now - current).total_seconds())))
        if previous is None:
            status: ReplayStatus = "STALE" if age > self._stale_after else "OK"
            detail = "first_frame_stale" if status == "STALE" else "first_frame"
            gap = Decimal("0")
        else:
            delta = Decimal(str((current - previous).total_seconds()))
            gap = max(Decimal("0"), delta)
            if delta == 0:
                status = "DUPLICATE"
                detail = "event_timestamp_repeated"
            elif delta < 0:
                status = "OUT_OF_ORDER"
                detail = "event_timestamp_regressed"
            elif delta > self._max_gap:
                status = "GAP"
                detail = "event_time_gap_exceeded_threshold"
            elif age > self._stale_after:
                status = "STALE"
                detail = "event_age_exceeded_threshold"
            else:
                status = "OK"
                detail = "accepted"

        # Keep the newest observed event for continuity.  A regressed frame is
        # never allowed to move the cursor backwards; a duplicate is likewise
        # harmless and leaves the cursor unchanged.
        if previous is None or current > previous:
            self._last_event[snapshot.symbol] = current
        return ReplayAssessment(
            symbol=snapshot.symbol,
            status=status,
            event_time=current,
            previous_event_time=previous,
            gap_seconds=gap,
            age_seconds=age,
            detail=detail,
        )

    def last_event_time(self, symbol: str) -> datetime | None:
        return self._last_event.get(symbol)

    def reset(self, symbol: str | None = None) -> None:
        if symbol is None:
            self._last_event.clear()
        else:
            self._last_event.pop(symbol, None)


class PublicStreamCoordinator:
    """Run one bounded consumer per configured symbol.

    The coordinator intentionally does not know about strategies, providers or
    exchanges.  Its handler receives only an admitted snapshot and the replay
    assessment, keeping the DATA boundary independent from the decision path.
    """

    def __init__(
        self,
        adapter: PublicStreamPort,
        *,
        monitor: ReplayGapMonitor,
        max_reconnect_delay_seconds: int = 60,
    ) -> None:
        if max_reconnect_delay_seconds < 1:
            raise ValueError("max_reconnect_delay_seconds must be positive")
        self._adapter = adapter
        self._monitor = monitor
        self._max_reconnect_delay = max_reconnect_delay_seconds

    async def run(
        self,
        symbols: Iterable[str],
        *,
        stop_event: asyncio.Event,
        handler: Callable[[MarketSnapshot, ReplayAssessment], Awaitable[None]],
    ) -> None:
        normalized = tuple(dict.fromkeys(symbols))
        if not normalized:
            raise ValueError("at least one stream symbol is required")

        async def consume(symbol: str) -> None:
            async for snapshot in self._adapter.stream_snapshots(
                symbol,
                max_reconnect_delay_seconds=self._max_reconnect_delay,
                stop_event=stop_event,
            ):
                if stop_event.is_set():
                    return
                assessment = self._monitor.observe(snapshot, now=datetime.now(UTC))
                await handler(snapshot, assessment)

        tasks = [
            asyncio.create_task(consume(symbol), name=f"market-stream:{symbol}")
            for symbol in normalized
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("stream timestamps must be timezone-aware")
    return value.astimezone(UTC)
