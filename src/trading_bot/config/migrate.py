"""Migrate an operator's ``local.yaml`` from the crypto layout (v1) to QQQ (v2).

v1 described a Binance spot account: ``exchange`` section, ``market_type``,
``BASE/QUOTE`` symbols and a derivatives provider. v2 trades QQQ through a
broker adapter. The migration is deliberately conservative:

* risk limits, region, AI and memory settings are copied unchanged
  (a migration must never loosen or silently retune a risk limit). The one
  exception is ``drop_ignored_keys``: the first-release dollar values written by
  the old setup screen give way to the risk profile, by the owner's decision of
  2026-09-28 (see spec/DECISIONS.md); any other value is kept;
* crypto-only keys are dropped and the executable universe becomes QQQ;
* the broker becomes Alpaca Paper; nothing is sent until the owner stores
  its paper keys (the engine refuses to start without a connected broker);
* the original file is kept next to the new one as ``local.yaml.v1.bak``.

This is the only module allowed to mention the retired venue by name.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from trading_bot.config.models import CONFIG_VERSION

_RETIRED_TRADING_KEYS = ("market_type", "leverage_enabled")
_RETIRED_EXTERNAL_KEYS = ("derivatives_provider",)
# Crypto news sources removed from the allowlist in v2.
_RETIRED_NEWS_SOURCES = frozenset(
    {
        "bitcoin-project",
        "ethereum-foundation",
        "binance-announcements",
        "coinbase-blog",
        "coindesk-news",
        "the-block-news",
    }
)


def needs_migration(data: dict[str, Any]) -> bool:
    return data.get("config_version") != CONFIG_VERSION and bool(data)


def migrate_mapping(data: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return the v2 mapping and a list of human-readable changes."""

    if not needs_migration(data):
        return data, ()
    migrated = deepcopy(data)
    changes: list[str] = []
    trading = migrated.get("trading")
    if isinstance(trading, dict):
        for key in _RETIRED_TRADING_KEYS:
            if trading.pop(key, None) is not None:
                changes.append(f"trading.{key} removed")
        symbols = trading.get("allowed_symbols")
        if symbols is not None:
            trading["allowed_symbols"] = ["QQQ"]
            changes.append(f"trading.allowed_symbols {list(symbols)} -> ['QQQ']")
    if migrated.pop("exchange", None) is not None:
        changes.append("exchange section removed; broker set to alpaca (paper)")
        migrated.setdefault("broker", {"provider": "alpaca", "environment": "paper"})
    external = migrated.get("external_data")
    if isinstance(external, dict):
        for key in _RETIRED_EXTERNAL_KEYS:
            if external.pop(key, None) is not None:
                changes.append(f"external_data.{key} removed")
        feeds = external.get("news_feeds")
        if isinstance(feeds, dict):
            kept = {k: v for k, v in feeds.items() if k not in _RETIRED_NEWS_SOURCES}
            if kept != feeds:
                external["news_feeds"] = kept
                changes.append("crypto news feeds removed")
        reviewed = external.get("news_reviewed_sources")
        if isinstance(reviewed, list):
            kept_reviewed = [item for item in reviewed if item not in _RETIRED_NEWS_SOURCES]
            if kept_reviewed != reviewed:
                external["news_reviewed_sources"] = kept_reviewed
    if isinstance(trading, dict) and trading.pop("capital", None) is not None:
        changes.append("trading.capital removed; equity comes from the broker account")
    migrated = {"config_version": CONFIG_VERSION, **migrated}
    changes.append(f"config_version -> {CONFIG_VERSION}")
    return migrated, tuple(changes)


# Keys retired without a version bump. They are ignored on read and dropped
# the next time the config manager writes the file.
_IGNORED_KEYS: dict[str, tuple[str, ...]] = {
    "trading": ("capital",),  # equity now comes from the broker account
    # AI runs on subscriptions: no USD budget; limits are the plan's 5 h / weekly.
    # Model profiles (FAST/CHEAP/...) became roles (analysis/decision/improvement).
    "ai": ("max_daily_cost_usd", "primary_profile", "profiles"),
}
# The first-release dollar risk values, written into every local.yaml by the old
# setup screen. They are the old defaults, not an owner choice, so they give way
# to the risk profile. Any other value is the owner's and is kept untouched.
_LEGACY_RISK_DEFAULTS: dict[str, str] = {
    "base_risk_percent": "0.25",
    "max_base_risk_usd": "10",
    "daily_loss_hard_cap_usd": "25",
    "weekly_loss_hard_cap_usd": "75",
}


def drop_ignored_keys(data: dict[str, Any]) -> dict[str, Any]:
    for section, keys in _IGNORED_KEYS.items():
        block = data.get(section)
        if isinstance(block, dict) and any(key in block for key in keys):
            data = {**data, section: {k: v for k, v in block.items() if k not in keys}}
    risk = data.get("risk")
    if isinstance(risk, dict) and "profile" not in risk:
        kept = {
            k: v
            for k, v in risk.items()
            if not (k in _LEGACY_RISK_DEFAULTS and _same(v, _LEGACY_RISK_DEFAULTS[k]))
        }
        if kept != risk:
            data = {**data, "risk": kept}
    return data


def _same(value: Any, legacy: str) -> bool:
    try:
        return Decimal(str(value)) == Decimal(legacy)
    except (ArithmeticError, ValueError):
        return False


def load_local_config(path: Path) -> dict[str, Any]:
    """Read ``local.yaml``, migrating it on disk once when it is still v1.

    If the file cannot be rewritten (read-only location) the migrated mapping
    is still returned, so the process starts with a valid v2 configuration.
    """

    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    migrated, changes = migrate_mapping(loaded)
    if changes:
        try:
            _write_migrated(path, migrated)
        except OSError:
            pass
    return drop_ignored_keys(migrated)


def _write_migrated(path: Path, data: dict[str, Any]) -> None:
    backup = path.with_name(path.name + ".v1.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    descriptor, temp_path = tempfile.mkstemp(prefix="local.", suffix=".yaml", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(data, handle, sort_keys=False)
        os.replace(temp_path, path)
    except Exception:
        Path(temp_path).unlink(missing_ok=True)
        raise
