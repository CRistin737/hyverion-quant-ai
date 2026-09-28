"""Instrument model and the execution whitelist.

Hyverion trades exactly one instrument, QQQ. Everything else it looks at (SPY,
IWM, SMH, VIX and, later, Nasdaq-100 components) is observation only: a sensor,
never an order. The whitelist is checked twice, as a RiskEngine deny reason and
again inside ExecutionEngine, so a bug that builds an NVDA order still cannot
reach a broker.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from trading_bot.schemas.common import StrictSchema

EXECUTION_WHITELIST: frozenset[str] = frozenset({"QQQ"})
SYMBOL_NOT_EXECUTION_WHITELISTED = "SYMBOL_NOT_EXECUTION_WHITELISTED"
MARKET_TIMEZONE = "America/New_York"


class AssetClass(StrEnum):
    ETF = "ETF"
    EQUITY = "EQUITY"
    INDEX = "INDEX"


class Instrument(StrictSchema):
    id: str = Field(min_length=1, max_length=40)
    symbol: str = Field(pattern=r"^[A-Z][A-Z.]{0,9}$")
    name: str
    asset_class: AssetClass
    currency: str = "USD"
    primary_exchange: str
    timezone: str = MARKET_TIMEZONE
    tradable: bool
    observation_only: bool
    fractional_possible: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


def _etf(symbol: str, name: str, exchange: str, *, tradable: bool = False) -> Instrument:
    return Instrument(
        id=f"us:{symbol}",
        symbol=symbol,
        name=name,
        asset_class=AssetClass.ETF,
        primary_exchange=exchange,
        tradable=tradable,
        observation_only=not tradable,
        fractional_possible=True,
    )


INSTRUMENTS: dict[str, Instrument] = {
    item.symbol: item
    for item in (
        _etf("QQQ", "Invesco QQQ Trust (Nasdaq-100)", "NASDAQ", tradable=True),
        _etf("SPY", "SPDR S&P 500 ETF", "NYSE Arca"),
        _etf("IWM", "iShares Russell 2000 ETF", "NYSE Arca"),
        _etf("SMH", "VanEck Semiconductor ETF", "NASDAQ"),
        Instrument(
            id="us:VIX",
            symbol="VIX",
            name="Cboe Volatility Index",
            asset_class=AssetClass.INDEX,
            primary_exchange="Cboe",
            tradable=False,
            observation_only=True,
            fractional_possible=False,
        ),
    )
}


def is_executable(symbol: str) -> bool:
    """True only for whitelisted, tradable instruments."""

    item = INSTRUMENTS.get(symbol)
    return symbol in EXECUTION_WHITELIST and item is not None and item.tradable


def observation_universe() -> tuple[str, ...]:
    return tuple(sorted(s for s, item in INSTRUMENTS.items() if item.observation_only))
