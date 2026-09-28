from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from trading_bot.config import load_settings
from trading_bot.providers.access import check_subscription
from trading_bot.providers.factory import build_model_router, build_provider_chain
from trading_bot.providers.subscription_cli import SubscriptionCLIProvider, sanitized_environment


class Output(BaseModel):
    decision: str


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        del input
        return self.stdout, self.stderr

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


@pytest.mark.asyncio
async def test_codex_subscription_status_is_parsed_without_api_keys(monkeypatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/codex" if name == "codex" else None
    )
    seen: dict[str, str] = {}

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        del command
        seen.update(kwargs["env"])
        return FakeProcess(b"Logged in using ChatGPT")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross-boundary")
    result = await check_subscription("openai")
    assert result.status == "CONNECTED"
    assert result.code == "subscription_authenticated"
    assert "OPENAI_API_KEY" not in seen


@pytest.mark.asyncio
async def test_claude_subscription_plan_is_exposed(monkeypatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/claude" if name == "claude" else None
    )
    payload = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        del command, kwargs
        return FakeProcess(json.dumps(payload).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = await check_subscription("anthropic")
    assert result.status == "CONNECTED"
    assert result.plan == "pro"


@pytest.mark.asyncio
async def test_claude_usage_command_exposes_remaining_session_and_week(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/claude")
    outputs = [
        json.dumps(
            {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"}
        ).encode(),
        json.dumps(
            {
                "result": (
                    "Current session: 82% used · resets Sep 15 at 12:20am (America/New_York)\n"
                    "Current week (all models): 10% used · resets Sep 18 at 3pm "
                    "(America/New_York)"
                )
            }
        ).encode(),
        json.dumps(
            {
                "result": (
                    "## Context Usage\n\n**Model:** claude-opus-5  \n"
                    "**Tokens:** 1.4k / 1m (0%)\n\n"
                    "| Free space | 965.6k | 96.6% |"
                )
            }
        ).encode(),
    ]

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        del command, kwargs
        return FakeProcess(outputs.pop(0))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = await check_subscription("anthropic")
    assert result.session_remaining == 18
    assert result.weekly_remaining == 90
    assert result.session_reset_label == "Sep 15 at 12:20am (America/New_York)"
    assert result.weekly_reset_label == "Sep 18 at 3pm (America/New_York)"
    assert str(result.context_remaining_percent) == "96.6"
    assert result.context_used_tokens == "1.4k"
    assert result.context_window_tokens == "1m"
    assert result.context_model == "claude-opus-5"
    assert result.usage_detail == (
        "Official Claude Code /usage: session 18% remaining, week 90% remaining"
    )


@pytest.mark.asyncio
async def test_grok_subscription_quota_stays_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/grok" if name == "grok" else None
    )

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        assert command == ("grok", "--version")
        del kwargs
        return FakeProcess(b"grok 0.1.0")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = await check_subscription("xai")
    assert result.status == "UNKNOWN"
    assert result.weekly_remaining is None


@pytest.mark.asyncio
async def test_gemini_subscription_cli_is_supported_but_quota_stays_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/local/bin/gemini" if name == "gemini" else None
    )

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        del command, kwargs
        return FakeProcess(b"0.1.0")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    result = await check_subscription("gemini")
    assert result.supported is True
    assert result.status == "UNKNOWN"
    assert result.code == "subscription_status_unavailable"


@pytest.mark.asyncio
async def test_subscription_cli_validates_nested_structured_output(monkeypatch) -> None:
    provider = SubscriptionCLIProvider("anthropic")
    monkeypatch.setattr(
        "trading_bot.providers.subscription_cli.check_subscription",
        lambda provider_id, **_: _connected(provider_id),
    )
    process = FakeProcess(json.dumps({"result": {"decision": "NO_TRADE"}}).encode())

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        del command, kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/claude")
    result = await provider.invoke(
        agent_id="strategy",
        system_spec="Return a decision.",
        context={},
        model="",
        output_schema=Output,
        timeout_seconds=2,
    )
    assert result.output == Output(decision="NO_TRADE")
    assert result.provider == "anthropic_subscription"
    assert result.cost_usd is None


@pytest.mark.asyncio
async def test_gemini_subscription_cli_validates_official_json_envelope(monkeypatch) -> None:
    provider = SubscriptionCLIProvider("gemini")
    process = FakeProcess(
        json.dumps({"response": json.dumps({"decision": "NO_TRADE"})}).encode()
    )

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        assert command[0] == "gemini"
        assert "--output-format" in command
        del kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gemini")
    result = await provider.invoke(
        agent_id="strategy",
        system_spec="Return a decision.",
        context={},
        model="",
        output_schema=Output,
        timeout_seconds=2,
    )
    assert result.output == Output(decision="NO_TRADE")
    assert result.provider == "gemini_subscription"


@pytest.mark.asyncio
async def test_grok_subscription_cli_uses_bounded_read_only_headless_flags(monkeypatch) -> None:
    provider = SubscriptionCLIProvider("xai")
    process = FakeProcess(
        json.dumps({"text": json.dumps({"decision": "NO_TRADE"})}).encode()
    )
    captured: tuple[str, ...] = ()

    async def create(*command: str, **kwargs: Any) -> FakeProcess:
        nonlocal captured
        captured = command
        del kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/grok")
    result = await provider.invoke(
        agent_id="strategy",
        system_spec="Return a decision.",
        context={},
        model="",
        output_schema=Output,
        timeout_seconds=2,
    )
    assert result.output == Output(decision="NO_TRADE")
    assert captured[0:6] == (
        "grok",
        "--single",
        "--prompt-file",
        captured[3],
        "--output-format",
        "json",
    )
    assert captured[6] == "--json-schema"
    assert "plan" in captured
    assert "--sandbox" in captured
    assert "read-only" in captured
    assert "--no-subagents" in captured
    assert "--disable-web-search" in captured
    assert "--tools" in captured
    assert "--json-schema" in captured


def test_subscription_factory_never_adds_api_provider_in_subscription_mode() -> None:
    settings = load_settings()
    configured = settings.model_copy(
        deep=True,
        update={
            "public": settings.public.model_copy(
                update={
                    "ai": settings.public.ai.model_copy(
                        update={
                            "primary_provider": "openai",
                            "primary_auth_mode": "subscription",
                        }
                    )
                }
            )
        },
    )
    providers = build_provider_chain(configured)
    assert [provider.provider_id for provider in providers] == ["openai_subscription"]
    router = build_model_router(configured)
    assert router is not None

    gemini = configured.model_copy(
        deep=True,
        update={
            "public": configured.public.model_copy(
                update={
                    "ai": configured.public.ai.model_copy(
                        update={"primary_provider": "gemini"}
                    )
                }
            )
        },
    )
    assert [provider.provider_id for provider in build_provider_chain(gemini)] == [
        "gemini_subscription"
    ]


def test_subscription_factory_preserves_one_primary_and_fallback_order() -> None:
    settings = load_settings()
    configured = settings.model_copy(
        deep=True,
        update={
            "public": settings.public.model_copy(
                update={
                    "ai": settings.public.ai.model_copy(
                        update={
                            "primary_provider": "gemini",
                            "primary_auth_mode": "subscription",
                            "fallback_providers": ("openai", "xai", "anthropic"),
                        }
                    )
                }
            )
        },
    )

    assert [provider.provider_id for provider in build_provider_chain(configured)] == [
        "gemini_subscription",
        "openai_subscription",
        "xai_subscription",
        "anthropic_subscription",
    ]


def test_environment_sanitizer_keeps_provider_cli_runtime() -> None:
    env = sanitized_environment()
    assert "PATH" in env
    assert all("API_KEY" not in key for key in env)


def test_environment_sanitizer_removes_subscription_tokens_and_google_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_ACCESS_TOKEN", "must-not-cross")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "must-not-cross")
    monkeypatch.setenv("GROK_ACCESS_TOKEN", "must-not-cross")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "credential.json")
    env = sanitized_environment()
    assert "CODEX_ACCESS_TOKEN" not in env
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "GROK_ACCESS_TOKEN" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env


async def _connected(provider_id: str):
    del provider_id
    from trading_bot.providers.access import SubscriptionHealthResult

    return SubscriptionHealthResult(
        "anthropic",
        True,
        "CONNECTED",
        "subscription_authenticated",
        "ok",
        checked_at=datetime.now(UTC),
    )
