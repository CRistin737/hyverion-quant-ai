"""US equity market clock for QQQ (NYSE calendar, America/New_York).

Timestamps are stored in UTC everywhere; New York time is only used here, to
answer "is the market open?", "which trading day is this?" and "how long until
the close?". Holidays, early closes and daylight-saving changes come from the
maintained ``exchange_calendars`` XNYS calendar, never from fixed UTC offsets.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from trading_bot.core.clock import Clock
from trading_bot.core.instruments import MARKET_TIMEZONE
from trading_bot.schemas.trading import Candle

NEW_YORK = ZoneInfo(MARKET_TIMEZONE)
PRE_MARKET_START = time(4, 0)
AFTER_HOURS_END = time(20, 0)
OPENING_MINUTES = 30
POWER_HOUR_MINUTES = 60
CLOSING_MINUTES = 15


class SessionState(StrEnum):
    CLOSED = "CLOSED"
    PRE_MARKET = "PRE_MARKET"
    OPENING = "OPENING"
    REGULAR = "REGULAR"
    MIDDAY = "MIDDAY"
    POWER_HOUR = "POWER_HOUR"
    CLOSING = "CLOSING"
    AFTER_HOURS = "AFTER_HOURS"


REGULAR_STATES = frozenset(
    {
        SessionState.OPENING,
        SessionState.REGULAR,
        SessionState.MIDDAY,
        SessionState.POWER_HOUR,
        SessionState.CLOSING,
    }
)


class TimeBucket(StrEnum):
    """Intraday buckets used to measure performance by time of day (§8)."""

    OPENING_DISCOVERY = "OPENING_DISCOVERY"  # 09:30-10:00 ET
    MORNING = "MORNING"  # 10:00-11:30 ET
    MIDDAY = "MIDDAY"  # 11:30-14:00 ET
    AFTERNOON = "AFTERNOON"  # 14:00-15:30 ET
    CLOSING_FLOW = "CLOSING_FLOW"  # 15:30-close
    OUTSIDE = "OUTSIDE"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    state: SessionState
    trading_day: date
    is_session_day: bool
    open_at: datetime | None
    close_at: datetime | None
    early_close: bool
    next_open: datetime
    next_close: datetime
    minutes_since_open: int | None
    minutes_to_close: int | None
    time_bucket: TimeBucket

    @property
    def is_open(self) -> bool:
        return self.state in REGULAR_STATES

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "is_open": self.is_open,
            "trading_day": self.trading_day.isoformat(),
            "is_session_day": self.is_session_day,
            "open_at": self.open_at.isoformat() if self.open_at else None,
            "close_at": self.close_at.isoformat() if self.close_at else None,
            "early_close": self.early_close,
            "next_open": self.next_open.isoformat(),
            "next_close": self.next_close.isoformat(),
            "minutes_since_open": self.minutes_since_open,
            "minutes_to_close": self.minutes_to_close,
            "time_bucket": self.time_bucket.value,
            "timezone": MARKET_TIMEZONE,
        }


def trading_day(timestamp: datetime) -> date:
    """The New York calendar date a UTC timestamp belongs to (the PnL day)."""

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("trading_day requires a timezone-aware timestamp")
    return timestamp.astimezone(NEW_YORK).date()


@cache
def nyse_calendar() -> Any:
    """The XNYS calendar, built once per process (it is immutable)."""

    return xcals.get_calendar("XNYS")


def _utc(value: Any) -> datetime:
    converted: datetime = pd.Timestamp(value).tz_convert("UTC").to_pydatetime()
    return converted


class MarketClock:
    """Answers session questions for an injected clock (tests use FixedClock)."""

    def __init__(self, clock: Clock, *, calendar: Any | None = None) -> None:
        self._clock = clock
        self._calendar = calendar or nyse_calendar()

    def now(self) -> datetime:
        return self._clock.now().astimezone(UTC)

    def snapshot(self, at: datetime | None = None) -> SessionSnapshot:
        moment = (at or self.now()).astimezone(UTC)
        local = moment.astimezone(NEW_YORK)
        day = local.date()
        label = pd.Timestamp(day)
        is_session = bool(self._calendar.is_session(label))
        open_at = close_at = None
        early_close = False
        if is_session:
            open_at = _utc(self._calendar.session_open(label))
            close_at = _utc(self._calendar.session_close(label))
            regular_close = datetime.combine(day, time(16, 0), tzinfo=NEW_YORK)
            early_close = close_at < regular_close.astimezone(UTC)
        stamp = pd.Timestamp(moment)
        next_open = _utc(self._calendar.next_open(stamp))
        next_close = _utc(self._calendar.next_close(stamp))
        state = self._state(local, open_at, close_at)
        minutes_since_open = minutes_to_close = None
        if state in REGULAR_STATES and open_at is not None and close_at is not None:
            minutes_since_open = int((moment - open_at).total_seconds() // 60)
            minutes_to_close = int((close_at - moment).total_seconds() // 60)
        return SessionSnapshot(
            state=state,
            trading_day=day,
            is_session_day=is_session,
            open_at=open_at,
            close_at=close_at,
            early_close=early_close,
            next_open=next_open,
            next_close=next_close,
            minutes_since_open=minutes_since_open,
            minutes_to_close=minutes_to_close,
            time_bucket=self._bucket(local, open_at, close_at, state),
        )

    def state(self, at: datetime | None = None) -> SessionState:
        return self.snapshot(at).state

    def is_open(self, at: datetime | None = None) -> bool:
        return self.snapshot(at).is_open

    def next_open(self, at: datetime | None = None) -> datetime:
        return self.snapshot(at).next_open

    def next_close(self, at: datetime | None = None) -> datetime:
        return self.snapshot(at).next_close

    def time_to_close(self, at: datetime | None = None) -> timedelta | None:
        snap = self.snapshot(at)
        if not snap.is_open or snap.close_at is None:
            return None
        return snap.close_at - (at or self.now()).astimezone(UTC)

    def session_id(self, at: datetime | None = None) -> str:
        return trading_day(at or self.now()).isoformat()

    def seconds_until_pre_market(self, at: datetime | None = None) -> float:
        """How long the engine may sleep before the next pre-market begins."""

        moment = (at or self.now()).astimezone(UTC)
        open_local = self.next_open(moment).astimezone(NEW_YORK)
        pre_market = datetime.combine(open_local.date(), PRE_MARKET_START, tzinfo=NEW_YORK)
        return max(0.0, (pre_market.astimezone(UTC) - moment).total_seconds())

    @staticmethod
    def _state(
        local: datetime, open_at: datetime | None, close_at: datetime | None
    ) -> SessionState:
        if open_at is None or close_at is None:
            return SessionState.CLOSED
        moment = local.astimezone(UTC)
        pre_start = datetime.combine(local.date(), PRE_MARKET_START, tzinfo=NEW_YORK)
        after_end = datetime.combine(local.date(), AFTER_HOURS_END, tzinfo=NEW_YORK)
        if moment < pre_start.astimezone(UTC) or moment >= after_end.astimezone(UTC):
            return SessionState.CLOSED
        if moment < open_at:
            return SessionState.PRE_MARKET
        if moment >= close_at:
            return SessionState.AFTER_HOURS
        if moment >= close_at - timedelta(minutes=CLOSING_MINUTES):
            return SessionState.CLOSING
        if moment >= close_at - timedelta(minutes=POWER_HOUR_MINUTES):
            return SessionState.POWER_HOUR
        if moment < open_at + timedelta(minutes=OPENING_MINUTES):
            return SessionState.OPENING
        clock_time = local.time()
        if time(11, 30) <= clock_time < time(14, 0):
            return SessionState.MIDDAY
        return SessionState.REGULAR

    @staticmethod
    def _bucket(
        local: datetime,
        open_at: datetime | None,
        close_at: datetime | None,
        state: SessionState,
    ) -> TimeBucket:
        if state not in REGULAR_STATES or open_at is None or close_at is None:
            return TimeBucket.OUTSIDE
        clock_time = local.time()
        if clock_time < time(10, 0):
            return TimeBucket.OPENING_DISCOVERY
        if local.astimezone(UTC) >= close_at - timedelta(minutes=30):
            return TimeBucket.CLOSING_FLOW
        if clock_time < time(11, 30):
            return TimeBucket.MORNING
        if clock_time < time(14, 0):
            return TimeBucket.MIDDAY
        if clock_time < time(15, 30):
            return TimeBucket.AFTERNOON
        return TimeBucket.CLOSING_FLOW


def regular_session_only(
    candles: Sequence[Candle], *, calendar: Any | None = None
) -> tuple[Candle, ...]:
    """Keep bars that close inside a NYSE regular session (early closes included).

    Pre-market, after-hours, weekend and holiday bars are dropped: strategies and
    replays are validated on the session they are allowed to trade.
    """

    source = calendar or nyse_calendar()
    bounds: dict[date, tuple[datetime, datetime] | None] = {}
    kept: list[Candle] = []
    for candle in candles:
        day = trading_day(candle.event_time)
        if day not in bounds:
            label = pd.Timestamp(day)
            bounds[day] = (
                (_utc(source.session_open(label)), _utc(source.session_close(label)))
                if source.is_session(label)
                else None
            )
        window = bounds[day]
        if window is not None and window[0] < candle.event_time <= window[1]:
            kept.append(candle)
    return tuple(kept)


def sessions_in(candles: Sequence[Candle]) -> int:
    return len({trading_day(candle.event_time) for candle in candles})
