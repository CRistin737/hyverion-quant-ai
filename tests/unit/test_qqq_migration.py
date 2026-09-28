"""QQQ migration invariants: one executable instrument, v1 configs migrate safely."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
import yaml
from conftest import make_context, make_critic, make_proposal
from pydantic import ValidationError

from trading_bot.config import load_settings
from trading_bot.config.migrate import drop_ignored_keys, load_local_config, migrate_mapping
from trading_bot.config.models import TradingConfig
from trading_bot.core.clock import FixedClock
from trading_bot.core.instruments import (
    EXECUTION_WHITELIST,
    SYMBOL_NOT_EXECUTION_WHITELISTED,
    is_executable,
    observation_universe,
)
from trading_bot.risk.engine import RiskEngine

V1_LOCAL = {
    "trading": {
        "mode": "paper",
        "market_type": "spot",
        "leverage_enabled": False,
        "capital": "100",
        "operating_region": "DO",
        "allowed_symbols": ["BTC/USDT", "ETH/USDT"],
    },
    "exchange": {"exchange_id": "binance", "sandbox": True},
    "ai": {"primary_provider": "anthropic", "max_daily_cost_usd": "500"},
    "external_data": {
        "news_provider": "rss",
        "derivatives_provider": "disabled",
        "news_feeds": {
            "coindesk-news": "https://example.test/c.xml",
            "sec-press-releases": "https://example.test/sec.xml",
        },
        "news_reviewed_sources": ["coindesk-news", "sec-press-releases"],
    },
    "risk": {
        "base_risk_percent": "0.25",
        "max_base_risk_usd": "10",
        "daily_loss_hard_cap_usd": "25",
        "weekly_loss_hard_cap_usd": "75",
        "max_profit_giveback_percent": "35",
    },
}


def test_qqq_is_the_only_executable_instrument() -> None:
    assert EXECUTION_WHITELIST == frozenset({"QQQ"})
    assert is_executable("QQQ")
    for symbol in ("SPY", "IWM", "SMH", "VIX", "NVDA", "BTC/USDT", ""):
        assert not is_executable(symbol)
    assert "QQQ" not in observation_universe()
    assert {"SPY", "IWM", "SMH", "VIX"} <= set(observation_universe())


@pytest.mark.parametrize("symbols", [("SPY",), ("QQQ", "NVDA"), ("BTC/USDT",)])
def test_config_rejects_non_whitelisted_symbols(symbols: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match=SYMBOL_NOT_EXECUTION_WHITELISTED):
        TradingConfig(allowed_symbols=symbols)


@pytest.mark.parametrize(
    "flag",
    ["shorting_enabled", "allow_overnight", "premarket_trading", "after_hours_trading",
     "options_trading"],
)
def test_unvalidated_trading_modes_cannot_be_enabled(flag: str) -> None:
    with pytest.raises(ValidationError):
        TradingConfig.model_validate({flag: True})


def test_risk_engine_denies_a_non_whitelisted_asset(now: datetime, risk_config) -> None:
    decision = RiskEngine(risk_config, FixedClock(now)).evaluate_entry(
        make_proposal(now, asset="NVDA"), make_critic(now, asset="NVDA"), make_context()
    )
    assert decision.verdict == "DENY"
    assert SYMBOL_NOT_EXECUTION_WHITELISTED in decision.reasons
    assert decision.approved_asset is None


def test_v1_crypto_config_migrates_without_touching_risk_limits() -> None:
    migrated, changes = migrate_mapping(V1_LOCAL)
    assert migrated["config_version"] == 2
    assert "exchange" not in migrated
    assert migrated["broker"] == {"provider": "alpaca", "environment": "paper"}
    assert migrated["trading"]["allowed_symbols"] == ["QQQ"]
    assert "market_type" not in migrated["trading"]
    assert "derivatives_provider" not in migrated["external_data"]
    assert migrated["external_data"]["news_feeds"] == {
        "sec-press-releases": "https://example.test/sec.xml"
    }
    assert migrated["external_data"]["news_reviewed_sources"] == ["sec-press-releases"]
    # Risk, region and AI are copied verbatim (§45: never retuned silently).
    assert migrated["risk"] == V1_LOCAL["risk"]
    # Capital is not migrated: equity comes from the broker account.
    assert "capital" not in migrated["trading"]
    assert migrated["ai"] == V1_LOCAL["ai"]
    assert changes and migrate_mapping(migrated) == (migrated, ())


def test_loader_migrates_local_file_once_and_keeps_a_backup(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    source = load_settings().public  # repo defaults
    local = config_dir / "local.yaml"
    local.write_text(yaml.safe_dump(V1_LOCAL), encoding="utf-8")

    migrated = load_local_config(local)

    assert migrated["config_version"] == 2
    backup = config_dir / "local.yaml.v1.bak"
    assert yaml.safe_load(backup.read_text()) == V1_LOCAL
    # Retired keys (the USD AI budget, the old dollar risk defaults) stay in the
    # file until the next write, and are ignored on every read.
    assert drop_ignored_keys(yaml.safe_load(local.read_text())) == migrated
    assert "max_daily_cost_usd" not in migrated["ai"]
    assert (local.stat().st_mode & 0o777) == 0o600
    # Idempotent: a second load changes nothing and keeps the original backup.
    assert load_local_config(local) == migrated
    assert yaml.safe_load(backup.read_text()) == V1_LOCAL
    assert source.risk.max_trades_per_day == 10
    assert source.risk.base_risk_percent == Decimal("0.5")
