from __future__ import annotations

import asyncio
import importlib
import time
from decimal import Decimal
from typing import Any

from trading_bot.providers.base import ProviderError, ProviderResult, SchemaT
from trading_bot.providers.common import build_prompt, estimate_cost, validated_result


class GeminiProvider:
    provider_id = "gemini"
    billing_mode = "api"

    def __init__(
        self,
        api_key: str,
        *,
        input_cost_per_million_usd: Decimal | None = None,
        output_cost_per_million_usd: Decimal | None = None,
    ) -> None:
        self._api_key = api_key
        self._input_rate = input_cost_per_million_usd
        self._output_rate = output_cost_per_million_usd

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
        del agent_id
        try:
            module = importlib.import_module("google.genai")
        except ImportError as exc:
            raise ProviderError("provider_sdk_not_installed", retryable=False) from exc
        client = module.Client(api_key=self._api_key)
        started = time.monotonic()

        def call() -> Any:
            return client.interactions.create(
                model=model,
                input=build_prompt(system_spec, context),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": output_schema.model_json_schema(),
                },
                store=False,
            )

        try:
            response = await asyncio.wait_for(asyncio.to_thread(call), timeout_seconds)
        except Exception as exc:
            raise ProviderError(
                "provider_request_failed", retryable=True, detail=type(exc).__name__
            ) from exc
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0))
        output_tokens = int(getattr(usage, "output_tokens", 0))
        return validated_result(
            raw=response.output_text,
            schema=output_schema,
            provider=self.provider_id,
            model=model,
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost(
                input_tokens, output_tokens, self._input_rate, self._output_rate
            ),
        )
