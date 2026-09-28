"""Build the configured provider chain without crossing auth boundaries."""

from __future__ import annotations

from trading_bot.config.models import AI_ROLES, AIModelsConfig, Settings
from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.providers.anthropic import AnthropicProvider
from trading_bot.providers.base import AIProvider
from trading_bot.providers.capabilities import SUBSCRIPTION_PROVIDER_IDS
from trading_bot.providers.circuit import PROCESS_HEALTH, ProviderHealthBoard
from trading_bot.providers.disabled import DisabledProvider
from trading_bot.providers.gemini import GeminiProvider
from trading_bot.providers.openai import OpenAIProvider
from trading_bot.providers.router import ModelRouter
from trading_bot.providers.subscription_cli import SubscriptionCLIProvider
from trading_bot.providers.xai import XAIProvider
from trading_bot.security.secrets import KeyringSecretStore


def build_provider_chain(settings: Settings) -> list[AIProvider]:
    """Construct the configured primary/fallback chain.

    Subscription mode routes to provider-owned local CLIs. It never silently
    falls back to an API key. API mode only includes providers with an explicit
    environment or OS-keychain secret.
    """

    provider_ids = _provider_ids(settings)
    if not provider_ids:
        return [DisabledProvider()]
    providers: list[AIProvider] = []
    for provider_id in provider_ids:
        provider = _build_provider(settings, provider_id)
        if provider is not None:
            providers.append(provider)
    return providers or [DisabledProvider()]


def model_by_role(settings: Settings, models: AIModelsConfig | None = None) -> dict[str, str]:
    """Role -> model for the primary provider (``"default"`` = CLI default)."""

    chosen = models or settings.public.ai.models
    return {role: chosen.for_role(role) or "default" for role in AI_ROLES}


def build_model_router(
    settings: Settings,
    *,
    models: AIModelsConfig | None = None,
    metrics: MetricsRegistry | None = None,
    health: ProviderHealthBoard | None = None,
) -> ModelRouter:
    """Create the production router used by ``AgentRuntime``.

    ``models`` lets the engine pass the latest role models (changed from the
    app) without a restart.
    """

    return ModelRouter(
        build_provider_chain(settings),
        model_by_role(settings, models),
        timeout_seconds=settings.public.ai.request_timeout_seconds,
        max_schema_retries=settings.public.ai.max_schema_retries,
        metrics=metrics,
        # The engine builds a router every cycle; failures must outlive it.
        health=health if health is not None else PROCESS_HEALTH,
    )


def _provider_ids(settings: Settings) -> tuple[str, ...]:
    primary = settings.public.ai.primary_provider.strip().lower()
    fallback = tuple(item.strip().lower() for item in settings.public.ai.fallback_providers)
    return tuple(dict.fromkeys(item for item in (primary, *fallback) if item != "disabled"))


def _build_provider(settings: Settings, provider_id: str) -> AIProvider | None:
    if settings.public.ai.primary_auth_mode == "subscription":
        if provider_id not in SUBSCRIPTION_PROVIDER_IDS:
            return None
        return SubscriptionCLIProvider(provider_id)

    store = KeyringSecretStore()
    secret_name = {
        "anthropic": "anthropic_api_key",
        "openai": "openai_api_key",
        "xai": "xai_api_key",
        "gemini": "gemini_api_key",
    }.get(provider_id)
    if secret_name is None:
        return None
    configured = getattr(settings.secrets, secret_name)
    key = configured.get_secret_value().strip() if configured is not None else ""
    if not key:
        key = store.get(f"provider:{provider_id}:api_key") or ""
    if not key:
        return None
    # API mode is legacy; there is no USD budget, so no rates are configured.
    input_rate = output_rate = None
    if provider_id == "anthropic":
        return AnthropicProvider(
            key,
            input_cost_per_million_usd=input_rate,
            output_cost_per_million_usd=output_rate,
        )
    if provider_id == "openai":
        return OpenAIProvider(
            key,
            input_cost_per_million_usd=input_rate,
            output_cost_per_million_usd=output_rate,
        )
    if provider_id == "xai":
        return XAIProvider(
            key,
            input_cost_per_million_usd=input_rate,
            output_cost_per_million_usd=output_rate,
        )
    return GeminiProvider(
        key,
        input_cost_per_million_usd=input_rate,
        output_cost_per_million_usd=output_rate,
    )
