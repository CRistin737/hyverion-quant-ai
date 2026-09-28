from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        detail: str = "",
        attempted_providers: tuple[str, ...] = (),
        fallback_reason: str | None = None,
        usage: ProviderResult | None = None,
    ) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.retryable = retryable
        self.detail = detail
        self.attempted_providers = attempted_providers
        self.fallback_reason = fallback_reason
        # A provider may have consumed a billable response before the budget
        # guard notices that the cap was crossed. The runtime uses this
        # bounded result to persist usage even though the cycle fails closed.
        self.usage = usage


@dataclass(frozen=True, slots=True)
class ProviderResult:
    output: BaseModel
    provider: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    # The router stamps the boundary's billing mode after the adapter returns.
    # Provider IDs alone are not sufficient because the same vendor can be
    # reached through an API adapter or its official subscription CLI.
    billing_mode: str = "unknown"
    # The router fills these fields so the single provider gateway remains
    # auditable without exposing provider errors or credentials to the model.
    attempted_providers: tuple[str, ...] = ()
    fallback_reason: str | None = None


class AIProvider(Protocol):
    provider_id: str
    billing_mode: str

    async def invoke(
        self,
        *,
        agent_id: str,
        system_spec: str,
        context: dict[str, Any],
        model: str,
        output_schema: type[SchemaT],
        timeout_seconds: int,
    ) -> ProviderResult: ...
