from __future__ import annotations

from trading_bot.providers.access import login_action, subscription_login_action


def test_provider_login_actions_use_official_console_fallbacks() -> None:
    expected = {
        "anthropic": "https://console.anthropic.com/settings/keys",
        "openai": "https://platform.openai.com/api-keys",
        "xai": "https://console.x.ai/",
        "gemini": "https://aistudio.google.com/app/apikey",
    }
    for provider_id, url in expected.items():
        action = login_action(provider_id)
        assert action.supported
        assert action.browser_url == url


def test_unknown_provider_has_no_login_surface() -> None:
    action = login_action("unknown")
    assert not action.supported
    assert action.browser_url is None


def test_subscription_login_never_falls_back_to_api_console(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)
    action = subscription_login_action("gemini")
    assert not action.supported
    assert action.billing_mode == "subscription"
    assert action.browser_url is None
    assert "official gemini CLI" in action.detail


def test_subscription_login_uses_the_installed_official_cli(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/" + name)
    action = subscription_login_action("xai")
    assert action.supported
    assert action.command == ("grok", "login")
    assert action.action == "subscription_login"
