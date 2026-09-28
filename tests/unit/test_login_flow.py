from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime

import pytest

import trading_bot.providers.login_flow as login_flow
from trading_bot.providers.access import ProviderAuthAction, SubscriptionHealthResult
from trading_bot.providers.login_flow import LoginFlowManager, is_allowed_login_url


def _status(status: str, code: str = "ok"):
    async def check(provider_id: str) -> SubscriptionHealthResult:
        return SubscriptionHealthResult(
            provider_id, True, status, code, "", checked_at=datetime.now(UTC)
        )

    return check


def _fake_cli(monkeypatch, script: str) -> None:
    monkeypatch.setattr(
        login_flow,
        "subscription_login_action",
        lambda provider_id: ProviderAuthAction(
            provider_id, "subscription_login", True, (sys.executable, "-c", script)
        ),
    )


async def _settle(manager: LoginFlowManager, provider: str) -> dict:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        state = manager.status(provider)
        if state["state"] in {"connected", "failed", "manual"}:
            return state
        await asyncio.sleep(0.05)
    raise AssertionError(manager.status(provider))


def test_only_official_https_hosts_may_be_opened() -> None:
    assert is_allowed_login_url("https://claude.ai/oauth/authorize?x=1")
    assert is_allowed_login_url("https://auth.openai.com/oauth/authorize")
    assert not is_allowed_login_url("http://claude.ai/login")
    assert not is_allowed_login_url("https://claude.ai.evil.com/login")
    assert not is_allowed_login_url("https://evil.com/?u=https://claude.ai")
    assert not is_allowed_login_url("javascript:alert(1)")
    # Parser-differential tricks: a browser would open evil.com for these.
    assert not is_allowed_login_url("https://evil.com\\@claude.ai/x")
    assert not is_allowed_login_url("https://user:pw@claude.ai/")
    assert not is_allowed_login_url("https://evil.com@claude.ai/")
    assert not is_allowed_login_url("https://cl\u0430ude.ai/")  # Cyrillic homoglyph
    assert not is_allowed_login_url("https://claude.ai/\tx")
    assert is_allowed_login_url("https://claude.ai:443/oauth/authorize?state=abc")


@pytest.mark.asyncio
async def test_login_opens_link_then_verifies_connection(monkeypatch) -> None:
    _fake_cli(monkeypatch, "print('Open https://claude.ai/oauth/authorize?code=1 to sign in')")
    opened: list[str] = []

    async def opener(url: str) -> None:
        opened.append(url)

    manager = LoginFlowManager(opener=opener, status_check=_status("CONNECTED"))
    started = await manager.start("anthropic")
    assert started["state"] in {"starting", "waiting_browser"}
    done = await _settle(manager, "anthropic")
    assert done["state"] == "connected"
    assert opened == ["https://claude.ai/oauth/authorize?code=1"]
    assert done["url"] is None  # single-use link is dropped once connected


@pytest.mark.asyncio
async def test_login_that_exits_but_is_not_connected_fails_with_code(monkeypatch) -> None:
    _fake_cli(monkeypatch, "print('done')")
    manager = LoginFlowManager(status_check=_status("AUTH_REQUIRED", "subscription_auth_required"))
    await manager.start("openai")
    done = await _settle(manager, "openai")
    assert done["state"] == "failed"
    assert done["detail"] == "subscription_auth_required"


@pytest.mark.asyncio
async def test_non_official_link_is_never_opened(monkeypatch) -> None:
    _fake_cli(monkeypatch, "print('visit https://evil.example/phish now')")
    opened: list[str] = []

    async def opener(url: str) -> None:
        opened.append(url)

    manager = LoginFlowManager(opener=opener, status_check=_status("CONNECTED"))
    await manager.start("anthropic")
    done = await _settle(manager, "anthropic")
    assert opened == [] and done["url"] is None


@pytest.mark.asyncio
async def test_hung_login_times_out_and_is_killed(monkeypatch) -> None:
    _fake_cli(monkeypatch, "import time; print('waiting', flush=True); time.sleep(60)")
    manager = LoginFlowManager(status_check=_status("CONNECTED"), timeout_seconds=0.5)
    await manager.start("anthropic")
    done = await _settle(manager, "anthropic")
    assert done["detail"] == "login_timeout"
    assert manager._processes == {}


@pytest.mark.asyncio
async def test_cancel_stops_running_login(monkeypatch) -> None:
    _fake_cli(monkeypatch, "import time; print('waiting', flush=True); time.sleep(60)")
    manager = LoginFlowManager(status_check=_status("CONNECTED"))
    await manager.start("anthropic")
    await asyncio.sleep(0.3)
    cancelled = await manager.cancel("anthropic")
    assert cancelled["detail"] == "cancelled"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_gemini_needs_manual_terminal_and_unsupported_cli_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        login_flow,
        "subscription_login_action",
        lambda pid: ProviderAuthAction(pid, "subscription_login", True, ("gemini",)),
    )
    manager = LoginFlowManager()
    manual = await manager.start("gemini")
    assert manual["state"] == "manual" and manual["manual_command"] == "gemini"
    monkeypatch.setattr(
        login_flow,
        "subscription_login_action",
        lambda pid: ProviderAuthAction(pid, "subscription_login", False),
    )
    missing = await manager.start("anthropic")
    assert missing["state"] == "failed" and missing["detail"] == "cli_unavailable"


@pytest.mark.asyncio
async def test_login_environment_drops_api_keys_and_control_token(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("CONTROL_API_TOKEN", "tok")
    monkeypatch.setenv("HYVERION_SHELL_PID", "1")
    env = LoginFlowManager()._login_environment()
    assert "OPENAI_API_KEY" not in env
    assert "CONTROL_API_TOKEN" not in env and "HYVERION_SHELL_PID" not in env
