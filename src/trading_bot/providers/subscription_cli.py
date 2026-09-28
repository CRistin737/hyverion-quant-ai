"""Provider-owned subscription CLI adapters.

The adapters deliberately use the public, documented headless commands exposed by
Codex, Claude Code, Grok, and Gemini CLI. They do not read local credential files, browser
cookies, or private web endpoints. (Plan limits are read separately by
``providers/usage.py``, which parses only the ``rate_limits`` block of Codex's logs.)
The provider CLI remains responsible for authentication; Hyverion only sends a
bounded prompt and validates the returned structured object.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from trading_bot.providers.access import SubscriptionHealthResult, check_subscription
from trading_bot.providers.base import ProviderError, ProviderResult, SchemaT
from trading_bot.providers.common import build_prompt, validated_result

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
_DEFAULT_MODEL_NAMES = {"", "default", "cli-default-profile"}


def sanitized_environment() -> dict[str, str]:
    """Return the inherited environment without API credentials."""

    return {
        key: value
        for key, value in os.environ.items()
        if key not in _SECRET_ENV_NAMES and key not in _RUNTIME_ENV_NAMES
    }


_FAILURE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "provider_auth_required",
        ("not logged in", "please log in", "please run", "login required", "sign in",
         "unauthorized", "401", "authentication", "token expired", "session expired"),
    ),
    (
        "provider_quota_exhausted",
        ("quota", "usage limit", "limit reached", "exceeded your", "out of credits",
         "insufficient credit", "billing", "weekly limit", "5-hour limit", "plan limit"),
    ),
    ("provider_rate_limited", ("rate limit", "rate_limit", "429", "too many requests")),
    ("provider_overloaded", ("overloaded", "503", "529", "capacity", "temporarily unavailable")),
)


def classify_cli_failure(text: str) -> str:
    """Map provider CLI stderr/stdout to a stable, secret-free failure code."""

    lowered = text.lower()
    for code, needles in _FAILURE_PATTERNS:
        if any(needle in lowered for needle in needles):
            return code
    return "provider_request_failed"


AUTH_CACHE_SECONDS = 60.0
_AUTH_CACHE: dict[str, tuple[float, SubscriptionHealthResult]] = {}


async def _auth_status(provider_id: str) -> SubscriptionHealthResult:
    """Login status only (no /usage, /context), cached briefly.

    Parallel agents would otherwise start several status processes per call.
    A failure is not cached, so a fresh login is picked up on the next call.
    """

    cached = _AUTH_CACHE.get(provider_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < AUTH_CACHE_SECONDS:
        return cached[1]
    status = await check_subscription(provider_id, include_usage=False)
    if status.status == "CONNECTED":
        _AUTH_CACHE[provider_id] = (now, status)
    else:
        _AUTH_CACHE.pop(provider_id, None)
    return status


class SubscriptionCLIProvider:
    """Run one provider-owned CLI in a non-interactive, read-only boundary."""

    billing_mode = "subscription"

    def __init__(self, provider_id: str) -> None:
        if provider_id not in {"openai", "anthropic", "xai", "gemini"}:
            raise ValueError(f"unsupported subscription provider: {provider_id}")
        self.provider_id = f"{provider_id}_subscription"
        self._source_provider_id = provider_id
        self._binary = {
            "openai": "codex",
            "anthropic": "claude",
            "xai": "grok",
            "gemini": "gemini",
        }[provider_id]

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
        if shutil.which(self._binary) is None:
            raise ProviderError("subscription_cli_missing", retryable=False)
        if self._source_provider_id not in {"xai", "gemini"}:
            status = await _auth_status(self._source_provider_id)
            if status.status != "CONNECTED":
                raise ProviderError(status.code, retryable=False, detail=status.detail)

        prompt = build_prompt(system_spec, context)
        started = time.monotonic()
        prefix = f"hyverion-{self._source_provider_id}-"
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            command, stdin_payload = self._command(
                directory=Path(directory),
                prompt=prompt,
                model=model,
                output_schema=output_schema,
            )
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=directory,
                    stdin=(
                        asyncio.subprocess.PIPE
                        if stdin_payload is not None
                        else asyncio.subprocess.DEVNULL
                    ),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=sanitized_environment(),
                )
            except OSError as exc:
                raise ProviderError(
                    "subscription_cli_unavailable",
                    retryable=True,
                    detail=type(exc).__name__,
                ) from exc
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(
                        stdin_payload.encode("utf-8") if stdin_payload is not None else None
                    ),
                    timeout_seconds,
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise ProviderError("provider_timeout", retryable=True) from exc
            except BaseException:
                # Cancelled (a sibling agent failed, or the cycle deadline hit):
                # never leave a CLI running and spending the subscription.
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise

        output = stdout.decode(errors="replace")
        error = stderr.decode(errors="replace")
        if process.returncode != 0:
            # Only the classified code is kept: raw CLI output can contain the
            # account e-mail or other personal data.
            raise ProviderError(classify_cli_failure(f"{error}\n{output}"), retryable=True)
        payload, input_tokens, output_tokens = _structured_payload(output, output_schema)
        return validated_result(
            raw=payload,
            schema=output_schema,
            provider=self.provider_id,
            model=model if model not in _DEFAULT_MODEL_NAMES else "cli-default-profile",
            latency_ms=int((time.monotonic() - started) * 1000),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=None,
        )

    def _command(
        self,
        *,
        directory: Path,
        prompt: str,
        model: str,
        output_schema: type[BaseModel],
    ) -> tuple[list[str], str | None]:
        schema_json = json.dumps(output_schema.model_json_schema(), separators=(",", ":"))
        model_args = [] if model in _DEFAULT_MODEL_NAMES else ["--model", model]
        if self._source_provider_id == "gemini":
            # Gemini CLI's documented JSON mode returns the model response as a
            # string envelope rather than accepting a caller-provided schema.
            # The response is parsed and validated by the same fail-closed
            # schema path below; no tool or filesystem action is enabled.
            # Gemini gets the schema in the prompt (it has no schema flag).
            return (
                [
                    "gemini",
                    "--prompt",
                    f"{prompt}\n\nJSON SCHEMA (the object must match it exactly):\n{schema_json}",
                    "--output-format",
                    "json",
                    "--approval-mode",
                    "plan",
                    "--sandbox",
                    *model_args,
                ],
                None,
            )
        if self._source_provider_id == "openai":
            schema_path = directory / "output-schema.json"
            schema_path.write_text(schema_json, encoding="utf-8")
            return (
                [
                    "codex",
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                    "--output-schema",
                    str(schema_path),
                    *model_args,
                    "-",
                ],
                prompt,
            )
        if self._source_provider_id == "anthropic":
            return (
                [
                    "claude",
                    "-p",
                    "--output-format",
                    "json",
                    "--json-schema",
                    schema_json,
                    "--permission-mode",
                    "plan",
                    "--permission-prompts",
                    "none",
                    "--no-session-persistence",
                    "--safe-mode",
                    "--tools",
                    "",
                    *model_args,
                ],
                prompt,
            )
        prompt_path = directory / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        return (
            [
                "grok",
                "--single",
                "--prompt-file",
                str(prompt_path),
                "--output-format",
                "json",
                "--json-schema",
                schema_json,
                "--permission-mode",
                "plan",
                "--sandbox",
                "read-only",
                "--no-subagents",
                "--disable-web-search",
                "--tools",
                "",
                *model_args,
            ],
            None,
        )


def _structured_payload(
    output: str,
    schema: type[BaseModel],
) -> tuple[dict[str, Any], int, int]:
    """Extract a schema object from direct JSON or documented CLI envelopes."""

    candidates: list[Any] = []
    stripped = output.strip()
    if stripped:
        try:
            candidates.append(json.loads(stripped))
        except json.JSONDecodeError:
            pass
        for line in reversed(stripped.splitlines()):
            try:
                candidates.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    seen: set[int] = set()
    for candidate in candidates:
        payload = _find_valid_payload(candidate, schema, seen)
        if payload is not None:
            usage = candidate.get("usage", {}) if isinstance(candidate, dict) else {}
            return payload, int(usage.get("input_tokens", 0) or 0), int(
                usage.get("output_tokens", 0) or 0
            )
    raise ProviderError(
        "invalid_structured_output",
        retryable=True,
        detail="subscription CLI returned no object matching the requested schema",
    )


def _find_valid_payload(
    candidate: Any,
    schema: type[BaseModel],
    seen: set[int],
) -> dict[str, Any] | None:
    if id(candidate) in seen:
        return None
    seen.add(id(candidate))
    if isinstance(candidate, str):
        try:
            return _find_valid_payload(json.loads(candidate), schema, seen)
        except json.JSONDecodeError:
            return None
    if isinstance(candidate, dict):
        try:
            schema.model_validate(candidate)
            return candidate
        except ValidationError:
            for key in (
                "result",
                "response",
                "output",
                "structured_output",
                "data",
                "content",
                "text",
            ):
                if key in candidate:
                    payload = _find_valid_payload(candidate[key], schema, seen)
                    if payload is not None:
                        return payload
    if isinstance(candidate, list):
        for item in reversed(candidate):
            payload = _find_valid_payload(item, schema, seen)
            if payload is not None:
                return payload
    return None
