"""Provider access: official subscription CLIs, API-key health and status cards.

This module groups three boundaries that the control plane consults before any
agent call and never while routing orders:

* Subscription access is intentionally separate from API-key access. The probe
  commands below ask the provider-owned CLIs for authentication state only. They
  never read credential files, scrape browser sessions, or call undocumented
  consumer endpoints. Quota values stay unknown unless the provider emits an
  official machine-readable value.
* Provider access verification (``check_provider``) runs without exposing
  credentials.
* ``provider_statuses`` builds the redacted status cards shown by the native UI.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from trading_bot.config.models import Settings
from trading_bot.providers.capabilities import SUBSCRIPTION_PROVIDER_IDS
from trading_bot.schemas.observability import ProviderStatus, SubscriptionStatus
from trading_bot.security.secrets import KeyringSecretStore, SecretStore

# --- official login capability (formerly providers/auth.py) ----------------
# Only documented provider CLIs may be launched. This returns a plan; it never
# reads browser cookies or session tokens, nor automates consumer web login.

@dataclass(frozen=True, slots=True)
class ProviderAuthAction:
    provider_id: str
    action: str
    supported: bool
    command: tuple[str, ...] = ()
    browser_url: str | None = None
    billing_mode: str = "subscription"
    detail: str = ""


_LOGIN_COMMANDS: dict[str, tuple[str, ...]] = {
    # These commands are the provider-owned subscription login flows. They
    # open the provider's browser OAuth flow and do not accept API keys.
    "openai": ("codex", "login"),
    "anthropic": ("claude", "auth", "login"),
    # OAuth is the documented default. Device auth is selected explicitly by
    # the operator for headless environments and is not automated here.
    "xai": ("grok", "login"),
    # Gemini CLI starts its documented Sign in with Google flow from the
    # provider-owned CLI. It is not a browser-cookie or token extraction path.
    "gemini": ("gemini",),
}

# These are provider-owned consoles, never consumer session automation. Opening
# one lets the user authenticate manually and then paste an API key into the
# password field managed by the native Keychain flow.
_BROWSER_LOGIN_URLS: dict[str, str] = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
    "xai": "https://console.x.ai/",
    "gemini": "https://aistudio.google.com/app/apikey",
}


def subscription_login_action(provider_id: str) -> ProviderAuthAction:
    """Return only the provider-owned subscription login action.

    Subscription mode must never redirect the operator to an API-key console.
    If the official local CLI is missing, the caller receives an explicit
    install/authentication blocker instead of silently changing billing mode.
    """

    command = _LOGIN_COMMANDS.get(provider_id)
    if command is None:
        return ProviderAuthAction(
            provider_id=provider_id,
            action="subscription_login",
            supported=False,
            billing_mode="subscription",
            detail="This provider has no supported official subscription login flow.",
        )
    if shutil.which(command[0]) is None:
        return ProviderAuthAction(
            provider_id=provider_id,
            action="subscription_login",
            supported=False,
            command=command,
            billing_mode="subscription",
            detail=(
                f"Install the official {command[0]} CLI and sign in with the provider "
                "subscription before connecting it."
            ),
        )
    return ProviderAuthAction(
        provider_id=provider_id,
        action="subscription_login",
        supported=True,
        command=command,
        billing_mode="subscription",
        detail="Ready to launch the provider's official subscription authentication flow.",
    )


def login_action(provider_id: str) -> ProviderAuthAction:
    command = _LOGIN_COMMANDS.get(provider_id)
    browser_url = _BROWSER_LOGIN_URLS.get(provider_id)
    if command is None:
        if browser_url is not None:
            return ProviderAuthAction(
                provider_id=provider_id,
                action="official_login",
                supported=True,
                billing_mode="subscription",
                browser_url=browser_url,
                detail="Open the provider's official console to authenticate and manage API keys.",
            )
        return ProviderAuthAction(
            provider_id=provider_id,
            action="official_login",
            supported=False,
            billing_mode="subscription",
            detail="No supported local login launcher is configured; use the official API flow.",
        )
    if shutil.which(command[0]) is None:
        if browser_url is not None:
            return ProviderAuthAction(
                provider_id=provider_id,
                action="official_login",
                supported=True,
                billing_mode="subscription",
                browser_url=browser_url,
                detail=(
                    "The official CLI is unavailable; open the provider's official console instead."
                ),
            )
        return ProviderAuthAction(
            provider_id=provider_id,
            action="official_login",
            supported=False,
            billing_mode="subscription",
            command=command,
            browser_url=browser_url,
            detail=f"Install the official {command[0]} CLI before launching login.",
        )
    return ProviderAuthAction(
        provider_id=provider_id,
        action="official_login",
        supported=True,
        billing_mode="subscription",
        command=command,
        browser_url=browser_url,
        detail="Ready to launch the provider's official local authentication flow.",
    )


def api_test_action(provider_id: str, configured: bool) -> ProviderAuthAction:
    if not configured:
        return ProviderAuthAction(
            provider_id=provider_id,
            action="api_connection_test",
            supported=False,
            billing_mode="api",
            detail="No API credential is configured in the secret backend.",
        )
    return ProviderAuthAction(
        provider_id=provider_id,
        action="api_connection_test",
        supported=True,
        billing_mode="api",
        detail=(
            "Credential presence is confirmed; a provider-specific network test is "
            "required before use."
        ),
    )

# --- Official subscription CLI boundaries -----------------------------------

_COMMANDS: dict[str, tuple[str, ...]] = {
    "openai": ("codex", "login", "status"),
    "anthropic": ("claude", "auth", "status"),
    # Grok's official CLI exposes OAuth login but does not document a
    # non-interactive account/quota status command. ``--version`` is therefore
    # only an installation probe; subscription access is verified by a bounded
    # read-only invocation when the router actually selects this provider.
    "xai": ("grok", "--version"),
    # Gemini CLI exposes Google-account OAuth login and headless execution but
    # no documented machine-readable account/quota status command. ``--version``
    # is therefore only an installation probe; access is verified on invoke.
    "gemini": ("gemini", "--version"),
}

_SECRET_ENV_NAMES = {
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "GROK_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
}
_RUNTIME_ENV_NAMES = {
    "CODEX_CI",
    "CODEX_ACCESS_TOKEN",
    "CODEX_SESSION_ID",
    "CODEX_THREAD_ID",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GROK_SESSION_ID",
    "GROK_ACCESS_TOKEN",
}


@dataclass(frozen=True, slots=True)
class SubscriptionHealthResult:
    provider_id: str
    supported: bool
    status: str
    code: str
    detail: str
    plan: str | None = None
    source: str = "OFFICIAL_CLI"
    context_remaining_percent: Decimal | None = None
    context_used_tokens: str | None = None
    context_window_tokens: str | None = None
    context_model: str | None = None
    session_remaining: Decimal | None = None
    four_hour_remaining: Decimal | None = None
    weekly_remaining: Decimal | None = None
    session_reset_label: str | None = None
    weekly_reset_label: str | None = None
    usage_detail: str | None = None
    checked_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def subscription_command(provider_id: str) -> tuple[str, ...] | None:
    """Return the provider-owned read-only status command, if supported."""

    command = _COMMANDS.get(provider_id)
    if command is None or shutil.which(command[0]) is None:
        return None
    return command


def subscription_login_available(provider_id: str) -> bool:
    return provider_id in _COMMANDS and shutil.which(_COMMANDS[provider_id][0]) is not None


async def check_subscription(
    provider_id: str,
    *,
    timeout_seconds: float = 8,
    include_usage: bool = True,
) -> SubscriptionHealthResult:
    """Official status command; ``include_usage`` also reads Claude's /usage and /context."""

    command = subscription_command(provider_id)
    checked_at = datetime.now(UTC)
    if command is None:
        return SubscriptionHealthResult(
            provider_id=provider_id,
            supported=provider_id in _COMMANDS,
            status="UNSUPPORTED" if provider_id not in _COMMANDS else "AUTH_REQUIRED",
            code="subscription_cli_missing",
            detail="Install the provider's official CLI and sign in with its subscription flow.",
            checked_at=checked_at,
        )

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _SECRET_ENV_NAMES and key not in _RUNTIME_ENV_NAMES
    }
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except TimeoutError:
        if process is not None:
            process.kill()
            await process.wait()
        return SubscriptionHealthResult(
            provider_id,
            True,
            "DEGRADED",
            "subscription_status_timeout",
            "The official subscription status command timed out.",
            checked_at=checked_at,
        )
    except OSError as exc:
        return SubscriptionHealthResult(
            provider_id,
            True,
            "DEGRADED",
            "subscription_cli_failed",
            type(exc).__name__,
            checked_at=checked_at,
        )

    output = (stdout + stderr).decode(errors="replace")[:16000]
    returncode = process.returncode if process.returncode is not None else 1
    if provider_id == "openai":
        return _parse_codex_status(output, returncode, checked_at)
    if provider_id == "anthropic":
        result = _parse_claude_status(output, returncode, checked_at)
        if result.status == "CONNECTED" and include_usage:
            usage = await _check_claude_usage(timeout_seconds)
            context = await _check_claude_context(timeout_seconds)
            if usage is not None or context is not None:
                usage = usage or {}
                context = context or {}
                return SubscriptionHealthResult(
                    provider_id=result.provider_id,
                    supported=result.supported,
                    status=result.status,
                    code=result.code,
                    detail=result.detail,
                    plan=result.plan,
                    source=result.source,
                    session_remaining=usage.get("session_remaining"),
                    weekly_remaining=usage.get("weekly_remaining"),
                    context_remaining_percent=context.get("context_remaining_percent"),
                    context_used_tokens=context.get("context_used_tokens"),
                    context_window_tokens=context.get("context_window_tokens"),
                    context_model=context.get("context_model"),
                    session_reset_label=usage.get("session_reset_label"),
                    weekly_reset_label=usage.get("weekly_reset_label"),
                    usage_detail=usage.get("usage_detail"),
                    checked_at=checked_at,
                )
        return result
    return SubscriptionHealthResult(
        provider_id,
        True,
        "UNKNOWN",
        "subscription_status_unavailable",
        (
            "The official provider CLI does not expose a machine-readable subscription "
            "status command; access is verified when a bounded request runs."
        ),
        checked_at=checked_at,
    )


