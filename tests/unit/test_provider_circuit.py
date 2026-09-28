from __future__ import annotations

import pytest
from pydantic import BaseModel

from trading_bot.providers.base import ProviderError
from trading_bot.providers.circuit import ProviderHealthBoard
from trading_bot.providers.router import ModelRouter, StaticJSONProvider


class Out(BaseModel):
    value: int


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Counting(StaticJSONProvider):
    def __init__(self, provider_id: str, output) -> None:
        super().__init__(output)
        self.provider_id = provider_id
        self.calls = 0

    async def invoke(self, **kwargs):
        self.calls += 1
        return await super().invoke(**kwargs)


def _router(providers, board):
    return ModelRouter(
        providers, {"STANDARD": "m"}, health=board
    )


async def _call(router):
    return await router.invoke(
        agent_id="a", profile="STANDARD", system_spec="s", context={}, output_schema=Out
    )


@pytest.mark.asyncio
async def test_dead_primary_is_skipped_until_cooldown_then_recovers() -> None:
    clock = Clock()
    board = ProviderHealthBoard(monotonic=clock)
    primary = Counting("claude", ProviderError("provider_quota_exhausted", retryable=True))
    backup = Counting("codex", {"value": 1})
    router = _router([primary, backup], board)

    first = await _call(router)
    assert first.provider == "codex" and primary.calls == 1
    assert first.fallback_reason == "claude:provider_quota_exhausted"

    # Cooldown open: the primary is not even tried, the backup answers at once.
    for _ in range(3):
        await _call(router)
    assert primary.calls == 1 and backup.calls == 4
    assert board.snapshot()["claude"]["last_code"] == "provider_quota_exhausted"

    # Subscription is renewed; after the cooldown one probe restores the primary.
    primary._output = {"value": 2}
    clock.t += 1801
    result = await _call(router)
    assert primary.calls == 2 and result.output.value == 2
    assert board.snapshot() == {}


@pytest.mark.asyncio
async def test_all_providers_open_fails_closed_without_calling_any() -> None:
    board = ProviderHealthBoard(monotonic=Clock())
    a = Counting("claude", ProviderError("provider_auth_required", retryable=True))
    b = Counting("codex", ProviderError("provider_rate_limited", retryable=True))
    router = _router([a, b], board)
    with pytest.raises(ProviderError):
        await _call(router)
    with pytest.raises(ProviderError) as exc:
        await _call(router)
    assert exc.value.code == "provider_circuit_open"
    assert (a.calls, b.calls) == (1, 1)


def test_cooldown_grows_and_probe_is_exclusive() -> None:
    clock = Clock()
    board = ProviderHealthBoard(monotonic=clock)
    board.record_failure("p", "provider_rate_limited")
    assert not board.allow("p")
    clock.t += 61
    assert board.allow("p") and not board.allow("p")  # single half-open probe
    board.record_failure("p", "provider_rate_limited")
    assert board.snapshot()["p"]["retry_in_seconds"] == 120
    board.record_failure("p", "invalid_structured_output")  # bad answer never trips
    board.reset("p")
    assert board.allow("p")


def test_snapshot_projects_engine_published_circuits() -> None:
    from trading_bot.control_api import _latest_provider_circuits

    circuit = {"state": "open", "failures": 2, "last_code": "provider_quota_exhausted"}
    rows = [
        {"payload": {"status": "PROVIDER_CIRCUIT", "circuits": {"openai_subscription": circuit}}},
        {"payload": {"status": "PROVIDER_CIRCUIT", "circuits": {}}},  # older, ignored
    ]
    assert _latest_provider_circuits(rows) == {"openai": circuit}
    cleared = [{"payload": {"status": "PROVIDER_CIRCUIT", "circuits": {}}}]
    assert _latest_provider_circuits(cleared) == {}
    assert _latest_provider_circuits([]) == {}
