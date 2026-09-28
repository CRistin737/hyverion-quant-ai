"""Source trust registry (§136) and the keys each data source needs.

The registry is data (``config/sources.yaml``), versioned, and read-only at
runtime. A source is *usable* only when its key (if any) is present; otherwise
it reports ``key_missing`` and its consumers fail closed with ``unavailable``
instead of inventing data.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from trading_bot.config.models import SecretSettings
from trading_bot.security import credentials as _credentials
from trading_bot.security.secrets import SecretStore

SourceCategory = Literal[
    "OFFICIAL_REGULATOR",
    "OFFICIAL_COMPANY",
    "LICENSED_WIRE",
    "MAJOR_FINANCIAL_PRESS",
    "AGGREGATOR",
    "SOCIAL",
    "UNKNOWN",
]
# Default trust by category, used to weigh evidence (never to create it).
CATEGORY_TRUST: dict[str, float] = {
    "OFFICIAL_REGULATOR": 1.0,
    "OFFICIAL_COMPANY": 0.9,
    "LICENSED_WIRE": 0.8,
    "MAJOR_FINANCIAL_PRESS": 0.7,
    "AGGREGATOR": 0.5,
    "SOCIAL": 0.2,
    "UNKNOWN": 0.0,
}

# Keychain names of data-source keys. Names, not secrets.
DATA_SOURCE_KEYS: dict[str, str] = {
    "data:fred:api_key": "FRED_API_KEY",
    "data:finnhub:api_key": "FINNHUB_API_KEY",
    # Not a secret: the owner's email, which the SEC and BLS require in the
    # User-Agent of automated clients. Kept in the keychain with the keys.
    "data:contact_email": "DATA_CONTACT_EMAIL",
}


class SourceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    category: SourceCategory
    access: Literal["api", "feed", "page"]
    hosts: tuple[str, ...]
    key: str | None
    purpose: str
    terms_url: str
    terms_checked_at: str

    @property
    def trust(self) -> float:
        return CATEGORY_TRUST[self.category]


class SourceRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    sources: tuple[SourceSpec, ...]

    def get(self, source_id: str) -> SourceSpec:
        for spec in self.sources:
            if spec.id == source_id:
                return spec
        raise KeyError(source_id)

    @property
    def hosts(self) -> frozenset[str]:
        return frozenset(host for spec in self.sources for host in spec.hosts)


def _registry_path() -> Path:
    import sys

    frozen = getattr(sys, "_MEIPASS", None)
    if frozen and (Path(frozen) / "config" / "sources.yaml").exists():
        return Path(frozen) / "config" / "sources.yaml"
    return Path(__file__).resolve().parents[3] / "config" / "sources.yaml"


@lru_cache(maxsize=1)
def load_source_registry(path: str | None = None) -> SourceRegistry:
    data = yaml.safe_load(Path(path or _registry_path()).read_text(encoding="utf-8"))
    return SourceRegistry.model_validate(data)


def data_source_key(
    name: str, secrets: SecretSettings | None = None, store: SecretStore | None = None
) -> str | None:
    """A data-source key from the environment (headless) or the keychain."""

    if name.startswith("broker:alpaca_paper"):
        credentials = _credentials.alpaca_paper_credentials(secrets or SecretSettings(), store)
        return credentials.key_id if credentials else None
    env_name = DATA_SOURCE_KEYS.get(name)
    if env_name is None:
        raise KeyError(name)
    value = os.getenv(env_name, "").strip()
    if not value:
        # Resolved through the credentials module so tests can isolate the keychain.
        value = ((store or _credentials.KeyringSecretStore()).get(name) or "").strip()
    return value or None


def source_key_present(
    spec: SourceSpec, secrets: SecretSettings | None = None, store: SecretStore | None = None
) -> bool:
    return spec.key is None or data_source_key(spec.key, secrets, store) is not None