def _parse_codex_status(
    output: str,
    returncode: int,
    checked_at: datetime,
) -> SubscriptionHealthResult:
    if "logged in using chatgpt" in output.lower():
        return SubscriptionHealthResult(
            "openai",
            True,
            "CONNECTED",
            "subscription_authenticated",
            "Codex is authenticated with ChatGPT subscription access.",
            checked_at=checked_at,
        )
    if "logged in using api key" in output.lower():
        return SubscriptionHealthResult(
            "openai",
            True,
            "AUTH_REQUIRED",
            "api_key_session_detected",
            "Codex is using API-key authentication; sign in with ChatGPT to use the subscription.",
            checked_at=checked_at,
        )
    return SubscriptionHealthResult(
        "openai",
        True,
        "AUTH_REQUIRED" if returncode != 0 else "UNKNOWN",
        "subscription_status_unrecognized",
        "Codex returned no recognized subscription status.",
        checked_at=checked_at,
    )


def _parse_claude_status(
    output: str,
    returncode: int,
    checked_at: datetime,
) -> SubscriptionHealthResult:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        payload = {}
    if isinstance(payload, dict) and payload.get("loggedIn") is True:
        auth_method = str(payload.get("authMethod") or "")
        plan = str(payload.get("subscriptionType") or "").strip() or None
        if auth_method == "claude.ai":
            return SubscriptionHealthResult(
                "anthropic",
                True,
                "CONNECTED",
                "subscription_authenticated",
                "Claude Code is authenticated with claude.ai subscription access.",
                plan=plan,
                checked_at=checked_at,
            )
        return SubscriptionHealthResult(
            "anthropic",
            True,
            "AUTH_REQUIRED",
            "api_session_detected",
            "Claude Code is authenticated without a claude.ai subscription session.",
            checked_at=checked_at,
        )
    return SubscriptionHealthResult(
        "anthropic",
        True,
        "AUTH_REQUIRED" if returncode != 0 else "UNKNOWN",
        "subscription_status_unrecognized",
        "Claude Code returned no recognized subscription status.",
        checked_at=checked_at,
    )


