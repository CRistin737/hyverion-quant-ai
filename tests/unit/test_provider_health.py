from __future__ import annotations

import pytest
import respx
from httpx import Response

from trading_bot.config import load_settings
from trading_bot.providers.access import check_provider, provider_statuses


@pytest.mark.asyncio
async def test_missing_provider_credential_fails_closed() -> None:
    result = await check_provider("openai", None)
    assert result.status == "NOT_CONFIGURED"
    assert not result.supported


@pytest.mark.asyncio
async def test_unknown_provider_healthcheck_is_explicitly_unsupported() -> None:
    result = await check_provider("unknown", "secret")
    assert result.status == "UNSUPPORTED"
    assert result.code == "provider_not_supported"


@respx.mock
async def test_anthropic_models_healthcheck_uses_official_headers() -> None:
    route = respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=Response(200, json={"data": []})
    )
    result = await check_provider("anthropic", "secret")
    assert result.status == "CONNECTED"
    assert route.calls[0].request.headers["x-api-key"] == "secret"
    assert route.calls[0].request.headers["anthropic-version"] == "2023-06-01"


@respx.mock
async def test_gemini_models_healthcheck_maps_auth_failure() -> None:
    respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
        return_value=Response(403, json={"error": {"status": "PERMISSION_DENIED"}})
    )
    result = await check_provider("gemini", "secret")
    assert result.status == "EXPIRED"
    assert result.code == "credential_rejected"


def test_provider_status_can_surface_last_health_result_without_secret() -> None:
    settings = load_settings()
    statuses = provider_statuses(
        settings,
        health_overrides={
            "openai": {
                "status": "CONNECTED",
                "detail": "Official models endpoint responded.",
            }
        },
    )
    openai = next(item for item in statuses if item.provider_id == "openai")
    assert openai.auth_state == "CONNECTED"
    assert openai.detail == "Official models endpoint responded."


def test_provider_limits_stay_unknown_until_a_value_is_configured() -> None:
    settings = load_settings()
    statuses = provider_statuses(settings)
    assert all(status.limit_source == "UNKNOWN" for status in statuses)
    assert all(status.auth_state == "AUTH_REQUIRED" for status in statuses)

    configured = settings.model_copy(deep=True)
    configured.public.ai.provider_limits["openai"] = configured.public.ai.provider_limits[
        "openai"
    ].model_copy(update={"context_window": 128000})
    openai = next(
        item for item in provider_statuses(configured) if item.provider_id == "openai"
    )
    assert openai.context_window == 128000
    assert openai.limit_source == "CONFIGURED"


def test_subscription_connection_is_projected_on_provider_card() -> None:
    settings = load_settings()
    statuses = provider_statuses(
        settings,
        subscription_overrides={
            "anthropic": {
                "status": "CONNECTED",
                "supported": True,
                "plan": "pro",
                "detail": "official status",
            }
        },
    )
    anthropic = next(item for item in statuses if item.provider_id == "anthropic")
    assert anthropic.auth_state == "CONNECTED"
    assert anthropic.subscription.auth_state == "CONNECTED"
