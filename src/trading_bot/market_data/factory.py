"""The single place that constructs market-data providers."""

from __future__ import annotations

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.market_data.alpaca import AlpacaMarketData
from trading_bot.market_data.base import EquityMarketDataProvider, MarketDataUnavailable
from trading_bot.market_data.fixture import FixtureMarketData
from trading_bot.security.credentials import AlpacaPaperCredentials, alpaca_paper_credentials


def build_market_data(
    settings: Settings,
    clock: Clock,
    *,
    credentials: AlpacaPaperCredentials | None = None,
) -> EquityMarketDataProvider:
    config = settings.public.market_data
    if config.provider == "fixture":
        return FixtureMarketData(clock)
    resolved = credentials or alpaca_paper_credentials(settings.secrets)
    if resolved is None:
        # Fail closed and say exactly what is missing; never fall back to fake prices.
        raise MarketDataUnavailable("market_data_credentials_missing")
    return AlpacaMarketData(resolved, clock, feed=config.feed)
