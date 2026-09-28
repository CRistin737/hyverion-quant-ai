from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from trading_bot.config.migrate import load_local_config
from trading_bot.config.models import AIModelsConfig, PublicSettings, SecretSettings, Settings


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(target)
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return loaded


def _environment_overrides() -> dict[str, Any]:
    mappings: dict[str, tuple[str, str, Any]] = {
        "APP_ENV": ("app", "environment", str),
        "LOG_LEVEL": ("app", "log_level", str),
        "DATABASE_URL": ("database", "url", str),
        "TRADING_MODE": ("trading", "mode", str),
        "PRIMARY_INSTRUMENT": ("trading", "primary_instrument", str),
        "LIVE_TRADING": ("trading", "live_trading", _as_bool),
        "SHADOW_TRADING": ("trading", "shadow_trading", _as_bool),
        "OPERATING_REGION": ("trading", "operating_region", str),
        "ALLOWED_SYMBOLS": ("trading", "allowed_symbols", _csv),
        "BROKER_PROVIDER": ("broker", "provider", str),
        "BROKER_ENV": ("broker", "environment", str),
        "MARKET_DATA_PROVIDER": ("market_data", "provider", str),
        "MARKET_DATA_FEED": ("market_data", "feed", str),
        "MAX_TRADES_PER_DAY": ("risk", "max_trades_per_day", int),
        "BASE_RISK_PERCENT": ("risk", "base_risk_percent", str),
        "MAX_BASE_RISK_USD": ("risk", "max_base_risk_usd", str),
        "DAILY_LOSS_LIMIT_USD": ("risk", "daily_loss_hard_cap_usd", str),
        "WEEKLY_LOSS_LIMIT_USD": ("risk", "weekly_loss_hard_cap_usd", str),
        "MAX_PROFIT_GIVEBACK_PERCENT": ("risk", "max_profit_giveback_percent", str),
        "AI_PRIMARY_PROVIDER": ("ai", "primary_provider", str),
        "AI_PRIMARY_AUTH_MODE": ("ai", "primary_auth_mode", str),
        "AI_FALLBACK_PROVIDERS": ("ai", "fallback_providers", _csv),
        "NEWS_PROVIDER": ("external_data", "news_provider", str),
        "SOCIAL_PROVIDER": ("external_data", "social_provider", str),
        "MEMORY_WORKING_BACKEND": ("memory", "working_backend", str),
        "MEMORY_ENABLED": ("memory", "enabled", _as_bool),
    }
    result: dict[str, Any] = {}
    for env_name, (section, key, parser) in mappings.items():
        raw = os.getenv(env_name)
        if raw is None or raw == "":
            continue
        result.setdefault(section, {})[key] = parser(raw)
    return result


def _as_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"expected true or false, got {value!r}")
    return normalized == "true"


def _csv(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items:
        raise ValueError("comma-separated setting cannot be empty")
    return items


def load_settings(config_dir: Path | str = "config") -> Settings:
    directory = _resolve_config_dir(Path(config_dir))
    data: dict[str, Any] = {}
    for name in ("default.yaml", "risk.yaml", "providers.yaml", "strategies.yaml", "autonomy.yaml"):
        data = _deep_merge(data, _read_yaml(directory / name))
    local_config = directory / "local.yaml"
    if local_config.exists():
        data = _deep_merge(data, load_local_config(local_config))
    mutable_local = _mutable_local_config(Path(config_dir))
    if mutable_local is not None and mutable_local != local_config and mutable_local.exists():
        data = _deep_merge(data, load_local_config(mutable_local))
    data = _deep_merge(data, _environment_overrides())
    if mutable_local is not None:
        data = _deep_merge(data, _frozen_runtime_paths())
    return Settings(public=PublicSettings.model_validate(data), secrets=SecretSettings())


def _resolve_config_dir(requested: Path) -> Path:
    if requested.is_absolute() or requested.exists():
        return requested
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        frozen_config = Path(frozen_root) / requested
        if frozen_config.exists():
            return frozen_config
    package_config = Path(__file__).resolve().parents[3] / requested
    return package_config if package_config.exists() else requested


def app_support_root() -> Path:
    """Where the desktop app keeps its mutable config, database and vault."""

    override = os.getenv("HYVERION_APP_SUPPORT")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / "Hyverion Quant AI"


def shares_app_state() -> bool:
    """True when this process must use the desktop app's config and database.

    Always inside the app bundle. From a source checkout too, once the app has
    been set up on this machine, so ``trading_bot broker status`` and the app
    see the same broker, keys and ledger. ``HYVERION_SHARE_APP_STATE=0`` opts
    out (tests, isolated development).
    """

    if sys.__dict__.get("_MEIPASS"):
        return True
    if os.getenv("HYVERION_SHARE_APP_STATE", "1") == "0":
        return False
    return (app_support_root() / "config" / "local.yaml").exists()


def local_config_path() -> Path:
    if shares_app_state():
        return app_support_root() / "config" / "local.yaml"
    return Path("config") / "local.yaml"


def _mutable_local_config(requested: Path) -> Path | None:
    """Return the writable local override shared with the desktop app."""

    frozen_root = sys.__dict__.get("_MEIPASS")
    default_dirs = {Path("config").resolve()}
    if isinstance(frozen_root, str) and frozen_root:
        default_dirs.add((Path(frozen_root) / "config").resolve())
    if requested != Path("config") and requested.resolve() not in default_dirs:
        return None
    return local_config_path() if shares_app_state() else None


def _frozen_runtime_paths() -> dict[str, dict[str, str]]:
    """Keep an app bundle's mutable control data out of its read-only resources."""

    root = app_support_root()
    return {
        "database": {
            "url": f"sqlite+aiosqlite:///{root / 'data' / 'trading_bot.db'}",
            "parquet_root": str(root / "parquet"),
        },
        # Relative memory paths would resolve against "/" inside the bundle.
        "memory": {
            "vault_path": str(root / "knowledge"),
            "embedding_model_dir": str(root / "models" / "all-MiniLM-L6-v2"),
        },
    }


def live_ai_models(settings: Settings, path: Path | None = None) -> AIModelsConfig:
    """Role models as saved right now.

    The app changes models without restarting the engine; the engine calls
    this each cycle. Any read or validation problem keeps the loaded models.
    """

    current = settings.public.ai.models
    path = path or local_config_path()
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        ai = loaded.get("ai") if isinstance(loaded, dict) else None
        models = ai.get("models") if isinstance(ai, dict) else None
        if not isinstance(models, dict):
            return current
        return AIModelsConfig.model_validate({**current.model_dump(), **models})
    except (OSError, ValueError, yaml.YAMLError):
        return current