async def _check_claude_usage(timeout_seconds: float) -> dict[str, Any] | None:
    """Read Claude Code's documented local ``/usage`` command.

    This is deliberately separate from auth status. If a future CLI removes or
    changes ``/usage``, authentication remains visible and quota falls back to
    UNKNOWN instead of blocking all provider access.
    """

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _SECRET_ENV_NAMES and key not in _RUNTIME_ENV_NAMES
    }
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            "/usage",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--safe-mode",
            "--permission-mode",
            "plan",
            "--permission-prompts",
            "none",
            "--tools",
            "",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except (TimeoutError, OSError):
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return None
    if process.returncode != 0:
        return None
    try:
        payload = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return None
    text = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        return None
    session_match = re.search(
        r"Current session:\s*(\d+(?:\.\d+)?)%\s+used\s+·\s+resets\s+(.+)",
        text,
    )
    weekly_match = re.search(
        r"Current week \(all models\):\s*(\d+(?:\.\d+)?)%\s+used\s+·\s+resets\s+(.+)",
        text,
    )
    if session_match is None and weekly_match is None:
        return None

    def remaining(match: re.Match[str] | None) -> Decimal | None:
        if match is None:
            return None
        return max(Decimal("0"), Decimal("100") - Decimal(match.group(1)))

    session_remaining = remaining(session_match)
    weekly_remaining = remaining(weekly_match)
    details: list[str] = []
    if session_remaining is not None:
        details.append(f"session {session_remaining}% remaining")
    if weekly_remaining is not None:
        details.append(f"week {weekly_remaining}% remaining")
    return {
        "session_remaining": session_remaining,
        "weekly_remaining": weekly_remaining,
        "session_reset_label": session_match.group(2).strip() if session_match else None,
        "weekly_reset_label": weekly_match.group(2).strip() if weekly_match else None,
        "usage_detail": "Official Claude Code /usage: " + ", ".join(details),
    }


