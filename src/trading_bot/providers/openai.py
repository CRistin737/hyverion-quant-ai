from __future__ import annotations

import importlib
import time
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from trading_bot.providers.base import ProviderError, ProviderResult, SchemaT
from trading_bot.providers.common import build_prompt, estimate_cost, validated_result


class OpenAIProvider:
    provider_id = "openai"
    billing_mode = "api"

    def __init__(
        self,
        api_key: str,
        *,
        input_cost_per_million_usd: Decimal | None = None,
        output_cost_per_million_usd: Decimal | None = None,
        base_url: str | None = None,
        provider_id: str = "openai",
    ) -> None:
        self.provider_id = provider_id
        self._api_key = api_key
        self._base_url = base_url
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
            module = importlib.import_module("openai")
        except ImportError as exc:
            raise ProviderError("provider_sdk_not_installed", retryable=False) from exc
        client = module.AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
        started = time.monotonic()
        try:
            response = await client.responses.parse(
                model=model,
                input=build_prompt(system_spec, context),
                text_format=output_schema,
                timeout=timeout_seconds,
            )
        except Exception as exc:
            raise ProviderError(
                "provider_request_failed", retryable=True, detail=type(exc).__name__
            ) from exc
        parsed: BaseModel | None = response.output_parsed
        if parsed is None:
            raise ProviderError("provider_refusal_or_empty", retryable=False)
        input_tokens = int(getattr(response.usage, "input_tokens", 0))
        output_tokens = int(getattr(response.usage, "output_tokens", 0))
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
