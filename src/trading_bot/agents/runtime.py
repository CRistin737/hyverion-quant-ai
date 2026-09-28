"""Safe provider invocation boundary for neutral agent specifications.

The runtime is intentionally boring: it loads a versioned ``AGENT.md``, sends
only data context to the provider router, validates the typed response, and
records enough metadata to reconstruct what happened.  It never receives an
exchange client and it cannot create an order.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from trading_bot.agents.registry import AgentRegistry
from trading_bot.core.clock import Clock
from trading_bot.db.repositories import AuditRepository
from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.providers.base import ProviderError, ProviderResult, SchemaT
from trading_bot.providers.router import ModelRouter
from trading_bot.schemas.observability import AgentRunRecord


@dataclass(frozen=True, slots=True)
class AgentInvocation:
    run: AgentRunRecord
    result: ProviderResult


class AgentRuntime:
    """Invoke one registered agent and fail closed on every provider boundary."""

    def __init__(
        self,
        *,
        registry: AgentRegistry,
        router: ModelRouter,
        repository: AuditRepository,
        clock: Clock,
        metrics: MetricsRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._repository = repository
        self._clock = clock
        self._metrics = metrics

    async def invoke(
        self,
        *,
        agent_id: str,
        profile: str,
        context: dict[str, Any],
        output_schema: type[SchemaT],
    ) -> AgentInvocation:
        descriptor, system_spec = self._registry.specification(agent_id)
        started_at = self._clock.now()
        run_id = str(uuid4())
        await self._repository.start_agent_run(
            agent_id=agent_id,
            agent_version=descriptor.version,
            started_at=started_at,
            run_id=run_id,
        )
        if self._metrics is not None:
            self._metrics.increment("agent_runs_started_total", labels={"agent_id": agent_id})
        context_digest = _digest_context(context)
        try:
            result = await self._router.invoke(
                agent_id=agent_id,
                profile=profile,
                system_spec=system_spec,
                context=context,
                output_schema=output_schema,
            )
        except ProviderError as exc:
            if self._metrics is not None:
                self._metrics.increment(
                    "agent_runs_failed_total",
                    labels={"agent_id": agent_id, "error_code": exc.code},
                )
            finished_at = self._clock.now()
            if exc.usage is not None:
                usage = exc.usage
                await self._repository.finish_agent_run(
                    run_id,
                    status="FAILED",
                    finished_at=finished_at,
                    provider=usage.provider,
                    model=usage.model,
                    latency_ms=usage.latency_ms,
                    error_code=exc.code,
                )
                await self._repository.record_model_usage(
                    agent_run_id=run_id,
                    provider=usage.provider,
                    model=usage.model,
                    billing_mode=(
                        usage.billing_mode
                        if usage.billing_mode in {"api", "subscription"}
                        else _billing_mode(usage.provider)
                    ),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                    fallback_reason=usage.fallback_reason or exc.fallback_reason,
                    attempted_providers=usage.attempted_providers
                    or exc.attempted_providers,
                    created_at=finished_at,
                )
            else:
                await self._repository.finish_agent_run(
                    run_id,
                    status="FAILED",
                    finished_at=finished_at,
                    error_code=exc.code,
                )
            await self._repository.append(
                "agent_outputs",
                {
                    "run_id": run_id,
                    "agent_id": agent_id,
                    "agent_version": descriptor.version,
                    "spec_hash": descriptor.spec_hash,
                    "status": "FAILED",
                    "error_code": exc.code,
                    "attempted_providers": list(exc.attempted_providers),
                    "fallback_reason": exc.fallback_reason,
                    "context_digest": context_digest,
                    "finished_at": finished_at.isoformat(),
                },
                created_at=finished_at,
            )
            raise
        except BaseException as exc:
            # Cancelled (engine stopped, or a sibling agent failed) or an
            # unexpected error: close the row so the app never shows a run as
            # "analyzing" forever. The write is shielded so the cancellation
            # that is unwinding this task cannot cut it short.
            status = "CANCELLED" if isinstance(exc, asyncio.CancelledError) else "FAILED"
            error_code = "cancelled" if status == "CANCELLED" else type(exc).__name__
            await _shielded(
                self._repository.finish_agent_run(
                    run_id,
                    status=status,
                    finished_at=self._clock.now(),
                    error_code=error_code,
                )
            )
            raise

        finished_at = self._clock.now()
        if self._metrics is not None:
            self._metrics.increment("agent_runs_succeeded_total", labels={"agent_id": agent_id})
        await self._repository.finish_agent_run(
            run_id,
            status="SUCCEEDED",
            finished_at=finished_at,
            provider=result.provider,
            model=result.model,
            latency_ms=result.latency_ms,
        )
        await self._repository.record_model_usage(
            agent_run_id=run_id,
            provider=result.provider,
            model=result.model,
            billing_mode=(
                result.billing_mode
                if result.billing_mode in {"api", "subscription"}
                else _billing_mode(result.provider)
            ),
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            fallback_reason=result.fallback_reason,
            attempted_providers=result.attempted_providers,
            created_at=finished_at,
        )
        await self._repository.append(
            "agent_outputs",
            {
                "run_id": run_id,
                "agent_id": agent_id,
                "agent_version": descriptor.version,
                "spec_hash": descriptor.spec_hash,
                "status": "SUCCEEDED",
                "provider": result.provider,
                "model": result.model,
                "attempted_providers": list(result.attempted_providers),
                "fallback_reason": result.fallback_reason,
                "context_digest": context_digest,
                "output": _serialize_output(result.output),
                "finished_at": finished_at.isoformat(),
            },
            created_at=finished_at,
        )
        record = AgentRunRecord(
            run_id=run_id,
            agent_id=agent_id,
            agent_version=descriptor.version,
            provider=result.provider,
            model=result.model,
            status="SUCCEEDED",
            latency_ms=result.latency_ms,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost_usd=result.cost_usd,
            started_at=started_at,
            finished_at=finished_at,
        )
        return AgentInvocation(run=record, result=result)


async def _shielded(write: Any) -> None:
    """Finish a status write even while the calling task is being cancelled."""

    task = asyncio.ensure_future(write)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
    except Exception:
        return


def _serialize_output(output: BaseModel) -> dict[str, Any]:
    return output.model_dump(mode="json")


def _digest_context(context: dict[str, Any]) -> str:
    encoded = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _billing_mode(provider_id: str) -> str:
    if provider_id in {"anthropic", "openai", "xai", "gemini"}:
        return "api"
    if (
        provider_id.endswith("_cli")
        or provider_id.endswith("_subscription")
        or provider_id == "static"
    ):
        return "subscription"
    return "unknown"
