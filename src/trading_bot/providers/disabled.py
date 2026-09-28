from __future__ import annotations

from typing import Any

from trading_bot.providers.base import ProviderError, ProviderResult, SchemaT


class DisabledProvider:
    provider_id = "disabled"
    billing_mode = "disabled"

    async def invoke(
        self,
        *,
        agent_id: str,
        system_spec: str,
        context: dict[str, Any],
        model: str,
        output_schema: type[SchemaT],
        timeout_seconds: int,
    ) -> ProviderResult:
        del agent_id, system_spec, context, model, output_schema, timeout_seconds
        raise ProviderError("provider_disabled", retryable=False)
