"""Backtest data audit (§99 of the QQQ plan). A dataset that fails is not replayed.

Checks bars against the NYSE calendar: duplicates, bars outside the regular
session (weekends, holidays, pre/after-hours), missing minutes inside sessions,
out-of-order data and implausible jumps. The free IEX feed skips minutes with no
IEX trade, so a small missing ratio is tolerated and reported, never hidden.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any

import pandas as pd
from pydantic import Field

from trading_bot.market.clock import nyse_calendar, trading_day
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import Candle

DATA_AUDIT_FAILED = "data_audit_failed"
MAX_MISSING_RATIO = Decimal("0.05")
MAX_BAR_JUMP = Decimal("0.05")  # 5 % close-to-close inside one session minute


class DataAuditReport(StrictSchema):
    ok: bool
    bars: int = Field(ge=0)
    sessions: int = Field(ge=0)
    expected_bars: int = Field(ge=0)
    missing_bars: int = Field(ge=0)
    missing_ratio: Decimal = Field(ge=0)
    duplicate_bars: int = Field(ge=0)
    out_of_order: int = Field(ge=0)
    outside_session_bars: int = Field(ge=0)
    outliers: int = Field(ge=0)
    issues: tuple[str, ...]


def audit_minute_bars(
    candles: Sequence[Candle],
    *,
    calendar: Any | None = None,
    max_missing_ratio: Decimal = MAX_MISSING_RATIO,
) -> DataAuditReport:
    source = calendar or nyse_calendar()
    issues: list[str] = []
    times = [candle.event_time for candle in candles]
    duplicates = sum(count - 1 for count in Counter(times).values() if count > 1)
    out_of_order = sum(1 for a, b in pairwise(times) if b < a)
    days = sorted({trading_day(moment) for moment in times})
    expected = 0
    inside = 0
    outside = 0
    for day in days:
        label = pd.Timestamp(day)
        if not source.is_session(label):
            outside += sum(1 for moment in times if trading_day(moment) == day)
            continue
        open_at = pd.Timestamp(source.session_open(label)).to_pydatetime()
        close_at = pd.Timestamp(source.session_close(label)).to_pydatetime()
        day_times = {m for m in times if trading_day(m) == day}
        in_session = sorted(m for m in day_times if open_at < m <= close_at)
        outside += len(day_times) - len(in_session)
        inside += len(in_session)
        # The first and last sessions of a dataset may be partial: count the
        # minutes the dataset claims to cover, not the whole day.
        start, end = open_at, close_at
        if in_session and day == days[0]:
            start = in_session[0] - timedelta(minutes=1)
        if in_session and day == days[-1]:
            end = in_session[-1]
        expected += int((end - start) / timedelta(minutes=1))
    missing = max(0, expected - inside)
    ratio = Decimal(missing) / Decimal(expected) if expected else Decimal("1")
    outliers = 0
    ordered = sorted(candles, key=lambda candle: candle.event_time)
    for previous, current in pairwise(ordered):
        same_session = trading_day(previous.event_time) == trading_day(current.event_time)
        if same_session and abs(current.close / previous.close - 1) > MAX_BAR_JUMP:
            outliers += 1
    if not candles:
        issues.append("no_bars")
    if duplicates:
        issues.append("duplicate_bars")
    if out_of_order:
        issues.append("bars_out_of_order")
    if outside:
        issues.append("bars_outside_regular_session")
    if ratio > max_missing_ratio:
        issues.append("too_many_missing_bars")
    if outliers:
        issues.append("implausible_price_jumps")
    # Bars outside the session are dropped by callers before replay; they are
    # reported but only block when nothing else is left.
    blocking = [issue for issue in issues if issue != "bars_outside_regular_session"]
    return DataAuditReport(
        ok=not blocking,
        bars=len(candles),
        sessions=sum(1 for day in days if source.is_session(pd.Timestamp(day))),
        expected_bars=expected,
        missing_bars=missing,
        missing_ratio=ratio,
        duplicate_bars=duplicates,
        out_of_order=out_of_order,
        outside_session_bars=outside,
        outliers=outliers,
        issues=tuple(issues),
    )