async def _check_claude_context(timeout_seconds: float) -> dict[str, Any] | None:
    """Read Claude Code's official zero-turn ``/context`` command."""

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _SECRET_ENV_NAMES and key not in _RUNTIME_ENV_NAMES
    }
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "claude",
            "-p",
            "/context",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--safe-mode",
            "--permission-mode",
            "plan",
            "--permission-prompts",
            "none",
            "--tools",
            "",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        stdout, _stderr = await asyncio.wait_for(process.communicate(), timeout_seconds)
    except (TimeoutError, OSError):
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return None
    if process.returncode != 0:
        return None
    try:
        payload = json.loads(stdout.decode(errors="replace"))
    except json.JSONDecodeError:
        return None
    text = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        return None
    model_match = re.search(r"\*\*Model:\*\*\s*([^\n]+)", text)
    token_match = re.search(
        r"\*\*Tokens:\*\*\s*([\d.]+[kKmM]?)\s*/\s*([\d.]+[kKmM]?)\s*\(([\d.]+)%\)",
        text,
    )
    free_match = re.search(r"\|\s*Free space\s*\|[^|]+\|\s*([\d.]+)%\s*\|", text)
    if token_match is None and free_match is None:
        return None
    if free_match is not None:
        remaining = Decimal(free_match.group(1))
    elif token_match is not None:
        remaining = max(Decimal("0"), Decimal("100") - Decimal(token_match.group(3)))
    else:
        return None
    return {
        "context_remaining_percent": remaining,
        "context_used_tokens": token_match.group(1) if token_match else None,
        "context_window_tokens": token_match.group(2) if token_match else None,
        "context_model": model_match.group(1).strip() if model_match else None,
    }


