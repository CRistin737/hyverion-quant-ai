from __future__ import annotations

from decimal import Decimal

from trading_bot.providers.openai import OpenAIProvider


class XAIProvider(OpenAIProvider):
    """xAI adapter using its documented OpenAI-compatible endpoint."""

    billing_mode = "api"

    def __init__(
        self,
        api_key: str,
        *,
        input_cost_per_million_usd: Decimal | None = None,
        output_cost_per_million_usd: Decimal | None = None,
    ) -> None:
        super().__init__(
            api_key,
            input_cost_per_million_usd=input_cost_per_million_usd,
            output_cost_per_million_usd=output_cost_per_million_usd,
            base_url="https://api.x.ai/v1",
            provider_id="xai",
        )
