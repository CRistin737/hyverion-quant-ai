"""HistoricalAnalogEngine (§56) and event memory (§119), strictly point-in-time.

* **Analogs**: describe today's session so far (gap, move since the open,
  realized volatility, distance to VWAP, opening range) and find the most
  similar *past* sessions at the same minute. Report how the rest of those
  days went: up rate, median move, median MFE and MAE, and sample quality.
  History is context, not certainty.
* **Event memory**: how QQQ moved in the 30 minutes after each past FOMC,
  CPI, NFP, PCE or GDP release. Small samples are flagged, never extrapolated.

Only sessions that ended before the one being evaluated are used (§55).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median

from trading_bot.macro.calendar import MacroEvent
from trading_bot.market.clock import trading_day
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import Candle

MIN_SAMPLE = 20
NEIGHBOURS = 25


class SessionShape(StrictSchema):
    day: date
    gap: float
    move: float  # open → cutoff
    volatility: float  # realized, per bar, until cutoff
    vwap_distance: float
    opening_range: float


class AnalogReport(StrictSchema):
    sample: int
    minute: int
    up_rate: Decimal | None
    median_rest_of_day: Decimal | None
    median_mfe: Decimal | None
    median_mae: Decimal | None
    mean_distance: Decimal | None
    quality: str  # "insufficient" | "low" | "ok"
    days: tuple[str, ...]


class EventReaction(StrictSchema):
    event_type: str
    samples: int
    up_rate: Decimal | None
    median_abs_move: Decimal | None
    median_move: Decimal | None
    small_sample: bool


def group_sessions(candles: Sequence[Candle]) -> dict[date, list[Candle]]:
    sessions: dict[date, list[Candle]] = defaultdict(list)
    for candle in sorted(candles, key=lambda item: item.event_time):
        sessions[trading_day(candle.event_time)].append(candle)
    return dict(sessions)


def _shape(
    day: date, bars: Sequence[Candle], previous_close: float | None, minute: int
) -> SessionShape | None:
    if len(bars) < max(minute, 5) or previous_close is None or previous_close <= 0:
        return None
    window = bars[:minute]
    open_price = float(window[0].open)
    last = float(window[-1].close)
    closes = [float(bar.close) for bar in window]
    returns = [math.log(b / a) for a, b in pairwise(closes) if a > 0]
    volume = sum(float(bar.volume) for bar in window) or 1.0
    vwap = sum(float(bar.vwap or bar.close) * float(bar.volume) for bar in window) / volume
    first = bars[: min(30, len(bars))]
    high = max(float(bar.high) for bar in first)
    low = min(float(bar.low) for bar in first)
    return SessionShape(
        day=day,
        gap=open_price / previous_close - 1,
        move=last / open_price - 1,
        volatility=(sum(r * r for r in returns) / len(returns)) ** 0.5 if returns else 0.0,
        vwap_distance=last / vwap - 1 if vwap else 0.0,
        opening_range=(high - low) / open_price,
    )


def _vector(shape: SessionShape) -> tuple[float, ...]:
    return (shape.gap, shape.move, shape.volatility, shape.vwap_distance, shape.opening_range)


def _distance(
    vector: tuple[float, ...], target: tuple[float, ...], scales: list[float]
) -> float:
    return math.sqrt(
        sum(((a - b) / s) ** 2 for a, b, s in zip(vector, target, scales, strict=True))
    )


def find_analogs(
    history: dict[date, list[Candle]], today: list[Candle], *, today_day: date, minute: int
) -> AnalogReport:
    """Past sessions most similar to today at ``minute`` bars after the open."""

    days = sorted(day for day in history if day < today_day)  # never today or later
    closes = {day: float(history[day][-1].close) for day in days if history[day]}
    previous = {day: closes.get(prev) for prev, day in pairwise(days)}
    prior_close = closes[days[-1]] if days else None
    current = _shape(today_day, today, prior_close, minute)
    past: list[tuple[SessionShape, list[Candle]]] = []
    for day in days:
        shape = _shape(day, history[day], previous.get(day), minute)
        if shape is not None and len(history[day]) > minute:
            past.append((shape, history[day]))
    if current is None or len(past) < 5:
        return AnalogReport(sample=len(past), minute=minute, up_rate=None, median_rest_of_day=None,
                            median_mfe=None, median_mae=None, mean_distance=None,
                            quality="insufficient", days=())
    vectors = [_vector(shape) for shape, _ in past]
    scales = [
        (sum((v[i] - sum(x[i] for x in vectors) / len(vectors)) ** 2 for v in vectors)
         / len(vectors)) ** 0.5 or 1.0
        for i in range(len(vectors[0]))
    ]
    target = _vector(current)
    ranked = sorted(
        (
            (_distance(vector, target, scales), shape, bars)
            for vector, (shape, bars) in zip(vectors, past, strict=True)
        ),
        key=lambda item: item[0],
    )[:NEIGHBOURS]
    rest, mfe, mae = [], [], []
    for _, _, bars in ranked:
        entry = float(bars[minute - 1].close)
        after = bars[minute:]
        rest.append(float(after[-1].close) / entry - 1)
        mfe.append(max(float(bar.high) for bar in after) / entry - 1)
        mae.append(min(float(bar.low) for bar in after) / entry - 1)
    sample = len(ranked)

    def dec(value: float) -> Decimal:
        return Decimal(str(round(value, 6)))

    return AnalogReport(
        sample=sample,
        minute=minute,
        up_rate=dec(sum(1 for value in rest if value > 0) / sample),
        median_rest_of_day=dec(median(rest)),
        median_mfe=dec(median(mfe)),
        median_mae=dec(median(mae)),
        mean_distance=dec(sum(item[0] for item in ranked) / sample),
        quality="ok" if sample >= MIN_SAMPLE else "low",
        days=tuple(shape.day.isoformat() for _, shape, _ in ranked[:5]),
    )


def event_reactions(
    events: Sequence[MacroEvent], candles: Sequence[Candle], *, as_of: datetime,
    horizon: timedelta = timedelta(minutes=30),
) -> list[EventReaction]:
    """QQQ's move in the ``horizon`` after each past HIGH/MEDIUM event, by type."""

    ordered = sorted(candles, key=lambda item: item.event_time)
    times = [candle.event_time for candle in ordered]
    moves: dict[str, list[float]] = defaultdict(list)
    for event in events:
        end = event.scheduled_at + horizon
        if not event.time_known or end > as_of or event.importance == "LOW":
            continue
        start_index = next((i for i, t in enumerate(times) if t >= event.scheduled_at), None)
        end_index = next((i for i, t in enumerate(times) if t >= end), None)
        if start_index is None or end_index is None or start_index == 0:
            continue
        if times[end_index] - end > timedelta(minutes=5):
            continue  # event outside the regular session (e.g. 08:30 before the open)
        before = float(ordered[start_index - 1].close)
        moves[event.event_type].append(float(ordered[end_index].close) / before - 1)
    reactions: list[EventReaction] = []
    for event_type, values in sorted(moves.items()):
        reactions.append(
            EventReaction(
                event_type=event_type,
                samples=len(values),
                up_rate=Decimal(str(round(sum(1 for v in values if v > 0) / len(values), 4))),
                median_abs_move=Decimal(str(round(median(abs(v) for v in values), 6))),
                median_move=Decimal(str(round(median(values), 6))),
                small_sample=len(values) < 8,
            )
        )
    return reactions
