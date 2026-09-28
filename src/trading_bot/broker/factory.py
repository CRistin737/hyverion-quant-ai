"""The single place that constructs the broker adapter ExecutionEngine holds."""

from __future__ import annotations

from trading_bot.broker.alpaca import AlpacaPaperBroker
from trading_bot.broker.base import BrokerUnavailable
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.security.credentials import alpaca_paper_credentials

Broker = SimulatedBroker | AlpacaPaperBroker


def build_broker(settings: Settings, clock: Clock, *, force_simulator: bool = False) -> Broker:
    config = settings.public.broker
    if config.environment != "paper":
        raise BrokerUnavailable("broker_paper_only", config.environment)
    if force_simulator or config.provider == "simulator":
        return SimulatedBroker(clock)
    credentials = alpaca_paper_credentials(settings.secrets)
    if credentials is None:
        raise BrokerUnavailable("broker_credentials_missing")
    return AlpacaPaperBroker(credentials, clock)
