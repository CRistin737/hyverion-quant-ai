from __future__ import annotations

import importlib
import time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from trading_bot.providers.base import ProviderError, ProviderResult, SchemaT
from trading_bot.providers.common import build_prompt, estimate_cost, validated_result


class AnthropicProvider:
    provider_id = "anthropic"
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
            module = importlib.import_module("anthropic")
        except ImportError as exc:
            raise ProviderError("provider_sdk_not_installed", retryable=False) from exc
        client = module.AsyncAnthropic(api_key=self._api_key, timeout=timeout_seconds)
        started = time.monotonic()
        try:
            response = await client.messages.parse(
                model=model,
                max_tokens=2048,
                messages=[{"role": "user", "content": build_prompt(system_spec, context)}],
                output_format=output_schema,
            )
        except Exception as exc:
            raise ProviderError(
                "provider_request_failed", retryable=True, detail=type(exc).__name__
            ) from exc
        parsed: BaseModel | None = response.parsed_output
        if parsed is None:
            raise ProviderError("provider_refusal_or_empty", retryable=False)
        input_tokens = int(response.usage.input_tokens)
        output_tokens = int(response.usage.output_tokens)
        return validated_result(
            raw=parsed,
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
