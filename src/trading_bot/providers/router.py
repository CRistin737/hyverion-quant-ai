from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

import structlog
from pydantic import BaseModel

from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.providers.base import AIProvider, ProviderError, ProviderResult, SchemaT
from trading_bot.providers.circuit import ProviderHealthBoard


class ModelRouter:
    """One gateway for every agent call.

    ``model_by_role`` names the model for the *primary* provider per role
    (``"default"`` = its CLI default). Fallback providers always run their own
    default model: a model name belongs to one vendor. There is no USD budget;
    subscription plans enforce their own 5-hour and weekly limits, and a
    provider at its limit is skipped by the circuit breaker.
    """

    def __init__(
        self,
        providers: list[AIProvider],
        model_by_role: Mapping[str, str | None],
        *,
        timeout_seconds: int = 30,
        max_schema_retries: int = 1,
        metrics: MetricsRegistry | None = None,
        health: ProviderHealthBoard | None = None,
    ) -> None:
        billing_modes = {getattr(provider, "billing_mode", "unknown") for provider in providers}
        if len(billing_modes) > 1:
            raise ValueError(
                "provider chain must use one billing mode; API and subscription adapters "
                "cannot be mixed"
            )
        self._providers = providers
        self._models = model_by_role
        self._timeout = timeout_seconds
        self._max_schema_retries = max_schema_retries
        self._metrics = metrics
        self._health = health if health is not None else ProviderHealthBoard()
        self._logger = structlog.get_logger("provider_gateway")

    @property
    def health(self) -> ProviderHealthBoard:
        return self._health

    async def invoke(
        self,
        *,
        agent_id: str,
        profile: str,
        system_spec: str,
        context: dict[str, Any],
        output_schema: type[SchemaT],
    ) -> ProviderResult:
        if profile not in self._models:
            raise ProviderError("unknown_model_profile", retryable=False)
        primary_model = self._models[profile]
        if not primary_model:
            raise ProviderError("model_profile_unconfigured", retryable=False)
        last_error: ProviderError | None = None
        attempted: list[str] = []
        failures: list[str] = []
        for index, provider in enumerate(self._providers):
            model = primary_model if index == 0 else "default"
            if model == "default" and provider.billing_mode == "api":
                # "default" only means something to a subscription CLI; an API
                # needs a concrete model name. Skip this hop instead of failing
                # every call with an HTTP error.
                failures.append(f"{provider.provider_id}:api_model_unconfigured")
                if last_error is None:
                    last_error = ProviderError("api_model_unconfigured", retryable=False)
                continue
            if not self._health.allow(provider.provider_id):
                # Known-unavailable (quota, login, rate limit): skip without
                # spending a full timeout, and go straight to the next hop.
                failures.append(f"{provider.provider_id}:circuit_open")
                if last_error is None:
                    last_error = ProviderError(
                        "provider_circuit_open", retryable=False, detail=provider.provider_id
                    )
                if self._metrics is not None:
                    self._metrics.increment(
                        "provider_circuit_skips_total",
                        labels={"provider": provider.provider_id},
                    )
                continue
            if provider.provider_id not in attempted:
                attempted.append(provider.provider_id)
            attempts = self._max_schema_retries + 1
            for _ in range(attempts):
                try:
                    result = await provider.invoke(
                        agent_id=agent_id,
                        system_spec=system_spec,
                        context=context,
                        model=model,
                        output_schema=output_schema,
                        timeout_seconds=self._timeout,
                    )
                    # The adapter boundary is the source of truth for billing
                    # mode. A vendor ID such as ``openai`` is ambiguous: it
                    # may represent API billing or the official subscription
                    # CLI. Stamp it before recording cost or returning the
                    # result to AgentRuntime.
                    result = replace(result, billing_mode=provider.billing_mode)
                    self._health.record_success(provider.provider_id)
                    if self._metrics is not None:
                        self._metrics.increment(
                            "ai_provider_calls_total",
                            labels={
                                "provider": provider.provider_id,
                                "billing_mode": provider.billing_mode,
                            },
                        )
                        if result.cost_usd is not None:
                            self._metrics.increment(
                                "ai_cost_usd_total",
                                result.cost_usd,
                                labels={"billing_mode": provider.billing_mode},
                            )
                    if failures:
                        fallback_reason = "; ".join(failures)
                        self._logger.warning(
                            "provider_fallback_used",
                            agent_id=agent_id,
                            primary_provider=attempted[0],
                            selected_provider=provider.provider_id,
                            attempted_providers=attempted,
                            fallback_reason=fallback_reason,
                        )
                        if self._metrics is not None:
                            self._metrics.increment(
                                "provider_fallbacks_total",
                                labels={"selected_provider": provider.provider_id},
                            )
                        return replace(
                            result,
                            attempted_providers=tuple(attempted),
                            fallback_reason=fallback_reason,
                        )
                    return replace(result, attempted_providers=tuple(attempted))
                except ProviderError as exc:
                    last_error = exc
                    failures.append(f"{provider.provider_id}:{exc.code}")
                    self._health.record_failure(provider.provider_id, exc.code)
                    if self._metrics is not None:
                        self._metrics.increment(
                            "ai_provider_failures_total",
                            labels={
                                "provider": provider.provider_id,
                                "error_code": exc.code,
                            },
                        )
                    if not exc.retryable:
                        break
                    if exc.code != "invalid_structured_output":
                        break
                except Exception as exc:  # provider boundary: fail over safely
                    # A provider adapter must not abort the gateway with an
                    # unclassified SDK/CLI exception. Keep only a stable type
                    # name in the audit trail; never persist prompt/token data.
                    last_error = ProviderError(
                        "provider_runtime_error",
                        retryable=True,
                        detail=type(exc).__name__,
                    )
                    failures.append(f"{provider.provider_id}:provider_runtime_error")
                    self._health.record_failure(provider.provider_id, "provider_runtime_error")
                    if self._metrics is not None:
                        self._metrics.increment(
                            "ai_provider_failures_total",
                            labels={
                                "provider": provider.provider_id,
                                "error_code": "provider_runtime_error",
                            },
                        )
                    break
        self._logger.error(
            "provider_chain_exhausted",
            agent_id=agent_id,
            attempted_providers=attempted,
            failure_codes=failures,
        )
        if self._metrics is not None:
            self._metrics.increment("provider_chain_exhausted_total")
        if last_error is None:
            raise ProviderError(
                "no_provider_available",
                retryable=False,
                attempted_providers=tuple(attempted),
            )
        raise ProviderError(
            last_error.code,
            retryable=False,
            detail=last_error.detail,
            attempted_providers=tuple(attempted),
            fallback_reason="; ".join(failures) if failures else None,
            usage=last_error.usage,
        ) from last_error


class StaticJSONProvider:
    """Test/replay provider. It validates supplied data through the production schema path."""

    provider_id = "static"
    billing_mode = "subscription"

    def __init__(self, output: BaseModel | dict[str, Any] | str | Exception) -> None:
        self._output = output

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
        del agent_id, system_spec, context, timeout_seconds
        if isinstance(self._output, Exception):
            raise self._output
        from trading_bot.providers.common import validated_result

        return validated_result(
            raw=self._output,
            schema=output_schema,
            provider=self.provider_id,
            model=model,
            latency_ms=1,
            input_tokens=0,
            output_tokens=0,
            cost_usd=None,
        )