# --- API-key provider health ------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderHealthResult:
    provider_id: str
    supported: bool
    status: str
    code: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


async def check_provider(
    provider_id: str,
    api_key: str | None,
    *,
    timeout_seconds: float = 10,
) -> ProviderHealthResult:
    if not api_key or not api_key.strip():
        return ProviderHealthResult(
            provider_id,
            False,
            "NOT_CONFIGURED",
            "credential_missing",
            "Configure an API key first.",
        )
    if provider_id in {"openai", "xai"}:
        return await _check_openai_compatible(provider_id, api_key, timeout_seconds)
    if provider_id in {"anthropic", "gemini"}:
        return await _check_http_models(provider_id, api_key, timeout_seconds)
    return ProviderHealthResult(
        provider_id,
        False,
        "UNSUPPORTED",
        "provider_not_supported",
        "No official healthcheck adapter exists for this provider.",
    )


async def _check_http_models(
    provider_id: str,
    api_key: str,
    timeout_seconds: float,
) -> ProviderHealthResult:
    if provider_id == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        headers = {"x-goog-api-key": api_key}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            response = await client.get(url, headers=headers, params={"pageSize": "1"})
    except httpx.TimeoutException:
        return ProviderHealthResult(
            provider_id, True, "DEGRADED", "provider_timeout", "Provider healthcheck timed out."
        )
    except httpx.HTTPError as exc:
        return ProviderHealthResult(
            provider_id, True, "DEGRADED", "provider_request_failed", type(exc).__name__
        )
    if response.status_code in {401, 403}:
        return ProviderHealthResult(
            provider_id, True, "EXPIRED", "credential_rejected", "Provider rejected the credential."
        )
    if response.status_code == 429:
        return ProviderHealthResult(
            provider_id,
            True,
            "RATE_LIMITED",
            "provider_rate_limited",
            "Provider rate limit reached.",
        )
    if response.is_success:
        return ProviderHealthResult(
            provider_id, True, "CONNECTED", "healthcheck_ok", "Official models endpoint responded."
        )
    return ProviderHealthResult(
        provider_id,
        True,
        "DEGRADED",
        "provider_request_failed",
        f"Provider returned HTTP {response.status_code}.",
    )


async def _check_openai_compatible(
    provider_id: str,
    api_key: str,
    timeout_seconds: float,
) -> ProviderHealthResult:
    try:
        module = importlib.import_module("openai")
    except ImportError:
        return ProviderHealthResult(
            provider_id,
            False,
            "DEGRADED",
            "provider_sdk_not_installed",
            "Install the optional provider SDK before testing access.",
        )
    base_url = "https://api.x.ai/v1" if provider_id == "xai" else None
    try:
        client_kwargs = {"api_key": api_key}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        client = module.AsyncOpenAI(**client_kwargs)
        await asyncio.wait_for(client.models.list(), timeout_seconds)
    except TimeoutError:
        return ProviderHealthResult(
            provider_id, True, "DEGRADED", "provider_timeout", "Provider healthcheck timed out."
        )
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code == 401:
            status = "EXPIRED"
            code = "credential_rejected"
        elif status_code == 429:
            status = "RATE_LIMITED"
            code = "provider_rate_limited"
        else:
            status = "DEGRADED"
            code = "provider_request_failed"
        return ProviderHealthResult(provider_id, True, status, code, type(exc).__name__)
    return ProviderHealthResult(
        provider_id, True, "CONNECTED", "healthcheck_ok", "Official models endpoint responded."
    )


