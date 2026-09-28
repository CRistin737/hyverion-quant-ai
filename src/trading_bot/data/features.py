"""Deterministic features. No LLM ever computes these (§93 of the QQQ plan).

Price-only features need five closes. Session features (VWAP anchored at the
09:30 New York open, ATR, RSI, EMAs, distance from VWAP in ATR units) need
candles and are ``None`` when there is not enough data, never guessed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import time
from decimal import Decimal
from itertools import pairwise

from pydantic import Field

from trading_bot.market.clock import NEW_YORK, trading_day
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import Candle

ATR_PERIOD = 14
RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
REGULAR_OPEN = time(9, 30)


class FeatureSet(StrictSchema):
    symbol: str
    prices: tuple[Decimal, ...] = Field(min_length=5)
    simple_return_percent: Decimal
    fast_sma: Decimal = Field(gt=0)
    slow_sma: Decimal = Field(gt=0)
    realized_volatility_percent: Decimal = Field(ge=0)
    momentum_score: Decimal = Field(ge=0, le=100)
    trend_score: Decimal = Field(ge=0, le=100)
    # Session features (None when candles are missing or too few).
    session_vwap: Decimal | None = None
    atr: Decimal | None = None
    rsi: Decimal | None = Field(default=None, ge=0, le=100)
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    vwap_distance_atr: Decimal | None = None
    session_bars: int = Field(default=0, ge=0)
    minutes_since_open: int | None = None
    # First 30 minutes of the session (known only once 10:00 ET has passed).
    opening_range_high: Decimal | None = None
    opening_range_low: Decimal | None = None
    # Last bar volume relative to the session's average bar so far.
    relative_volume: Decimal | None = None


class FeatureEngine:
    def compute(
        self,
        symbol: str,
        prices: tuple[Decimal, ...],
        candles: Sequence[Candle] = (),
    ) -> FeatureSet:
        if len(prices) < 5 or any(price <= 0 for price in prices):
            raise ValueError("at least five positive prices are required")
        fast_window = prices[-3:]
        slow_window = prices[-5:]
        fast_sma = sum(fast_window, Decimal("0")) / Decimal(len(fast_window))
        slow_sma = sum(slow_window, Decimal("0")) / Decimal(len(slow_window))
        simple_return = (prices[-1] / prices[0] - Decimal("1")) * Decimal("100")
        returns = [
            (current / previous - Decimal("1")) * Decimal("100")
            for previous, current in pairwise(prices)
        ]
        mean_return = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum(((item - mean_return) ** 2 for item in returns), Decimal("0")) / Decimal(
            len(returns)
        )
        volatility = variance.sqrt()
        momentum = _bounded_score(Decimal("50") + simple_return * Decimal("8"))
        sma_gap_percent = (fast_sma / slow_sma - Decimal("1")) * Decimal("100")
        trend = _bounded_score(Decimal("50") + sma_gap_percent * Decimal("20"))
        bars = sorted((c for c in candles if c.symbol == symbol), key=lambda c: c.event_time)
        closes = [bar.close for bar in bars]
        session = session_bars(bars)
        vwap = session_vwap(session)
        # Smoothed indicators converge within a few periods; bounded windows keep
        # bar-by-bar replays linear instead of quadratic.
        atr = average_true_range(bars[-ATR_PERIOD * 6 :])
        distance = None
        if vwap is not None and atr is not None and atr > 0:
            distance = (prices[-1] - vwap) / atr
        opening_high, opening_low, since_open = opening_range(session)
        return FeatureSet(
            symbol=symbol,
            prices=prices,
            simple_return_percent=simple_return,
            fast_sma=fast_sma,
            slow_sma=slow_sma,
            realized_volatility_percent=volatility,
            momentum_score=momentum,
            trend_score=trend,
            session_vwap=vwap,
            atr=atr,
            rsi=relative_strength_index(closes[-RSI_PERIOD * 6 :]),
            ema_fast=exponential_average(closes[-EMA_FAST * 6 :], EMA_FAST),
            ema_slow=exponential_average(closes[-EMA_SLOW * 6 :], EMA_SLOW),
            vwap_distance_atr=distance,
            session_bars=len(session),
            minutes_since_open=since_open,
            opening_range_high=opening_high,
            opening_range_low=opening_low,
            relative_volume=relative_volume(session),
        )


def session_bars(bars: Sequence[Candle]) -> list[Candle]:
    """Bars of the latest New York trading day that closed after the 09:30 open."""

    if not bars:
        return []
    day = trading_day(bars[-1].event_time)
    return [
        bar
        for bar in bars
        if trading_day(bar.event_time) == day
        and bar.event_time.astimezone(NEW_YORK).time() > REGULAR_OPEN
    ]


OPENING_RANGE_MINUTES = 30


def _minutes_after_open(bar: Candle) -> int:
    local = bar.event_time.astimezone(NEW_YORK)
    opened = local.replace(hour=REGULAR_OPEN.hour, minute=REGULAR_OPEN.minute, second=0,
                           microsecond=0)
    return int((local - opened).total_seconds() // 60)


def opening_range(
    session: Sequence[Candle],
) -> tuple[Decimal | None, Decimal | None, int | None]:
    """High/low of the first 30 minutes, and minutes since the open at the last bar."""

    if not session:
        return None, None, None
    since_open = _minutes_after_open(session[-1])
    if since_open < OPENING_RANGE_MINUTES:
        return None, None, since_open
    window = [bar for bar in session if _minutes_after_open(bar) <= OPENING_RANGE_MINUTES]
    if len(window) < OPENING_RANGE_MINUTES // 2:  # too many missing bars to trust it
        return None, None, since_open
    return max(bar.high for bar in window), min(bar.low for bar in window), since_open


def relative_volume(session: Sequence[Candle]) -> Decimal | None:
    if len(session) < 6:
        return None
    previous = [bar.volume for bar in session[:-1]]
    average = sum(previous, Decimal("0")) / len(previous)
    return session[-1].volume / average if average > 0 else None


def session_vwap(bars: Sequence[Candle]) -> Decimal | None:
    volume = sum((bar.volume for bar in bars), Decimal("0"))
    if not bars or volume <= 0:
        return None
    weighted = sum(
        (bar.volume * (bar.vwap or (bar.high + bar.low + bar.close) / 3) for bar in bars),
        Decimal("0"),
    )
    return weighted / volume


def average_true_range(bars: Sequence[Candle], period: int = ATR_PERIOD) -> Decimal | None:
    if len(bars) < period + 1:
        return None
    ranges = [
        max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        for prev, bar in pairwise(bars)
    ]
    atr = sum(ranges[:period], Decimal("0")) / period
    for value in ranges[period:]:  # Wilder smoothing
        atr = (atr * (period - 1) + value) / period
    return atr


def relative_strength_index(closes: Sequence[Decimal], period: int = RSI_PERIOD) -> Decimal | None:
    if len(closes) < period + 1:
        return None
    changes = [current - previous for previous, current in pairwise(closes)]
    gains = [max(change, Decimal("0")) for change in changes]
    losses = [max(-change, Decimal("0")) for change in changes]
    avg_gain = sum(gains[:period], Decimal("0")) / period
    avg_loss = sum(losses[:period], Decimal("0")) / period
    for gain, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return Decimal("100") if avg_gain > 0 else Decimal("50")
    rs = avg_gain / avg_loss
    return Decimal("100") - Decimal("100") / (1 + rs)


def exponential_average(values: Sequence[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    alpha = Decimal("2") / Decimal(period + 1)
    ema = sum(values[:period], Decimal("0")) / period
    for value in values[period:]:
        ema = alpha * value + (1 - alpha) * ema
    return ema


def _bounded_score(value: Decimal) -> Decimal:
    return min(Decimal("100"), max(Decimal("0"), value))
