"""Validated, non-secret configuration drafts for the native terminal.

The manager only writes ``config/local.yaml``.  Credentials are deliberately
outside this path and belong to the configured :class:`SecretStore`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import Field, field_validator

from trading_bot.config.loader import app_support_root, shares_app_state
from trading_bot.config.migrate import drop_ignored_keys, migrate_mapping
from trading_bot.config.models import (
    AI_ROLES,
    AIModelsConfig,
    PublicSettings,
    Settings,
    risk_envelope_values,
)
from trading_bot.config.risk_profiles import PROFILE_FIELDS, RISK_PROFILES
from trading_bot.data.sources import NEWS_ALLOWLIST
from trading_bot.providers.capabilities import (
    API_PROVIDER_IDS,
    SUBSCRIPTION_PROVIDER_IDS,
    SUPPORTED_PROVIDER_IDS,
)
from trading_bot.schemas.common import StrictSchema


class ConfigPatch(StrictSchema):
    """Safe public settings that may be edited from the native UI."""

    # The owner's paper broker. Equity comes from its account; the internal
    # simulator is not an owner-facing choice.
    broker_provider: Literal["alpaca"] | None = None
    primary_provider: str | None = None
    primary_auth_mode: str | None = None
    # Model per role for the primary subscription ("default" = CLI default).
    # Applied by the engine on its next cycle, without a restart.
    model_analysis: str | None = None
    model_decision: str | None = None
    model_improvement: str | None = None
    fallback_providers: tuple[str, ...] | None = None
    news_provider: str | None = None
    news_feeds: dict[str, str] | None = None
    news_reviewed_sources: tuple[str, ...] | None = None
    collection_interval_seconds: int | None = Field(default=None, ge=15, le=86400)
    collection_max_backoff_seconds: int | None = Field(default=None, ge=60, le=604800)
    social_provider: str | None = None
    # Picking a profile writes its four percentages; editing one of those by
    # hand switches the profile to "personalizado". All inside the envelope.
    risk_profile: Literal["conservador", "medio", "alto"] | None = None
    base_risk_percent: Decimal | None = Field(default=None, gt=0, le=100)
    daily_loss_percent: Decimal | None = Field(default=None, gt=0, le=100)
    weekly_loss_percent: Decimal | None = Field(default=None, gt=0, le=100)
    max_account_drawdown_percent: Decimal | None = Field(default=None, gt=0, le=100)
    max_profit_giveback_percent: Decimal | None = Field(default=None, ge=0, le=100)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=20)
    opening_no_trade_minutes: int | None = Field(default=None, ge=0, le=60)
    eod_flatten_minutes_before_close: int | None = Field(default=None, ge=1, le=120)
    max_position_hold_minutes: int | None = Field(default=None, ge=1, le=1440)
    operating_region: str | None = None

    @field_validator("operating_region")
    @classmethod
    def validate_region(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        # ISO 3166-1 alpha-2; used to check that the venue serves the operator's country.
        if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("operating_region must be a two-letter ISO country code")
        return normalized

    @field_validator("primary_auth_mode")
    @classmethod
    def validate_auth_mode(cls, value: str | None) -> str | None:
        if value is not None and value not in {"api", "subscription"}:
            raise ValueError("authentication mode must be api or subscription")
        return value

    @field_validator("fallback_providers")
    @classmethod
    def normalize_fallback_providers(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized = tuple(item.strip().lower() for item in value if item.strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError("fallback providers must be unique and ordered")
        return normalized


class ConfigApplyResult(StrictSchema):
    applied: bool
    requires_restart: bool
    changed_fields: tuple[str, ...]
    backup_path: str | None = None
    detail: str


class ConfigManager:
    """Atomically persist a validated public configuration patch."""

    def __init__(self, config_dir: Path | str | None = None) -> None:
        self.config_dir = self._resolve_config_dir(config_dir)
        self.local_path = self.config_dir / "local.yaml"

    @staticmethod
    def _resolve_config_dir(config_dir: Path | str | None) -> Path:
        if config_dir is not None:
            return Path(config_dir)
        if shares_app_state():
            return app_support_root() / "config"
        return Path("config")

    def validate_patch(self, patch: ConfigPatch, current: Settings) -> tuple[str, ...]:
        errors: list[str] = []
        if patch.primary_provider is not None and not patch.primary_provider.strip():
            errors.append("primary_provider cannot be empty")
        if patch.primary_provider is not None and patch.primary_provider not in {
            "disabled",
            "anthropic",
            "openai",
            "xai",
            "gemini",
        }:
            errors.append("primary_provider is not supported by this release")
        final_primary = (
            patch.primary_provider.strip().lower()
            if patch.primary_provider is not None
            else current.public.ai.primary_provider
        )
        final_auth_mode = (
            patch.primary_auth_mode
            if patch.primary_auth_mode is not None
            else current.public.ai.primary_auth_mode
        )
        final_fallbacks = (
            patch.fallback_providers
            if patch.fallback_providers is not None
            else current.public.ai.fallback_providers
        )
        unknown_fallbacks = set(final_fallbacks).difference(SUPPORTED_PROVIDER_IDS)
        if unknown_fallbacks:
            errors.append(f"fallback provider is not supported: {sorted(unknown_fallbacks)[0]}")
        if final_primary == "disabled" and final_fallbacks:
            errors.append("fallback providers require one configured primary provider")
        if final_primary in final_fallbacks:
            errors.append("primary provider cannot also be a fallback provider")
        configured_chain = final_fallbacks + (
            (final_primary,) if final_primary != "disabled" else ()
        )
        if final_auth_mode == "subscription":
            unsupported_subscription = set(configured_chain).difference(SUBSCRIPTION_PROVIDER_IDS)
            if unsupported_subscription:
                errors.append(
                    "subscription mode only accepts providers with official subscription login: "
                    + sorted(unsupported_subscription)[0]
                )
        elif final_auth_mode == "api":
            unsupported_api = set(configured_chain).difference(API_PROVIDER_IDS)
            if unsupported_api:
                errors.append(f"API mode provider is not supported: {sorted(unsupported_api)[0]}")
        for role in AI_ROLES:
            value = getattr(patch, f"model_{role}")
            if value is not None:
                try:
                    AIModelsConfig.normalize_model(value)
                except ValueError:
                    errors.append(f"invalid model name for {role}")
        final_feeds = (
            patch.news_feeds
            if patch.news_feeds is not None
            else current.public.external_data.news_feeds
        )
        final_reviewed = (
            patch.news_reviewed_sources
            if patch.news_reviewed_sources is not None
            else current.public.external_data.news_reviewed_sources
        )
        if any(source_id not in final_reviewed for source_id in final_feeds):
            errors.append("every news feed must be explicitly reviewed")
        if patch.news_feeds is not None:
            for source_id, url in patch.news_feeds.items():
                if source_id not in NEWS_ALLOWLIST:
                    errors.append(f"news feed is not allowlisted: {source_id}")
                    continue
                try:
                    parsed = httpx.URL(url)
                except (httpx.InvalidURL, TypeError, ValueError):
                    errors.append(f"news feed URL is invalid: {source_id}")
                    continue
                if parsed.scheme not in {"http", "https"} or not parsed.host:
                    errors.append(f"news feed URL is invalid: {source_id}")
        if patch.news_reviewed_sources is not None:
            unknown = set(patch.news_reviewed_sources).difference(NEWS_ALLOWLIST)
            if unknown:
                errors.append(f"news source is not allowlisted: {sorted(unknown)[0]}")
        collection_interval = (
            patch.collection_interval_seconds
            if patch.collection_interval_seconds is not None
            else current.public.external_data.collection_interval_seconds
        )
        collection_backoff = (
            patch.collection_max_backoff_seconds
            if patch.collection_max_backoff_seconds is not None
            else current.public.external_data.collection_max_backoff_seconds
        )
        if collection_backoff < collection_interval:
            errors.append("collection backoff cannot be shorter than its interval")
        risk = _patched_risk(patch, current)
        if risk["daily_loss_percent"] > risk["weekly_loss_percent"]:
            errors.append("daily loss limit cannot exceed weekly loss limit")
        if risk["base_risk_percent"] > risk["daily_loss_percent"]:
            errors.append("risk per trade cannot exceed the daily loss limit")
        errors.extend(current.public.autonomy.violations(risk))
        return tuple(errors)

    def apply(self, patch: ConfigPatch, current: Settings) -> ConfigApplyResult:
        errors = self.validate_patch(patch, current)
        if errors:
            raise ValueError("; ".join(errors))
        changes = patch.model_dump(exclude_none=True)
        profile = changes.pop("risk_profile", None)
        if profile is not None:
            preset = RISK_PROFILES[profile].as_values()
            # A profile plus a different hand-typed value is a custom setting.
            custom = any(
                key in changes and Decimal(str(changes[key])) != value
                for key, value in preset.items()
            )
            changes = {
                **preset,
                **changes,
                "risk_profile": "personalizado" if custom else profile,
            }
        elif PROFILE_FIELDS.intersection(changes):
            changes["risk_profile"] = "personalizado"
        if not changes:
            return ConfigApplyResult(
                applied=False,
                requires_restart=False,
                changed_fields=(),
                detail="No configuration changes were supplied.",
            )
        local: dict[str, Any] = {}
        if self.local_path.exists():
            loaded = yaml.safe_load(self.local_path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError("config/local.yaml must contain a mapping")
            local, _ = migrate_mapping(loaded)
            local = drop_ignored_keys(local)
        mapping = self._to_local_mapping(changes)
        # The whole resulting configuration must be valid (envelope, LIVE caps,
        # cross-field rules) before anything is written: the engine must never
        # find an invalid file on disk because of the app.
        candidate = current.public.model_dump(mode="json")
        _deep_merge(candidate, mapping)
        try:
            PublicSettings.model_validate(candidate)
        except ValueError as exc:
            raise ValueError(f"configuration would be invalid: {exc}") from exc
        _deep_merge(local, mapping)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        backup_path: str | None = None
        if self.local_path.exists():
            backup = self.local_path.with_suffix(".yaml.bak")
            shutil.copy2(self.local_path, backup)
            backup_path = str(backup)
        descriptor, temp_path = tempfile.mkstemp(
            prefix="local.", suffix=".yaml", dir=self.config_dir
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                yaml.safe_dump(local, handle, sort_keys=False)
            os.replace(temp_path, self.local_path)
            os.chmod(self.local_path, 0o600)
            if backup_path is not None:
                os.chmod(backup_path, 0o600)
        except Exception:
            Path(temp_path).unlink(missing_ok=True)
            raise
        live_only = all(key.startswith("model_") for key in changes)
        return ConfigApplyResult(
            applied=True,
            requires_restart=not live_only,
            changed_fields=tuple(sorted(changes)),
            backup_path=backup_path,
            detail=(
                "Model saved. The engine uses it from its next cycle."
                if live_only
                else "Configuration saved. Restart the engine to apply it everywhere."
            ),
        )

    @staticmethod
    def _to_local_mapping(changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
        trading: dict[str, Any] = {}
        broker: dict[str, Any] = {}
        ai: dict[str, Any] = {}
        external_data: dict[str, Any] = {}
        risk: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "operating_region":
                trading["operating_region"] = value
            elif key in RISK_INT_FIELDS:
                risk[key] = value
            elif key == "risk_profile":
                risk["profile"] = value
            elif key == "broker_provider":
                broker["provider"] = value
            elif key in {
                "primary_provider",
                "primary_auth_mode",
                "fallback_providers",
            }:
                ai[key] = value
            elif key.startswith("model_"):
                models = ai.setdefault("models", {})
                models[key.removeprefix("model_")] = None if value == "default" else value
            elif key in {"news_provider", "social_provider"}:
                external_data[key] = value
            elif key == "news_feeds":
                external_data[key] = value
            elif key == "news_reviewed_sources":
                external_data[key] = list(value)
            elif key in {"collection_interval_seconds", "collection_max_backoff_seconds"}:
                external_data[key] = value
            elif key in RISK_DECIMAL_FIELDS:
                risk[key] = str(value)
        result: dict[str, dict[str, Any]] = {}
        for name, section in (
            ("trading", trading),
            ("broker", broker),
            ("ai", ai),
            ("external_data", external_data),
            ("risk", risk),
        ):
            if section:
                result[name] = section
        return result


RISK_DECIMAL_FIELDS = frozenset(
    {
        "base_risk_percent",
        "daily_loss_percent",
        "weekly_loss_percent",
        "max_account_drawdown_percent",
        "max_profit_giveback_percent",
    }
)
RISK_INT_FIELDS = frozenset(
    {
        "max_trades_per_day",
        "opening_no_trade_minutes",
        "eod_flatten_minutes_before_close",
        "max_position_hold_minutes",
    }
)


def _patched_risk(patch: ConfigPatch, current: Settings) -> dict[str, Decimal]:
    """The envelope-relevant risk values after applying ``patch``."""

    values = risk_envelope_values(current.public.risk)
    if patch.risk_profile is not None:
        values.update(RISK_PROFILES[patch.risk_profile].as_values())
    for key in RISK_DECIMAL_FIELDS | RISK_INT_FIELDS:
        value = getattr(patch, key)
        if value is not None and key in values:
            values[key] = Decimal(value)
    return values


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value
