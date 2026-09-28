"""Volatility and options context for QQQ (§24-§26). A sensor, never an order.

Realized and implied volatility are kept apart on purpose (§24): realized comes
from QQQ's own bars, implied from the option chain (Alpaca ``indicative``
feed) and the VIX close (FRED). Without option data the context says
``unavailable`` and every field stays ``None`` — nothing is invented (§26).

There is deliberately no "dealer gamma": only a plain
summary of what the chain directly shows (§25).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from itertools import pairwise

from trading_bot.market_data.base import OptionQuote
from trading_bot.schemas.common import StrictSchema
from trading_bot.schemas.trading import Candle

MINUTES_PER_YEAR = 252 * 390


class VolatilityContext(StrictSchema):
    status: str  # "ok" | "partial" | "unavailable"
    realized_vol_annual: Decimal | None = None  # from 1-minute QQQ returns
    atm_iv: Decimal | None = None  # nearest expiry ≥ 1 day
    next_atm_iv: Decimal | None = None
    term_slope: Decimal | None = None  # next - near (negative = stress, inverted)
    skew_25d: Decimal | None = None  # put IV(25Δ) - call IV(25Δ)
    expected_move_pct: Decimal | None = None  # ATM straddle / spot, near expiry
    near_expiry: str | None = None
    vix_close: Decimal | None = None
    iv_minus_realized: Decimal | None = None
    contracts: int = 0


def realized_volatility(candles: Sequence[Candle]) -> Decimal | None:
    closes = [float(candle.close) for candle in candles if candle.close > 0]
    if len(closes) < 20:
        return None
    returns = [math.log(b / a) for a, b in pairwise(closes)]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return Decimal(str(round(math.sqrt(variance * MINUTES_PER_YEAR), 6)))


def _mid(quote: OptionQuote) -> Decimal | None:
    if quote.bid is None or quote.ask is None or quote.ask <= 0 or quote.bid < 0:
        return None
    return (quote.bid + quote.ask) / 2


def _atm(chain: Sequence[OptionQuote], spot: Decimal, kind: str) -> OptionQuote | None:
    candidates = [q for q in chain if q.option_type == kind]
    return min(candidates, key=lambda q: abs(q.strike - spot), default=None)


def _by_delta(chain: Sequence[OptionQuote], kind: str, target: Decimal) -> OptionQuote | None:
    candidates = [
        q for q in chain if q.option_type == kind and q.delta is not None and q.implied_volatility
    ]
    return min(candidates, key=lambda q: abs(q.delta - target), default=None)  # type: ignore[operator]


def volatility_context(
    chain: Sequence[OptionQuote],
    *,
    spot: Decimal | None,
    today: date,
    candles: Sequence[Candle] = (),
    vix_close: Decimal | None = None,
) -> VolatilityContext:
    realized = realized_volatility(candles)
    expiries = sorted({q.expiration for q in chain if date.fromisoformat(q.expiration) > today})
    if not chain or spot is None or spot <= 0 or not expiries:
        return VolatilityContext(
            status="partial" if realized or vix_close else "unavailable",
            realized_vol_annual=realized,
            vix_close=vix_close,
        )
    near = [q for q in chain if q.expiration == expiries[0]]
    atm_call, atm_put = _atm(near, spot, "call"), _atm(near, spot, "put")
    ivs = [q.implied_volatility for q in (atm_call, atm_put) if q and q.implied_volatility]
    atm_iv = sum(ivs, Decimal("0")) / len(ivs) if ivs else None
    next_iv = None
    if len(expiries) > 1:
        later = [q for q in chain if q.expiration == expiries[1]]
        pair = [_atm(later, spot, "call"), _atm(later, spot, "put")]
        later_ivs = [q.implied_volatility for q in pair if q and q.implied_volatility]
        next_iv = sum(later_ivs, Decimal("0")) / len(later_ivs) if later_ivs else None
    put25 = _by_delta(near, "put", Decimal("-0.25"))
    call25 = _by_delta(near, "call", Decimal("0.25"))
    skew = (
        put25.implied_volatility - call25.implied_volatility
        if put25 and call25 and put25.implied_volatility and call25.implied_volatility
        else None
    )
    straddle = None
    if atm_call and atm_put:
        call_mid, put_mid = _mid(atm_call), _mid(atm_put)
        if call_mid is not None and put_mid is not None:
            straddle = (call_mid + put_mid) / spot
    return VolatilityContext(
        status="ok" if atm_iv is not None else "partial",
        realized_vol_annual=realized,
        atm_iv=atm_iv,
        next_atm_iv=next_iv,
        term_slope=(next_iv - atm_iv) if next_iv is not None and atm_iv is not None else None,
        skew_25d=skew,
        expected_move_pct=straddle,
        near_expiry=expiries[0],
        vix_close=vix_close,
        iv_minus_realized=(atm_iv - realized) if atm_iv is not None and realized else None,
        contracts=len(chain),
    )
