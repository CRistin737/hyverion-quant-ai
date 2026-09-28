from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from trading_bot.config import ConfigManager, ConfigPatch, load_settings
from trading_bot.config.models import StrategiesConfig


def test_config_manager_validates_safe_public_patch_and_writes_backup(tmp_path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    initial = ConfigPatch(
        broker_provider="alpaca",
        primary_provider="openai",
        primary_auth_mode="subscription",
    )

    assert manager.validate_patch(initial, settings) == ()
    applied = manager.apply(initial, settings)
    assert applied.applied is True
    assert applied.requires_restart is True
    assert applied.backup_path is None

    second = manager.apply(ConfigPatch(model_analysis="claude-opus-5-5"), settings)
    assert second.backup_path is not None
    # A model change reaches the engine on its next cycle, without a restart.
    assert second.requires_restart is False
    assert manager.local_path.with_suffix(".yaml.bak").is_file()
    stored = yaml.safe_load(manager.local_path.read_text(encoding="utf-8"))
    assert "capital" not in stored.get("trading", {})
    assert stored["broker"]["provider"] == "alpaca"
    assert stored["ai"]["primary_provider"] == "openai"
    assert stored["ai"]["models"] == {"analysis": "claude-opus-5-5"}
    assert "api_key" not in manager.local_path.read_text(encoding="utf-8").lower()


def test_config_manager_rejects_unsupported_broker_and_risk_relationship(tmp_path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    with pytest.raises(ValueError):
        ConfigPatch.model_validate({"broker_provider": "kraken"})

    risk_errors = manager.validate_patch(
        ConfigPatch(daily_loss_percent="3", weekly_loss_percent="2"), settings
    )
    assert "daily loss limit cannot exceed weekly loss limit" in risk_errors
    # The autonomy envelope is a hard rail: 2 % per trade is outside it.
    envelope_errors = manager.validate_patch(ConfigPatch(base_risk_percent="2"), settings)
    assert any("base_risk_percent" in error for error in envelope_errors)
    # The AI budget in USD is gone (subscriptions have 5 h / weekly limits).
    with pytest.raises(ValueError):
        ConfigPatch.model_validate({"max_ai_cost_usd": "2"})
    # The internal simulator is never an owner-facing broker choice.
    with pytest.raises(ValueError):
        ConfigPatch.model_validate({"broker_provider": "simulator"})
    # Configured capital is gone: equity comes from the broker account.
    with pytest.raises(ValueError):
        ConfigPatch.model_validate({"capital_usd": "100"})


def test_config_manager_only_accepts_allowlisted_reviewed_feed_urls(tmp_path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    valid = ConfigPatch(
        news_provider="rss",
        news_feeds={"sec-press-releases": "https://example.test/sec.xml"},
        news_reviewed_sources=("sec-press-releases",),
    )
    assert manager.validate_patch(valid, settings) == ()
    applied = manager.apply(valid, settings)
    assert applied.applied is True
    stored = yaml.safe_load(manager.local_path.read_text(encoding="utf-8"))
    assert stored["external_data"]["news_provider"] == "rss"
    assert stored["external_data"]["news_feeds"]["sec-press-releases"] == (
        "https://example.test/sec.xml"
    )
    assert stored["external_data"]["news_reviewed_sources"] == ["sec-press-releases"]

    errors = manager.validate_patch(
        ConfigPatch(news_feeds={"unreviewed": "https://example.test/feed"}), settings
    )
    assert any("news feed is not allowlisted" in error for error in errors)

    malformed = manager.validate_patch(
        ConfigPatch(
            news_feeds={"sec-press-releases": "not a URL"},
            news_reviewed_sources=("sec-press-releases",),
        ),
        settings,
    )
    assert "news feed URL is invalid: sec-press-releases" in malformed


def test_config_manager_keeps_one_primary_and_ordered_fallback_subscriptions(tmp_path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    patch = ConfigPatch(
        primary_provider="openai",
        primary_auth_mode="subscription",
        fallback_providers=("anthropic", "xai", "gemini"),
    )

    assert manager.validate_patch(patch, settings) == ()
    applied = manager.apply(patch, settings)
    assert applied.applied is True
    stored = yaml.safe_load(manager.local_path.read_text(encoding="utf-8"))
    assert stored["ai"]["fallback_providers"] == ["anthropic", "xai", "gemini"]

    errors = manager.validate_patch(
        ConfigPatch(primary_provider="openai", fallback_providers=("openai",)),
        settings,
    )
    assert "primary provider cannot also be a fallback provider" in errors

    disabled_errors = manager.validate_patch(
        ConfigPatch(primary_provider="disabled", fallback_providers=("gemini",)),
        settings,
    )
    assert "fallback providers require one configured primary provider" in disabled_errors


def test_config_manager_validates_external_collection_backoff(tmp_path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    errors = manager.validate_patch(
        ConfigPatch(collection_interval_seconds=300, collection_max_backoff_seconds=60),
        settings,
    )
    assert "collection backoff cannot be shorter than its interval" in errors
    valid = ConfigPatch(collection_interval_seconds=120, collection_max_backoff_seconds=600)
    assert manager.validate_patch(valid, settings) == ()
    result = manager.apply(valid, settings)
    assert result.applied is True
    stored = yaml.safe_load(manager.local_path.read_text(encoding="utf-8"))
    assert stored["external_data"]["collection_interval_seconds"] == 120
    assert stored["external_data"]["collection_max_backoff_seconds"] == 600


def test_strategy_config_rejects_unknown_plugins_but_accepts_verified_plugins() -> None:
    with pytest.raises(ValueError, match="strategy plugin is not available"):
        StrategiesConfig(
            enabled=("trend_momentum", "unknown_plugin"),
            signal_weights_version="v1",
            signal_weights={"technical": 1},
        )

    settings = load_settings()
    assert settings.public.strategies.enabled == ("trend_pullback",)


def test_frozen_loader_reads_mutable_desktop_override(tmp_path, monkeypatch) -> None:
    support_config = tmp_path / "Library" / "Application Support" / "Hyverion Quant AI" / "config"
    support_config.mkdir(parents=True)
    (support_config / "local.yaml").write_text(
        "trading:\n  capital: '123'\n  operating_region: PR\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "_MEIPASS", str(Path.cwd()), raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    loaded = load_settings()
    # The retired capital key is ignored; the rest of the override applies.
    assert loaded.public.trading.operating_region == "PR"
    assert not hasattr(loaded.public.trading, "capital")
    support = tmp_path / "Library" / "Application Support" / "Hyverion Quant AI"
    # A bundle runs with cwd "/"; every mutable path must live under Application Support.
    assert loaded.public.memory.vault_path == support / "knowledge"
    assert loaded.public.memory.embedding_model_dir == support / "models" / "all-MiniLM-L6-v2"
    assert loaded.public.database.parquet_root == support / "parquet"


def test_operating_region_and_hold_time_are_validated_and_persisted(tmp_path) -> None:
    import pytest as _pytest
    from pydantic import ValidationError

    from trading_bot.config import load_settings
    from trading_bot.config.manager import ConfigManager, ConfigPatch

    assert ConfigPatch(operating_region=" do ").operating_region == "DO"
    for bad in ("DOM", "1A", "", "ñx"):
        with _pytest.raises(ValidationError):
            ConfigPatch(operating_region=bad)
    with _pytest.raises(ValidationError):
        ConfigPatch(max_position_hold_minutes=0)
    with _pytest.raises(ValidationError):
        ConfigPatch(max_position_hold_minutes=1441)
    manager = ConfigManager(tmp_path)
    manager.apply(
        ConfigPatch(operating_region="ES", max_position_hold_minutes=90), load_settings()
    )
    text = (tmp_path / "local.yaml").read_text()
    assert "operating_region: ES" in text
    assert "max_position_hold_minutes: 90" in text


def test_venue_region_readiness_check() -> None:
    from trading_bot.config import load_settings
    from trading_bot.monitoring.readiness import _venue_region_check

    settings = load_settings()

    def with_region(region: str | None):
        trading = settings.public.trading.model_copy(update={"operating_region": region})
        public = settings.public.model_copy(update={"trading": trading})
        return _venue_region_check(settings.model_copy(update={"public": public}))

    assert with_region(None).status == "INFO"
    # Region is declared, never assumed eligible; LIVE review verifies the broker.
    assert with_region("US").status == "PASS"
    assert "verify" in with_region("DO").detail


def test_risk_profile_writes_its_values_and_manual_edit_is_personalizado(tmp_path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    manager.apply(ConfigPatch(risk_profile="alto"), settings)
    stored = yaml.safe_load(manager.local_path.read_text(encoding="utf-8"))["risk"]
    assert stored["profile"] == "alto"
    assert stored["base_risk_percent"] == "1"
    assert stored["weekly_loss_percent"] == "8"

    manager.apply(ConfigPatch(base_risk_percent="0.4"), settings)
    stored = yaml.safe_load(manager.local_path.read_text(encoding="utf-8"))["risk"]
    assert stored["profile"] == "personalizado"
    assert stored["base_risk_percent"] == "0.4"


def test_legacy_dollar_defaults_give_way_to_the_profile_but_owner_values_stay() -> None:
    from trading_bot.config.migrate import drop_ignored_keys

    legacy = {
        "ai": {"primary_provider": "anthropic", "max_daily_cost_usd": "500"},
        "risk": {
            "base_risk_percent": "0.25",
            "max_base_risk_usd": "10",
            "daily_loss_hard_cap_usd": "25",
            "weekly_loss_hard_cap_usd": "80",
            "max_profit_giveback_percent": "35",
        },
    }
    cleaned = drop_ignored_keys(legacy)
    assert cleaned["ai"] == {"primary_provider": "anthropic"}
    # 80 is not the old default: it was the owner's choice and is kept.
    assert cleaned["risk"] == {
        "weekly_loss_hard_cap_usd": "80",
        "max_profit_giveback_percent": "35",
    }


def test_live_ai_models_follow_the_saved_file(tmp_path) -> None:
    from trading_bot.config.loader import live_ai_models

    settings = load_settings()
    manager = ConfigManager(tmp_path)
    manager.apply(ConfigPatch(model_decision="claude-sonnet-5", model_analysis="default"), settings)
    models = live_ai_models(settings, manager.local_path)
    assert models.decision == "claude-sonnet-5"
    assert models.analysis is None  # CLI default
    assert models.improvement == "claude-opus-5-5"
    assert manager.validate_patch(ConfigPatch(model_decision="rm -rf /"), settings)
