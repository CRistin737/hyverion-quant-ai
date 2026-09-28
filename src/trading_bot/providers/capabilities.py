"""Provider capability matrix used by configuration, auth, and routing.

The matrix is intentionally conservative: a provider is subscription-capable
only when its provider-owned CLI exposes an official local authentication path.
It is the single source of truth for the one-primary/fallback-chain guard.
"""

from __future__ import annotations

from typing import Literal

ProviderId = Literal["anthropic", "openai", "xai", "gemini"]

SUPPORTED_PROVIDER_IDS = frozenset({"anthropic", "openai", "xai", "gemini"})
API_PROVIDER_IDS = SUPPORTED_PROVIDER_IDS

# Gemini CLI supports Google-account sign-in for individual users and Google
# AI Pro/Ultra accounts. The adapter uses only the official CLI, never cached
# OAuth material or an undocumented consumer endpoint.
SUBSCRIPTION_PROVIDER_IDS = frozenset({"anthropic", "openai", "xai", "gemini"})


def normalize_provider_id(value: str) -> str:
    return value.strip().lower()


def subscription_provider_supported(provider_id: str) -> bool:
    return normalize_provider_id(provider_id) in SUBSCRIPTION_PROVIDER_IDS
