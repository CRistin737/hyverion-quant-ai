"""Deterministic PAPER/LIVE readiness projection for operators.

The report is deliberately informational.  A passing PAPER report never
authorizes LIVE, and a gated check is not silently converted into a pass.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_bot.config.models import Settings
from trading_bot.schemas.observability import ReadinessCheck, ReadinessReport


def _venue_region_check(settings: Settings) -> ReadinessCheck:
    """Region is declared, never assumed eligible: brokers must be verified before LIVE."""

    region = (settings.public.trading.operating_region or "").strip().upper()
    broker = settings.public.broker.provider
    if not region:
        return ReadinessCheck(
            check_id="venue_region",
            label="Operating region",
            status="INFO",
            detail="No operating region declared; required before any LIVE review.",
            evidence=("config.trading.operating_region",),
        )
    return ReadinessCheck(
        check_id="venue_region",
        label="Operating region",
        status="PASS",
        detail=(
            f"Region {region} declared; verify {broker} live eligibility "
            "from its official terms before LIVE."
        ),
        evidence=("config.trading.operating_region", "config.broker.provider"),
    )


def build_readiness_report(
    settings: Settings,
    *,
    database_ok: bool,
    database_detail: str,
    provider_rows: list[dict[str, Any]],
    protection_ok: bool,
    protection_detail: str,
    now: datetime | None = None,
) -> ReadinessReport:
    """Build a bounded report from current deterministic state only."""

    checked_at = now or datetime.now(UTC)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("readiness timestamp must be timezone-aware")
    checked_at = checked_at.astimezone(UTC)

    trading = settings.public.trading
    paper_ok = trading.mode == "paper" and not trading.live_trading
    long_only_ok = not (
        trading.shorting_enabled
        or trading.options_trading
        or trading.allow_overnight
        or trading.premarket_trading
        or trading.after_hours_trading
    ) and trading.primary_instrument == "QQQ"
    primary = settings.public.ai.primary_provider
    fallbacks = settings.public.ai.fallback_providers
    chain_ok = (
        (primary == "disabled" and not fallbacks)
        or (
            primary != "disabled"
            and primary not in fallbacks
            and len(fallbacks) == len(set(fallbacks))
        )
    )
    configured_chain = ((primary,) if primary != "disabled" else ()) + tuple(fallbacks)
    provider_state = {
        str(row.get("provider_id")): str(
            (row.get("subscription") or {}).get("auth_state")
            if isinstance(row.get("subscription"), dict)
            else row.get("auth_state") or "UNKNOWN"
        ).upper()
        for row in provider_rows
    }
    connected = [
        provider
        for provider in configured_chain
        if provider_state.get(provider) == "CONNECTED"
    ]
    provider_detail = (
        "No AI subscription selected; provider-dependent cycles fail closed."
        if not configured_chain
        else "Connected: " + ", ".join(connected)
        if connected
        else "No selected subscription is currently verified."
    )

    checks = (
        ReadinessCheck(
            check_id="paper_mode",
            label="PAPER mode",
            status="PASS" if paper_ok else "FAIL",
            detail=(
                "PAPER is active and LIVE is disabled."
                if paper_ok
                else "PAPER must be active and live_trading must be false."
            ),
            evidence=("config.trading.mode", "config.trading.live_trading"),
        ),
        ReadinessCheck(
            check_id="long_only_session",
            label="QQQ long-only, regular session",
            status="PASS" if long_only_ok else "FAIL",
            detail=(
                "QQQ only, long-only, regular session, flat overnight, no options."
                if long_only_ok
                else "Shorting, options, overnight or extended hours are enabled; fail closed."
            ),
            evidence=("config.trading.primary_instrument", "config.trading.shorting_enabled"),
        ),
        ReadinessCheck(
            check_id="database",
            label="Control-plane database",
            status="PASS" if database_ok else "FAIL",
            detail=database_detail[:500] or "Database healthcheck failed.",
            evidence=("Database.healthcheck",),
        ),
        ReadinessCheck(
            check_id="provider_gateway",
            label="Single provider gateway",
            status="PASS" if chain_ok else "FAIL",
            detail=(
                "One primary and ordered unique fallbacks use ModelRouter."
                if chain_ok
                else "The primary/fallback chain is invalid."
            ),
            evidence=("AIConfig.validate_provider_chain", "ModelRouter"),
        ),
        ReadinessCheck(
            check_id="provider_accounts",
            label="Selected subscription accounts",
            status="INFO" if not configured_chain else "PASS" if connected else "GATED",
            detail=provider_detail,
            evidence=("/api/v1/providers", "official CLI status"),
        ),
        ReadinessCheck(
            check_id="protective_recovery",
            label="Protective-stop recovery",
            status="PASS" if protection_ok else "FAIL",
            detail=(
                "Open positions have deterministic protection."
                if protection_ok
                else protection_detail[:500] or "Protective recovery failed."
            ),
            evidence=("TradingStateRepository.protective_stop_recovery",),
        ),
        _venue_region_check(settings),
        ReadinessCheck(
            check_id="broker_mutations",
            label="Live broker orders",
            status="GATED",
            detail=(
                "Only paper brokers exist; no live broker adapter or live "
                "credential profile is present."
            ),
            evidence=("config.broker.environment", "broker/"),
        ),
        ReadinessCheck(
            check_id="live_authorization",
            label="LIVE authorization",
            status="GATED",
            detail=(
                "Independent readiness review and explicit confirm-live approval "
                "are still required."
            ),
            evidence=("main.py live", "docs/LIVE_TRADING_CHECKLIST.md"),
        ),
    )
    return ReadinessReport(
        generated_at=checked_at,
        overall=(
            "PAPER_READY"
            if not any(item.status == "FAIL" for item in checks)
            else "PAPER_BLOCKED"
        ),
        live_authorized=False,
        checks=checks,
    )