# --- Provider status cards --------------------------------------------------

_PROVIDERS = (
    ("anthropic", "Claude", "anthropic_api_key", "api"),
    ("openai", "OpenAI / Codex", "openai_api_key", "api"),
    ("xai", "Grok / xAI", "xai_api_key", "api"),
    ("gemini", "Gemini", "gemini_api_key", "api"),
)
_SUBSCRIPTION_PROVIDERS = SUBSCRIPTION_PROVIDER_IDS


def provider_statuses(
    settings: Settings,
    *,
    secret_store: SecretStore | None = None,
    health_overrides: Mapping[str, Mapping[str, object]] | None = None,
    subscription_overrides: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[ProviderStatus, ...]:
    configured = settings.public.ai.primary_provider
    store = secret_store or KeyringSecretStore()
    statuses: list[ProviderStatus] = []
    for provider_id, display_name, secret_name, billing_mode in _PROVIDERS:
        secret = getattr(settings.secrets, secret_name)
        env_configured = bool(secret and secret.get_secret_value().strip())
        keychain_configured = store.has(f"provider:{provider_id}:api_key")
        api_configured = env_configured or keychain_configured
        login_capability = (
            subscription_login_action(provider_id)
            if settings.public.ai.primary_auth_mode == "subscription"
            else login_action(provider_id)
        )
        cli_available = login_capability.supported
        enabled = provider_id == configured or provider_id in settings.public.ai.fallback_providers
        if api_configured:
            # Presence is not proof of a successful provider call. Keep this state
            # explicitly recoverable until the provider-specific healthcheck runs.
            auth_state = "AVAILABLE"
        elif enabled or cli_available or (
            settings.public.ai.primary_auth_mode == "subscription"
            and provider_id in SUBSCRIPTION_PROVIDER_IDS
        ):
            # Official login availability is an action capability, not proof
            # that the account is authenticated. Keep the connection state
            # explicit until a key is present and healthcheck succeeds.
            auth_state = "AUTH_REQUIRED"
        else:
            auth_state = "DISABLED"
        override = (health_overrides or {}).get(provider_id)
        if override is not None:
            candidate = str(override.get("status") or "").upper()
            allowed_states = {
                "CONNECTED",
                "AVAILABLE",
                "NOT_CONFIGURED",
                "AUTH_REQUIRED",
                "EXPIRED",
                "RATE_LIMITED",
                "DEGRADED",
                "DISABLED",
                "ERROR",
                "UNSUPPORTED",
                "UNKNOWN",
            }
            if candidate in allowed_states:
                auth_state = candidate
        limits = settings.public.ai.provider_limits.get(provider_id)
        configured_limit_values = (
            limits is not None
            and any(
                value is not None
                for value in (
                    limits.context_window,
                    limits.session_limit,
                    limits.four_hour_limit,
                    limits.weekly_limit,
                )
            )
        )
        # A provider entry can exist in YAML purely as a documented slot. Do not
        # label an all-null slot CONFIGURED: that would imply a verified quota.
        limit_source = (
            limits.source if limits is not None and configured_limit_values else "UNKNOWN"
        )
        subscription = _subscription_status(
            provider_id,
            (subscription_overrides or {}).get(provider_id),
        )
        if settings.public.ai.primary_auth_mode == "subscription" and override is None:
            # The nested subscription probe is the authoritative connection
            # state in this mode. Keeping the top-level card state aligned
            # prevents the UI from showing AUTH_REQUIRED beside a verified
            # CONNECTED subscription.
            auth_state = subscription.auth_state
        statuses.append(
            ProviderStatus(
                provider_id=provider_id,
                display_name=display_name,
                api_configured=api_configured,
                official_login_available=cli_available,
                auth_state=auth_state,
                billing_mode=(
                    settings.public.ai.primary_auth_mode
                    if settings.public.ai.primary_auth_mode in {"api", "subscription"}
                    else billing_mode
                ),
                usage_state="UNKNOWN",
                context_window=limits.context_window if limits else None,
                session_limit=limits.session_limit if limits else None,
                four_hour_limit=limits.four_hour_limit if limits else None,
                weekly_limit=limits.weekly_limit if limits else None,
                limit_source=limit_source,
                subscription=subscription,
                detail=(
                    str(override.get("detail"))
                    if override is not None and override.get("detail")
                    else "API key configured; run the provider healthcheck to verify access."
                    if env_configured
                    else (
                        "API key reference is present; run the provider healthcheck "
                        "to verify access."
                    )
                    if keychain_configured
                    else login_capability.detail
                ),
            )
        )
    return tuple(statuses)


def _subscription_status(
    provider_id: str,
    override: Mapping[str, object] | None,
) -> SubscriptionStatus:
    cli_available = subscription_login_available(provider_id)
    supported = provider_id in _SUBSCRIPTION_PROVIDERS
    if override is None:
        return SubscriptionStatus(
            supported=supported,
            cli_available=cli_available,
            auth_state="AUTH_REQUIRED" if supported else "UNSUPPORTED",
            detail=(
                "Run the official subscription status check."
                if supported and cli_available
                else "Install the provider's official subscription CLI to connect this account."
                if supported
                else "No supported subscription CLI is configured for this provider."
            ),
        )
    status = str(override.get("status") or "UNKNOWN").upper()
    allowed = {"CONNECTED", "AUTH_REQUIRED", "DEGRADED", "UNKNOWN", "UNSUPPORTED"}
    auth_state = status if status in allowed else "UNKNOWN"
    last_checked = override.get("checked_at")
    if isinstance(last_checked, str):
        try:
            last_checked = datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
        except ValueError:
            last_checked = None
    return SubscriptionStatus(
        supported=bool(override.get("supported", supported)),
        cli_available=cli_available,
        auth_state=auth_state,
        plan=str(override.get("plan")) if override.get("plan") else None,
        usage_state=(
            "AVAILABLE"
            if any(
                override.get(key) is not None
                for key in (
                    "context_remaining_percent",
                    "session_remaining",
                    "four_hour_remaining",
                    "weekly_remaining",
                )
            )
            else "UNKNOWN"
        ),
        context_remaining_percent=override.get("context_remaining_percent"),
        context_used_tokens=(
            str(override.get("context_used_tokens"))
            if override.get("context_used_tokens")
            else None
        ),
        context_window_tokens=(
            str(override.get("context_window_tokens"))
            if override.get("context_window_tokens")
            else None
        ),
        context_model=(
            str(override.get("context_model")) if override.get("context_model") else None
        ),
        session_remaining=override.get("session_remaining"),
        four_hour_remaining=override.get("four_hour_remaining"),
        weekly_remaining=override.get("weekly_remaining"),
        session_reset_label=(
            str(override.get("session_reset_label"))
            if override.get("session_reset_label")
            else None
        ),
        weekly_reset_label=(
            str(override.get("weekly_reset_label"))
            if override.get("weekly_reset_label")
            else None
        ),
        usage_detail=(
            str(override.get("usage_detail")) if override.get("usage_detail") else None
        ),
        limit_source=(
            str(override.get("source"))
            if str(override.get("source")) in {"OFFICIAL_CLI", "OFFICIAL_API"}
            else "UNKNOWN"
        ),
        last_checked_at=last_checked if hasattr(last_checked, "tzinfo") else None,
        detail=str(override.get("detail") or "Subscription status checked."),
    )


def provider_ids() -> Iterable[str]:
    return (item[0] for item in _PROVIDERS)
