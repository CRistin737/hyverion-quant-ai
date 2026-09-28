"""Internal control API used by the native terminal and future remote clients.

This module deliberately exposes JSON state and commands only.  The product UI
is native; the API is a transport boundary for the local engine and a future
VPS deployment, not a browser dashboard.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic
from typing import Any, Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from trading_bot.agents.registry import AgentRegistry
from trading_bot.broker.base import BrokerUnavailable
from trading_bot.broker.lifecycle import OperationState
from trading_bot.broker.readonly import BrokerReader, reconcile_broker
from trading_bot.config import load_settings
from trading_bot.config.loader import live_ai_models, shares_app_state
from trading_bot.config.manager import ConfigManager, ConfigPatch
from trading_bot.config.models import AI_ROLES, Settings
from trading_bot.config.risk_profiles import RISK_PROFILES
from trading_bot.core.clock import SystemClock
from trading_bot.core.instruments import INSTRUMENTS
from trading_bot.core.operator_resolution import (
    OperatorResolutionError,
    OperatorResolutionService,
)
from trading_bot.core.trade_view import build_trades, filter_trades, rejected_entries
from trading_bot.data.sources import default_source_statuses
from trading_bot.db.backup import create_sqlite_backup, sqlite_path
from trading_bot.db.database import Database
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository, position_recency
from trading_bot.engine_supervisor import (
    DEFAULT_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    EngineStartError,
    EngineSupervisor,
    clear_flatten,
    engine_data_dir,
    flatten_pending,
    request_flatten,
)
from trading_bot.intelligence.hub import SOURCE_TEST_STEPS, IntelligenceHub
from trading_bot.intelligence.reader import sources_status, stored_intelligence
from trading_bot.learning.deployer import ChangeDeployer, baselines
from trading_bot.learning.metrics import summarize_evaluations
from trading_bot.learning.optimizer_job import last_run, optimizer_lock, run_optimizer
from trading_bot.learning.proposals import latest_proposal, proposal_timeline
from trading_bot.learning.requests import write_feature_request
from trading_bot.learning.supervisor import LearningSupervisor
from trading_bot.learning.versions import ChangeNotApplicable, VersionRepository, window_metrics
from trading_bot.market.clock import MarketClock, regular_session_only, trading_day
from trading_bot.market_data import MarketDataUnavailable, build_market_data
from trading_bot.market_data.alpaca import AlpacaMarketData
from trading_bot.memory.cycle import run_memory_cycle
from trading_bot.memory.models import KnowledgeStatus
from trading_bot.memory.operations import (
    MemoryDecisionRequest,
    MemoryHealthService,
    MemoryOperations,
    MemoryOperatorError,
    memory_summary,
)
from trading_bot.memory.vault import FileVaultRepository, VaultError
from trading_bot.memory.vault_view import (
    MAX_OWNER_NOTES_CHARS,
    read_note,
    vault_overview,
    write_owner_notes,
)
from trading_bot.monitoring.alerts import project_operational_alerts
from trading_bot.monitoring.plan_audit import build_plan_audit
from trading_bot.monitoring.readiness import build_readiness_report
from trading_bot.monitoring.retention import apply_retention
from trading_bot.providers.access import (
    api_test_action,
    check_provider,
    check_subscription,
    login_action,
    provider_statuses,
)
from trading_bot.providers.base import ProviderError
from trading_bot.providers.capabilities import SUBSCRIPTION_PROVIDER_IDS
from trading_bot.providers.catalog import ROLE_LABELS, catalog_payload
from trading_bot.providers.factory import build_model_router
from trading_bot.providers.login_flow import LoginFlowManager, sign_out
from trading_bot.providers.usage import UsageCache
from trading_bot.reports.campaign import daily_report, live_readiness
from trading_bot.reports.period import period_report, trades_csv
from trading_bot.schemas.learning import (
    BacktestRunRequest,
    ChangeProposalRequest,
    ChangeProposalTransitionRequest,
    ReviewReasonRequest,
)
from trading_bot.schemas.observability import ProviderProbeOutput
from trading_bot.schemas.trading import Candle, OperationResolutionRequest
from trading_bot.security.credentials import alpaca_paper_credentials
from trading_bot.security.secrets import KeyringSecretStore
from trading_bot.service import launchd
from trading_bot.simulation.backtest import BacktestEngine, BacktestResult, WalkForwardResult
from trading_bot.simulation.costs import SIMULATION_CAPITAL_USD
from trading_bot.simulation.strategy_replay import (
    MIN_CANDLES,
    compare_champion_challenger,
    replay_strategies,
)
from trading_bot.sources.runtime import build_fetcher
from trading_bot.strategies.base import ExitParams
from trading_bot.strategies.registry import exit_defaults, strategy_factories, strategy_factory


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return cast(dict[str, Any], payload) if isinstance(payload, dict) else {}


def _json_value(value: Any) -> Any:
    """Convert SQLAlchemy scalar values into values safe for the native client."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _record(row: dict[str, Any]) -> dict[str, Any]:
    """Flatten an audit row while preserving provenance metadata."""

    payload = _payload(row)
    record = dict(payload)
    for field in ("id", "asset", "event_time", "received_time", "processed_time", "created_at"):
        if field not in record and row.get(field) is not None:
            record[field] = row[field]
    return cast(dict[str, Any], _json_value(record))


def _unique_candles(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Oldest-first candles with one entry per close time (newest row wins)."""

    by_close: dict[str, dict[str, Any]] = {}
    for row in rows:  # newest first
        payload = _payload(row)
        key = str(payload.get("event_time") or row.get("event_time") or "")
        if key and key not in by_close:
            by_close[key] = payload
    return [by_close[key] for key in sorted(by_close)]


def _latest_position_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Latest projection per position_id, newest first."""

    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = _record(row)
        position_id = str(record.get("position_id") or record.get("id") or "")
        if not position_id:
            continue
        current = latest.get(position_id)
        if current is None or position_recency(record) > position_recency(current):
            latest[position_id] = record
    return sorted(latest.values(), key=position_recency, reverse=True)


def _provider_overrides_from_events(
    event_rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    """Project the latest persisted provider checks after an API restart.

    The native process keeps an in-memory view for immediate feedback, but the
    control plane is also expected to explain the last known provider state
    after a restart.  Only the small, sanitized result emitted by the official
    health/subscription adapter is replayed; credentials never enter this
    projection.
    """

    health: dict[str, dict[str, object]] = {}
    subscriptions: dict[str, dict[str, object]] = {}
    for row in event_rows:  # repository order is newest first
        payload = _payload(row)
        event_type = str(payload.get("event_type") or "")
        provider_id = str(payload.get("provider_id") or row.get("asset") or "")
        result = payload.get("result")
        if not provider_id or not isinstance(result, dict):
            continue
        if event_type == "PROVIDER_HEALTH_CHECK" and provider_id not in health:
            health[provider_id] = cast(dict[str, object], result)
        elif event_type == "PROVIDER_SUBSCRIPTION_CHECK" and provider_id not in subscriptions:
            subscriptions[provider_id] = cast(dict[str, object], result)
    return health, subscriptions


def _latest_provider_circuits(event_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Newest engine-published breaker state, keyed by provider id (no ``_subscription``)."""

    for row in event_rows:  # newest first
        payload = _payload(row)
        if payload.get("status") != "PROVIDER_CIRCUIT":
            continue
        raw = payload.get("circuits")
        if not isinstance(raw, dict):
            return {}
        return {
            str(key).removesuffix("_subscription"): cast(dict[str, Any], value)
            for key, value in raw.items()
            if isinstance(value, dict)
        }
    return {}


async def _persist_provider_check(
    repository: AuditRepository,
    *,
    event_type: str,
    provider_id: str,
    result: dict[str, Any],
) -> None:
    """Persist a provider check as sanitized audit metadata."""

    now = datetime.now(UTC)
    await repository.append(
        "system_events",
        {
            "event_type": event_type,
            "provider_id": provider_id,
            "result": cast(dict[str, object], _json_value(result)),
        },
        created_at=now,
        event_time=now,
        received_time=now,
        processed_time=now,
        asset=provider_id,
    )


def _public_risk(settings: Settings) -> dict[str, Any]:
    """Read-only view of the deterministic risk limits for the desktop UI."""

    risk = settings.public.risk
    limits = risk.model_dump(mode="json", exclude={"ladder"})
    limits["ladder"] = [level.model_dump(mode="json") for level in risk.ladder]
    return limits


def _public_config(settings: Settings) -> dict[str, Any]:
    trading = settings.public.trading
    return {
        "mode": trading.mode,
        "asset_class": trading.asset_class,
        "primary_instrument": trading.primary_instrument,
        "live_trading": trading.live_trading,
        "shadow_trading": trading.shadow_trading,
        "allowed_symbols": list(trading.allowed_symbols),
        "operating_region": trading.operating_region,
        "broker": settings.public.broker.provider,
        "broker_environment": settings.public.broker.environment,
        "market_data_provider": settings.public.market_data.provider,
        "market_data_feed": settings.public.market_data.feed,
        "ai_provider": settings.public.ai.primary_provider,
        "ai_auth_mode": settings.public.ai.primary_auth_mode,
        "ai_fallback_providers": list(settings.public.ai.fallback_providers),
        "ai_provider_chain": [
            settings.public.ai.primary_provider,
            *settings.public.ai.fallback_providers,
        ]
        if settings.public.ai.primary_provider != "disabled"
        else [],
        "ai_provider_policy": {
            "single_primary": True,
            "primary": (
                settings.public.ai.primary_provider
                if settings.public.ai.primary_provider != "disabled"
                else None
            ),
            "ordered_fallbacks": list(settings.public.ai.fallback_providers),
            "authentication": settings.public.ai.primary_auth_mode,
            "gateway": "ModelRouter",
            "fail_closed_when_exhausted": True,
        },
        "ai_models": live_ai_models(settings).model_dump(mode="json"),
        "strategies": {
            "enabled": list(settings.public.strategies.enabled),
            "signal_weights_version": settings.public.strategies.signal_weights_version,
            "signal_weights": {
                name: str(value)
                for name, value in settings.public.strategies.signal_weights.items()
            },
        },
        "risk": _public_risk(settings),
        "risk_profiles": {
            name: {key: str(value) for key, value in profile.as_values().items()}
            for name, profile in RISK_PROFILES.items()
        },
        "autonomy": {
            "mode": settings.public.autonomy.mode,
            "auto_promote_paper": settings.public.autonomy.auto_promote_paper,
            "shadow_sessions_before_promotion": (
                settings.public.autonomy.shadow_sessions_before_promotion
            ),
            "envelope": {
                name: {"minimum": str(bound.minimum), "maximum": str(bound.maximum)}
                for name, bound in settings.public.autonomy.envelope.items()
            },
        },
        "external_data": {
            "news": settings.public.external_data.news_provider,
            "social": settings.public.external_data.social_provider,
            "news_feeds": dict(settings.public.external_data.news_feeds),
            "news_reviewed_sources": list(settings.public.external_data.news_reviewed_sources),
            "collection_interval_seconds": (
                settings.public.external_data.collection_interval_seconds
            ),
            "collection_max_backoff_seconds": (
                settings.public.external_data.collection_max_backoff_seconds
            ),
        },
    }


def _current_era(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop crypto-era rows (pairs like "BTC/USDT"); no equity symbol has a "/"."""

    return [row for row in rows if "/" not in str(row.get("asset") or "")]


# Matches the agent pipeline deadline: no run can legitimately last longer.
AGENT_RUN_MAX_AGE = timedelta(seconds=240)


def _younger_than(value: object, age: timedelta) -> bool:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return False
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return datetime.now(UTC) - value < age


async def build_snapshot(
    database: Database,
    settings: Settings,
    health_overrides: dict[str, dict[str, object]] | None = None,
    subscription_overrides: dict[str, dict[str, object]] | None = None,
    *,
    engine_running: bool | None = None,
) -> dict[str, Any]:
    """Build one bounded, JSON-safe snapshot for the desktop client.

    ``engine_running=False`` means no agent can be working, whatever the run
    table says; ``None`` (unknown) falls back to the run's age alone.
    """

    repository = AuditRepository(database)
    market_rows = await repository.recent("market_snapshots", limit=240)
    market_history: dict[str, list[dict[str, Any]]] = {}
    for row in reversed(market_rows):
        payload = _payload(row)
        symbol = str(payload.get("symbol") or row.get("asset") or "UNKNOWN")
        # Historical rows for retired (crypto) symbols stay in the audit log only.
        if symbol not in INSTRUMENTS:
            continue
        market_history.setdefault(symbol, []).append(payload)

    markets: list[dict[str, Any]] = []
    for symbol, history in market_history.items():
        latest = history[-1].copy()
        latest["history"] = history[-60:]
        # The engine re-appends the last closed candles every cycle, so read a
        # per-symbol window and keep one candle per close time.
        candle_rows = await repository.recent("candles", limit=500, asset=symbol)
        latest["candles"] = _unique_candles(candle_rows)[-120:]
        markets.append(latest)
    markets.sort(key=lambda item: str(item.get("symbol", "")))

    risk_rows = _current_era(await repository.recent("risk_decisions", limit=20))[:1]
    proposal_rows = _current_era(await repository.recent("trade_proposals", limit=20))[:8]
    order_rows = await repository.recent("orders", limit=12)
    fill_rows = await repository.recent("fills", limit=80)
    # Positions are append-only (one row per mark); consolidate per position_id.
    position_rows = await repository.recent("positions", limit=500)
    shadow_rows = _current_era(await repository.recent("shadow_trades", limit=20))
    change_rows = await repository.recent("change_proposals", limit=20)
    experiment_rows = _current_era(await repository.recent("experiments", limit=20))
    evaluation_rows = _current_era(await repository.recent("trade_evaluations", limit=20))
    event_rows = _current_era(await repository.recent("system_events", limit=60))[:40]
    lifecycle = OperationLifecycleRepository(database)
    operation_rows = await lifecycle.recent(limit=50)
    unresolved_operations = await lifecycle.count_unresolved()
    persisted_health, persisted_subscription = _provider_overrides_from_events(event_rows)
    # Explicit process-local results are fresher than the last persisted event;
    # persisted values provide restart continuity when those dictionaries are
    # empty in a newly-created API process.
    effective_health = {**persisted_health, **(health_overrides or {})}
    effective_subscription = {
        **persisted_subscription,
        **(subscription_overrides or {}),
    }
    alert_rows = await repository.recent("alerts", limit=8)
    # Keep a bounded daily history for the native Overview chart.  This is
    # control-plane data (one row per session date), not a raw tick stream.
    pnl_rows = await repository.recent("daily_pnl", limit=90)
    agent_rows = await repository.recent("agent_runs", limit=100)
    agent_output_rows = await repository.recent("agent_outputs", limit=100)
    usage_rows = await repository.recent("model_usage", limit=200)
    agent_registry = AgentRegistry(overrides=await VersionRepository(database).agent_overrides())
    agents = [item.model_dump(mode="json") for item in agent_registry.descriptors()]
    latest_agent_runs: dict[str, dict[str, Any]] = {}
    for row in agent_rows:
        agent_id = str(row.get("agent_id") or "")
        if agent_id and agent_id not in latest_agent_runs:
            latest_agent_runs[agent_id] = row
    for descriptor in agents:
        latest_run = latest_agent_runs.get(str(descriptor["agent_id"]))
        if latest_run is not None:
            run_status = str(latest_run.get("status") or "IDLE").upper()
            # A RUNNING row only means "analyzing" while an engine exists and
            # the run is younger than the pipeline deadline; anything else is
            # a leftover of a stop or crash.
            running = (
                run_status == "RUNNING"
                and engine_running is not False
                and _younger_than(latest_run.get("created_at"), AGENT_RUN_MAX_AGE)
            )
            descriptor["status"] = (
                "ERROR" if run_status == "FAILED" else "ACTIVE" if running else "IDLE"
            )
            descriptor["last_run_at"] = latest_run.get("created_at")
            descriptor["last_error"] = latest_run.get("error_code")
            descriptor["provider"] = latest_run.get("provider")
            descriptor["model"] = latest_run.get("model")
            descriptor["latency_ms"] = latest_run.get("latency_ms")
    provider_rows = [
        item.model_dump(mode="json")
        for item in provider_statuses(
            settings,
            health_overrides=effective_health,
            subscription_overrides=effective_subscription,
        )
    ]
    circuits = _latest_provider_circuits(event_rows)
    for row in provider_rows:
        circuit = circuits.get(str(row.get("provider_id")))
        if circuit is not None:
            row["circuit"] = circuit
    source_rows = [
        item.model_dump(mode="json")
        for item in default_source_statuses(settings.public.external_data)
    ]
    source_run_rows = await repository.recent("source_runs", limit=500)
    latest_source_runs: dict[str, dict[str, Any]] = {}
    records_today: dict[str, int] = {}
    today = trading_day(datetime.now(UTC))
    for row in source_run_rows:
        payload = _payload(row)
        source_id = str(payload.get("source_id") or row.get("asset") or "")
        if not source_id:
            continue
        latest_source_runs.setdefault(source_id, {**payload, **row})
        created_at = row.get("created_at")
        if isinstance(created_at, datetime) and trading_day(_as_utc(created_at)) == today:
            records_today[source_id] = records_today.get(source_id, 0) + int(
                payload.get("records_count") or 0
            )
    for source in source_rows:
        source_id = str(source.get("source_id") or "")
        run = latest_source_runs.get(source_id)
        if run is None and source.get("category") == "social":
            run = next(
                (
                    value
                    for key, value in latest_source_runs.items()
                    if key.startswith("social-")
                ),
                None,
            )
        if run is None:
            continue
        run_status = str(run.get("status") or "").upper()
        created_at = run.get("created_at")
        if run_status == "SUCCEEDED" and isinstance(created_at, datetime):
            source["last_success_at"] = created_at.isoformat()
            source["freshness_seconds"] = max(
                0,
                int((datetime.now(UTC) - _as_utc(created_at)).total_seconds()),
            )
        if run_status == "FAILED":
            source["last_error"] = str(run.get("error") or "source_run_failed")
        source["records_today"] = records_today.get(
            source_id,
            records_today.get(str(run.get("source_id") or ""), 0),
        )
        source["detail"] = (
            f"Last run {run_status.lower()}; {source['records_today']} records today."
        )
    memory_rows: list[dict[str, Any]] = []
    seen_memory_agents: set[str] = set()
    for row in agent_output_rows:
        payload = _payload(row)
        agent_id = str(payload.get("agent_id") or "unknown")
        if agent_id in seen_memory_agents:
            continue
        seen_memory_agents.add(agent_id)
        output = payload.get("output")
        memory_rows.append(
            {
                "agent_id": agent_id,
                "version": str(payload.get("agent_version") or "unknown"),
                "prompt_hash": str(payload.get("spec_hash") or "unknown"),
                "summary": _memory_summary(output),
                "evidence_count": _evidence_count(output),
                "outcome": str(payload.get("status") or "UNKNOWN"),
                "recorded_at": row.get("created_at"),
            }
        )
    usage_rows_today = [row for row in usage_rows if _is_today(row.get("created_at"))]
    # Subscriptions: no USD budget. Calls and tokens are statistics only; the
    # plan limits (5 h / weekly) come from GET /api/v1/ai/usage.
    usage = {
        "calls_today": len(usage_rows_today),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in usage_rows_today),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in usage_rows_today),
        "records": [
            {
                "provider": row.get("provider"),
                "model": row.get("model"),
                "billing_mode": row.get("billing_mode"),
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "fallback_reason": row.get("fallback_reason"),
                "attempted_providers": row.get("attempted_providers"),
                "created_at": row.get("created_at"),
            }
            for row in usage_rows_today[:40]
        ],
    }
    observability = {
        "agent_runs_total": len(agent_rows),
        "agent_failures": sum(
            1 for row in agent_rows if str(row.get("status") or "").upper() == "FAILED"
        ),
        "provider_fallbacks_today": sum(
            1 for row in usage_rows_today if row.get("fallback_reason")
        ),
        "source_runs_total": len(source_run_rows),
        "alerts_open": 0,
        "database": "ok",
    }
    latest_metric_event = next(
        (
            _payload(row)
            for row in event_rows
            if str(_payload(row).get("status") or "") == "RUNTIME_METRICS"
        ),
        None,
    )
    observability["runtime_metrics"] = _safe_runtime_samples(
        latest_metric_event.get("samples", [])
        if isinstance(latest_metric_event, dict)
        else []
    )
    trace_rows: list[dict[str, Any]] = []
    for table_name, rows in (
        ("agent_runs", agent_rows[:80]),
        ("trade_proposals", proposal_rows),
        ("critic_reviews", _current_era(await repository.recent("critic_reviews", limit=20))),
        ("risk_decisions", risk_rows),
        ("orders", order_rows),
        ("fills", await repository.recent("fills", limit=40)),
        ("system_events", event_rows),
        ("source_runs", source_run_rows[:40]),
        ("model_usage", usage_rows_today[:40]),
    ):
        trace_rows.extend(_trace_record(table_name, row) for row in rows)
    trace_rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    reconciliation: dict[str, Any] = {
        "status": "NOT_RUN",
        "safe_mode": False,
        "mismatches": [],
        "checked_at": None,
    }
    for row in event_rows:
        payload = _payload(row)
        if str(payload.get("status") or "").startswith("RECONCILIATION_"):
            reconciliation = {
                "status": payload.get("status"),
                "safe_mode": bool(payload.get("safe_mode")),
                "mismatches": payload.get("mismatches") or [],
                "checked_at": row.get("created_at"),
            }
            break
    state_repository = TradingStateRepository(database)
    protection_recovery = await state_repository.protective_stop_recovery()
    account_projection = await state_repository.account_projection()
    projected_alerts = project_operational_alerts(
        event_rows,
        alert_rows,
        protection_recovery.model_dump(mode="json"),
    )
    observability["alerts_open"] = sum(
        1 for alert in projected_alerts if not alert.get("acknowledged")
    )
    # Equity is the broker account's, as of its last sync. Never a configured number.
    broker_account = await state_repository.latest_broker_account()
    pnl: dict[str, Any] = {
        "equity": broker_account.get("equity") if broker_account else None,
        "realized_net_pnl": "0",
        "unrealized_pnl": "0",
        "peak_realized_pnl": "0",
        "peak_total_pnl": "0",
        "fees": "0",
        "losing_streak": 0,
    }
    if pnl_rows:
        pnl.update(cast(dict[str, Any], _json_value(dict(pnl_rows[0]))))
    pnl["broker_account"] = broker_account
    pnl["cash_equity"] = str(account_projection.get("equity") or pnl.get("equity") or "0")
    pnl["account_high_water_mark"] = str(
        account_projection.get("high_water_mark") or pnl.get("equity") or "0"
    )
    pnl["marked_equity"] = str(
        Decimal(str(pnl["cash_equity"])) + Decimal(str(pnl.get("unrealized_pnl") or "0"))
    )
    audit: list[dict[str, Any]] = []
    position_records = _latest_position_records(position_rows)
    open_position_records = [
        row
        for row in position_records
        if str(row.get("status") or "OPEN").upper() in {"OPEN", "PARTIALLY_FILLED"}
    ]
    for table_name, rows in (
        ("risk_decisions", risk_rows),
        ("trade_proposals", proposal_rows),
        ("orders", order_rows),
        ("positions", position_rows[:12]),
        ("shadow_trades", shadow_rows),
        ("change_proposals", change_rows),
        ("experiments", experiment_rows),
        ("trade_evaluations", evaluation_rows),
        ("system_events", event_rows),
        ("alerts", alert_rows),
    ):
        for row in rows:
            record = _record(row)
            audit.append(
                {
                    "created_at": record.get("created_at"),
                    "type": table_name,
                    "asset": record.get("asset") or record.get("symbol"),
                    "status": (
                        record.get("status")
                        or record.get("verdict")
                        or record.get("event_type")
                        or "RECORDED"
                    ),
                    "detail": record.get("detail")
                    or record.get("message")
                    or record.get("reason")
                    or record.get("why_now")
                    or record.get("error_code")
                    or record.get("event_type")
                    or "",
                }
            )
    audit.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    config = _public_config(settings)
    session_action = "STOP_LIVE_FOR_DAY" if pnl.get("live_stopped") else "CONTINUE"
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": config,
        "markets": markets,
        "risk": _record(risk_rows[0]) if risk_rows else {},
        "pnl": pnl,
        "session": {
            "action": session_action,
            "reasons": [pnl["stop_reason"]] if pnl.get("stop_reason") else [],
        },
        "reconciliation": cast(dict[str, Any], _json_value(reconciliation)),
        "protection_recovery": cast(
            dict[str, Any], _json_value(protection_recovery.model_dump(mode="json"))
        ),
        "pnl_history": [
            cast(dict[str, Any], _json_value(dict(row)))
            for row in reversed(pnl_rows)
        ],
        "proposals": [_record(row) for row in proposal_rows],
        "orders": [_record(row) for row in order_rows],
        "fills": [_record(row) for row in fill_rows],
        "positions": open_position_records,
        "position_history": position_records[:20],
        "alerts": projected_alerts,
        "shadow_trades": [_record(row) for row in shadow_rows],
        "change_proposals": [_record(row) for row in change_rows],
        "experiments": [_record(row) for row in experiment_rows],
        "trade_evaluations": [_record(row) for row in evaluation_rows],
        "operations": [cast(dict[str, Any], _json_value(row)) for row in operation_rows],
        "unresolved_operations": unresolved_operations,
        "learning_metrics": summarize_evaluations(
            [_payload(row) for row in evaluation_rows]
        ).model_dump(mode="json"),
        "audit": audit[:80],
        "decision_trace": trace_rows[:160],
        "agents": agents,
        "providers": provider_rows,
        "usage": usage,
        "observability": observability,
        "sources": source_rows,
        "source_runs": [_record(row) for row in source_run_rows[:80]],
        "agent_memory": memory_rows,
    }


def _is_today(value: object) -> bool:
    if not isinstance(value, datetime):
        return False
    return trading_day(_as_utc(value)) == trading_day(datetime.now(UTC))


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamps and PostgreSQL aware values alike."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _memory_summary(output: object) -> str:
    if not isinstance(output, dict):
        return "Agent output recorded without a structured summary."
    for key in ("why_not_trade", "why_now", "status", "decision", "verdict", "action"):
        value = output.get(key)
        if value:
            text = str(value)
            return text[:180]
    return "Structured output recorded; no summary field was supplied."


def _evidence_count(output: object) -> int:
    if not isinstance(output, dict):
        return 0
    evidence = output.get("evidence")
    return len(evidence) if isinstance(evidence, list) else 0


def _trace_record(table_name: str, row: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, secret-free metadata for the native decision trace.

    The trace is deliberately metadata-only.  It links the records needed to
    reconstruct a decision without returning prompts, raw model output or
    provider credentials to the desktop client.
    """

    record = _record(row)
    return {
        "id": record.get("id"),
        "type": table_name,
        "asset": record.get("asset") or record.get("symbol"),
        "status": record.get("status") or record.get("verdict") or "RECORDED",
        "created_at": record.get("created_at"),
        "event_time": record.get("event_time"),
        "received_time": record.get("received_time"),
        "processed_time": record.get("processed_time"),
        "decision_id": record.get("decision_id"),
        "proposal_id": record.get("proposal_id") or record.get("trade_id"),
        "agent_run_id": record.get("agent_run_id"),
        "agent_id": record.get("agent_id"),
        "agent_version": record.get("agent_version"),
        "provider": record.get("provider"),
        "model": record.get("model"),
        "billing_mode": record.get("billing_mode"),
        "fallback_reason": record.get("fallback_reason"),
        "attempted_providers": record.get("attempted_providers"),
        "source_id": record.get("source_id"),
        "error_code": record.get("error_code"),
        "event_type": record.get("event_type"),
        "detail": (
            record.get("detail")
            or record.get("message")
            or record.get("reason")
            or record.get("why_now")
            or record.get("error_code")
            or record.get("event_type")
            or record.get("status")
            or ""
        ),
    }


def _prometheus_name(value: object) -> str:
    """Normalize a persisted metric name before writing a text exposition."""

    normalized = re.sub(r"[^a-zA-Z0-9_:]", "_", str(value))
    if not normalized or normalized[0].isdigit():
        normalized = f"hyverion_{normalized}"
    return normalized[:120]


def _prometheus_label(value: object) -> str:
    """Keep labels bounded and escape the Prometheus string grammar."""

    escaped = str(value).replace("\\", "\\\\").replace("\"", '\\"').replace("\n", "\\n")
    return escaped[:160]


def _safe_runtime_samples(samples: object) -> list[dict[str, Any]]:
    """Validate and redact persisted samples before UI or export exposure."""

    if not isinstance(samples, list):
        return []
    safe: list[dict[str, Any]] = []
    sensitive_fragments = ("secret", "token", "password", "credential", "api_key")
    for raw_sample in samples[:500]:
        if not isinstance(raw_sample, dict):
            continue
        try:
            value = Decimal(str(raw_sample.get("value")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not value.is_finite():
            continue
        name = _prometheus_name(raw_sample.get("name"))
        raw_labels = raw_sample.get("labels")
        labels: dict[str, str] = {}
        if isinstance(raw_labels, dict):
            for key, label_value in sorted(raw_labels.items(), key=lambda item: str(item[0]))[:8]:
                normalized_key = str(key).lower()
                if any(fragment in normalized_key for fragment in sensitive_fragments):
                    continue
                labels[_prometheus_name(key)] = str(label_value)[:160]
        safe.append({"name": name, "value": str(value), "labels": labels})
    return safe


def _prometheus_metrics(samples: object, *, mode: str, live_trading: bool) -> str:
    """Render bounded runtime counters for standard local/VPS scrapers.

    Runtime metrics are generated by the deterministic engine and persisted as
    a bounded audit event.  Invalid or non-finite values are skipped instead
    of making the health endpoint fail open.
    """

    lines = [
        "# HELP hyverion_runtime_info Runtime mode and safety boundary.",
        "# TYPE hyverion_runtime_info gauge",
        (
            "hyverion_runtime_info{mode=\""
            f"{_prometheus_label(mode)}\",live_trading=\"{str(live_trading).lower()}\"}} 1"
        ),
    ]
    samples = _safe_runtime_samples(samples)
    emitted: set[str] = set()
    for raw_sample in samples[:500]:
        if not isinstance(raw_sample, dict):
            continue
        name = _prometheus_name(raw_sample.get("name"))
        try:
            value = Decimal(str(raw_sample.get("value")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if not value.is_finite():
            continue
        if name not in emitted:
            lines.extend((f"# TYPE {name} gauge",))
            emitted.add(name)
        labels = raw_sample.get("labels")
        if isinstance(labels, dict):
            label_items = sorted(labels.items(), key=lambda item: str(item[0]))[:8]
            label_text = ",".join(
                f'{_prometheus_name(key)}="{_prometheus_label(value)}"'
                for key, value in label_items
            )
            suffix = f"{{{label_text}}}" if label_text else ""
        else:
            suffix = ""
        lines.append(f"{name}{suffix} {value}")
    return "\n".join(lines) + "\n"


class OwnerNotesRequest(BaseModel):
    """The owner's free-text annotations for one vault note."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=4, max_length=300)
    notes: str = Field(max_length=MAX_OWNER_NOTES_CHARS)


WS_PROTOCOL = "hyverion.v1"
WS_TOKEN_PREFIX = "hyverion.bearer."  # noqa: S105 - protocol prefix, not a secret


def configured_control_api_token(settings: Settings) -> str | None:
    token = settings.secrets.control_api_token
    if token is None or not token.get_secret_value().strip():
        return None
    return token.get_secret_value()


def ensure_control_api_token(settings: Settings) -> Settings:
    """Return settings that carry a control API token, generating one if absent."""

    if configured_control_api_token(settings) is not None:
        return settings
    return settings.model_copy(
        update={
            "secrets": settings.secrets.model_copy(
                update={"control_api_token": SecretStr(secrets.token_urlsafe(32))}
            )
        }
    )


def allowed_ui_origins() -> tuple[str, ...]:
    """Browser origins allowed to call the API directly.

    The desktop app never calls the API from its webview (the Rust shell proxies
    every request), so no browser origin is allowed by default. Loopback dev
    origins can be opted in with CONTROL_API_EXTRA_ORIGINS.
    """

    extra = tuple(
        item.strip()
        for item in os.getenv("CONTROL_API_EXTRA_ORIGINS", "").split(",")
        if item.strip().startswith(("http://127.0.0.1:", "http://localhost:"))
    )
    return extra


def first_run_status(settings: Settings, local_config: Path) -> dict[str, Any]:
    trading = settings.public.trading
    missing: list[str] = []
    if alpaca_paper_credentials(settings.secrets) is None:
        missing.append("broker_credentials")
    if not trading.allowed_symbols:
        missing.append("allowed_symbols")
    return {
        "configured": not missing,
        "missing": missing,
        "local_config_exists": local_config.exists(),
        "mode": trading.mode,
        "live_trading": trading.live_trading,
    }


def _token_matches(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate.encode(), expected.encode())


def _bearer_matches(authorization: str | None, expected: str) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    return _token_matches(authorization.removeprefix("Bearer "), expected)


class StrategyReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(default=None, max_length=20)
    candles: int = Field(default=1000, ge=MIN_CANDLES, le=7_800)


class ChallengerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy_id: str = Field(min_length=1, max_length=40)
    symbol: str | None = Field(default=None, max_length=20)
    candles: int = Field(default=1000, ge=MIN_CANDLES, le=7_800)
    stop_percent: Decimal = Field(ge=Decimal("0.05"), le=Decimal("10"))
    reward_multiple: Decimal = Field(ge=Decimal("0.5"), le=Decimal("10"))
    horizon_minutes: int = Field(ge=1, le=1440)


CandleSource = Callable[[str, int], Awaitable[tuple[Candle, ...]]]


ChartSource = Callable[[str, str, int], Awaitable[tuple[Candle, ...]]]
CHART_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")
CHART_CACHE_SECONDS = 15.0


def _public_chart_source(settings: Settings) -> ChartSource:
    async def fetch(symbol: str, interval: str, limit: int) -> tuple[Candle, ...]:
        adapter = build_market_data(settings, SystemClock())
        return await adapter.fetch_candles(symbol, interval=interval, limit=limit)

    return fetch


def _public_candle_source(settings: Settings) -> CandleSource:
    """The last ``limit`` regular-session 1-minute bars (research replays)."""

    async def fetch(symbol: str, limit: int) -> tuple[Candle, ...]:
        clock = SystemClock()
        adapter = build_market_data(settings, clock)
        end = clock.now()
        # 390 bars per session; pad for weekends and holidays.
        days = (limit // 390 + 1) * 7 // 5 + 4
        history = await adapter.fetch_candle_range(
            symbol, start=end - timedelta(days=days), end=end
        )
        return regular_session_only(history)[-limit:]

    return fetch


class EngineStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_seconds: int = Field(
        default=DEFAULT_INTERVAL_SECONDS, ge=MIN_INTERVAL_SECONDS, le=3600
    )


async def _record_engine_event(database: Database, status: str, detail: dict[str, Any]) -> None:
    await AuditRepository(database).append(
        "system_events",
        {"status": status, "source": "control_api", "engine": detail},
        created_at=datetime.now(UTC),
    )


def create_control_api(
    settings: Settings,
    config_dir: str | None = None,
    engine: EngineSupervisor | None = None,
    candle_source: CandleSource | None = None,
    chart_source: ChartSource | None = None,
) -> FastAPI:
    database = Database(settings.public.database.url)
    config_manager = ConfigManager(config_dir)
    provider_health: dict[str, dict[str, object]] = {}
    subscription_health: dict[str, dict[str, object]] = {}

    login_flows = LoginFlowManager()
    usage_cache = UsageCache()
    if candle_source is None:
        candle_source = _public_candle_source(settings)
    if chart_source is None:
        chart_source = _public_chart_source(settings)
    chart_cache: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}
    if engine is None:
        engine = EngineSupervisor(settings, settings_loader=load_settings)

    optimizer_state: dict[str, Any] = {"task": None, "error": None}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await database.initialize()
        # Open a first version for new agents/strategies and record AGENT.md edits.
        await VersionRepository(database).record_baselines(
            baselines(settings.public.strategies), now=datetime.now(UTC)
        )
        try:
            yield
        finally:
            # Never leave a PAPER engine running without its control plane.
            await asyncio.to_thread(engine.stop)
            await login_flows.shutdown()
            await database.close()

    app = FastAPI(
        title="Hyverion Quant AI Control API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # Fail closed: every state/command route requires a bearer token.  When no
    # token is configured an ephemeral one is generated for this process, so an
    # unauthenticated loopback API (reachable by any local web page) never exists.
    api_token = configured_control_api_token(settings) or secrets.token_urlsafe(32)
    app.state.control_api_token = api_token
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_ui_origins()),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
        max_age=600,
    )

    def require_api_token(authorization: str | None) -> None:
        if not _bearer_matches(authorization, api_token):
            raise HTTPException(status_code=401, detail="control API authentication required")

    @app.get("/")
    async def root() -> dict[str, Any]:
        return {
            "service": "hyverion-quant-control-api",
            "ui": "native",
            "mode": settings.public.trading.mode,
            "live_trading": settings.public.trading.live_trading,
        }

    @app.get("/health/live")
    @app.get("/api/v1/health/live")
    async def live_health() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    @app.get("/api/v1/health/ready")
    async def ready_health() -> dict[str, str]:
        healthy, detail = await database.healthcheck()
        return {"status": "ready" if healthy else "safe_mode", "database": detail}

    @app.get("/api/v1/snapshot")
    async def snapshot(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_api_token(authorization)
        engine_body = await _engine_dict()
        body = await build_snapshot(
            database,
            settings,
            provider_health,
            subscription_health,
            engine_running=engine_body["state"] in {"running", "external"},
        )
        body["engine"] = engine_body
        if body["engine"]["state"] in {"stopped", "exited"} and body["positions"]:
            # Nothing is watching stops/targets/time limits: say so loudly.
            body["alerts"] = [
                {
                    "alert_id": "positions-without-engine",
                    "severity": "CRITICAL",
                    "source": "engine",
                    "message": "Open positions without a running engine",
                    "created_at": datetime.now(UTC).isoformat(),
                    "acknowledged": False,
                },
                *body["alerts"],
            ]
        return body

    async def _engine_dict() -> dict[str, Any]:
        body = (await asyncio.to_thread(engine.status)).as_dict()
        body["flatten_pending"] = flatten_pending(settings)
        return body

    @app.get("/api/v1/engine")
    async def engine_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_api_token(authorization)
        return await _engine_dict()

    @app.post("/api/v1/engine/flatten")
    async def engine_flatten(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Ask the running engine to close every position (paper) and stop entering.

        The API never submits orders: it leaves a request that the engine's
        deterministic cycle executes through RiskEngine -> ExecutionEngine.
        """

        require_api_token(authorization)
        status = await asyncio.to_thread(engine.status)
        if status.state not in {"running", "external"}:
            raise HTTPException(status_code=409, detail={"code": "engine_not_running"})
        await asyncio.to_thread(request_flatten, settings)
        await _record_engine_event(database, "OPERATOR_FLATTEN_REQUESTED", status.as_dict())
        return await _engine_dict()

    @app.post("/api/v1/engine/flatten/cancel")
    async def engine_flatten_cancel(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_api_token(authorization)
        await asyncio.to_thread(clear_flatten, settings)
        await _record_engine_event(database, "OPERATOR_FLATTEN_CANCELLED", {})
        return await _engine_dict()

    @app.post("/api/v1/engine/start")
    async def engine_start(
        request: EngineStartRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Start the PAPER loop as a supervised child process (never LIVE)."""

        require_api_token(authorization)
        try:
            status = await asyncio.to_thread(engine.start, request.interval_seconds)
        except EngineStartError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from exc
        await _record_engine_event(database, "ENGINE_STARTED", status.as_dict())
        return status.as_dict()

    @app.post("/api/v1/engine/stop")
    async def engine_stop(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_api_token(authorization)
        status = await asyncio.to_thread(engine.stop)
        await _record_engine_event(database, "ENGINE_STOPPED", status.as_dict())
        return status.as_dict()

    @app.get("/api/v1/service")
    async def service_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Background engine (launchd agent): installed, loaded, pid."""

        require_api_token(authorization)
        return (await asyncio.to_thread(launchd.status)).as_dict()

    @app.post("/api/v1/service/install")
    async def service_install(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_api_token(authorization)
        # The app's own child engine would hold the lock: stop it first.
        await asyncio.to_thread(engine.stop)
        result = (await asyncio.to_thread(launchd.install)).as_dict()
        await _record_engine_event(database, "SERVICE_INSTALLED", result)
        return result

    @app.post("/api/v1/service/uninstall")
    async def service_uninstall(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        require_api_token(authorization)
        result = (await asyncio.to_thread(launchd.uninstall)).as_dict()
        await _record_engine_event(database, "SERVICE_UNINSTALLED", result)
        return result

    @app.get("/api/v1/setup/status")
    async def setup_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Tell the desktop shell whether first-run onboarding is still required."""

        require_api_token(authorization)
        return first_run_status(settings, config_manager.local_path)

    @app.get("/api/v1/config/public")
    async def public_config(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_api_token(authorization)
        return _public_config(settings)

    @app.get("/api/v1/observability/metrics")
    async def observability_metrics(
        format: str = "json",
        authorization: str | None = Header(default=None),
    ) -> Any:
        """Export bounded runtime metrics without exposing prompts or secrets.

        ``format=json`` is consumed by the native terminal and ``format``
        ``prometheus`` is intended for a future VPS scraper.  Both views use
        the same persisted, sanitized runtime event and remain token-protected
        when the control API token is configured.
        """

        require_api_token(authorization)
        if format not in {"json", "prometheus"}:
            raise HTTPException(status_code=422, detail="format must be json or prometheus")
        snapshot = await build_snapshot(database, settings, provider_health, subscription_health)
        observability = cast(dict[str, Any], snapshot.get("observability") or {})
        samples = observability.get("runtime_metrics") or []
        if format == "prometheus":
            config = cast(dict[str, Any], snapshot.get("config") or {})
            return PlainTextResponse(
                _prometheus_metrics(
                    samples,
                    mode=str(config.get("mode") or "paper"),
                    live_trading=bool(config.get("live_trading")),
                ),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        return {
            "generated_at": snapshot.get("generated_at"),
            "mode": _public_config(settings)["mode"],
            "live_trading": _public_config(settings)["live_trading"],
            "source": "persisted_runtime_metrics",
            "samples": samples[:500] if isinstance(samples, list) else [],
            "summary": {
                key: observability.get(key, 0)
                for key in (
                    "agent_runs_total",
                    "agent_failures",
                    "provider_fallbacks_today",
                    "source_runs_total",
                    "alerts_open",
                )
            },
        }

    @app.get("/api/v1/observability/retention")
    async def observability_retention(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Return a read-only retention estimate for the native audit view."""

        require_api_token(authorization)
        try:
            result = await apply_retention(database, dry_run=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=type(exc).__name__) from exc
        return {
            "status": "RETENTION_DRY_RUN",
            "generated_at": result.generated_at.isoformat(),
            "eligible_rows": result.eligible_rows,
            "deleted_rows": result.deleted_rows,
            "tables": {
                item.table: {
                    "cutoff": item.cutoff.isoformat(),
                    "eligible_rows": item.eligible_rows,
                    "deleted_rows": item.deleted_rows,
                }
                for item in result.tables
            },
        }

    @app.get("/api/v1/readiness")
    async def readiness(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Return the deterministic PAPER/LIVE readiness projection.

        This report is intentionally separate from ``/snapshot`` so operators
        can run an explicit gate review without implying that LIVE is enabled.
        It contains only public configuration, provider status and protection
        invariants; credentials and model prompts never cross this boundary.
        """

        require_api_token(authorization)
        database_ok, database_detail = await database.healthcheck()
        event_rows = await AuditRepository(database).recent("system_events", limit=40)
        persisted_health, persisted_subscription = _provider_overrides_from_events(event_rows)
        provider_rows = [
            item.model_dump(mode="json")
            for item in provider_statuses(
                settings,
                health_overrides={**persisted_health, **provider_health},
                subscription_overrides={**persisted_subscription, **subscription_health},
            )
        ]
        protection = await TradingStateRepository(database).protective_stop_recovery()
        report = build_readiness_report(
            settings,
            database_ok=database_ok,
            database_detail=database_detail,
            provider_rows=provider_rows,
            protection_ok=protection.ok,
            protection_detail=(
                protection.reason or "protective-stop recovery is not healthy"
            ),
        )
        return report.model_dump(mode="json")

    @app.get("/api/v1/plan-audit")
    async def plan_audit(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Return the repository plan/specification audit for the native Guides view.

        This is a read-only development and operations check.  It never changes
        trading state and it never authorizes LIVE; the response is deliberately
        a compact projection of the machine-readable ``spec/`` evidence.
        """

        require_api_token(authorization)
        try:
            report = build_plan_audit()
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=type(exc).__name__) from exc
        return report.model_dump()

    @app.post("/api/v1/system/backup")
    async def system_backup(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Create a verified local SQLite backup without changing trading state."""

        require_api_token(authorization)
        try:
            source = sqlite_path(settings.public.database.url)
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            destination = source.parent / "backups" / f"trading-bot-{timestamp}.db"
            result = await create_sqlite_backup(database, destination)
        except (OSError, RuntimeError, ValueError) as exc:
            now = datetime.now(UTC)
            await AuditRepository(database).append(
                "system_events",
                {
                    "status": "DATABASE_BACKUP_FAILED",
                    "error_code": type(exc).__name__,
                },
                created_at=now,
                event_time=now,
                received_time=now,
                processed_time=now,
            )
            return {
                "status": "BACKUP_FAILED",
                "safe_mode": True,
                "error_code": type(exc).__name__,
            }
        now = datetime.now(UTC)
        await AuditRepository(database).append(
            "system_events",
            {
                "status": "DATABASE_BACKUP_VERIFIED",
                "filename": Path(result.path).name,
                "size_bytes": result.size_bytes,
                "verified": result.verified,
            },
            created_at=now,
            event_time=now,
            received_time=now,
            processed_time=now,
        )
        return {
            "status": "BACKUP_VERIFIED",
            "filename": Path(result.path).name,
            "path": str(result.path),
            "size_bytes": result.size_bytes,
            "verified": result.verified,
        }

    @app.post("/api/v1/config/validate")
    async def validate_config(
        patch: ConfigPatch, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        errors = config_manager.validate_patch(patch, settings)
        return {
            "valid": not errors,
            "errors": list(errors),
            "changed_fields": sorted(patch.model_dump(exclude_none=True)),
        }

    @app.post("/api/v1/config/apply")
    async def apply_config(
        patch: ConfigPatch, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        nonlocal settings
        require_api_token(authorization)
        try:
            result = config_manager.apply(patch, settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if result.applied:
            # Validate the next patch against what is on disk now, not the
            # settings this process started with.
            if (config_manager.config_dir / "default.yaml").exists():
                settings = load_settings(config_manager.config_dir)
            elif shares_app_state():
                settings = load_settings()
        return result.model_dump(mode="json")

    @app.post("/api/v1/backtest/run")
    async def run_backtest(
        request: BacktestRunRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Run a bounded local research replay; never creates an order intent."""

        require_api_token(authorization)
        capital = request.capital_usd or SIMULATION_CAPITAL_USD
        prices = request.prices
        if prices is None:
            # Never replay invented prices: real evidence is /backtest/strategies.
            raise HTTPException(status_code=422, detail={"code": "prices_required"})
        engine = BacktestEngine()
        if request.walk_forward:
            if len(prices) < 10:
                raise HTTPException(
                    status_code=422,
                    detail="walk-forward replay requires at least 10 price observations",
                )
            walk_result = engine.run_walk_forward(
                tuple(prices),
                capital=capital,
                train_size=4,
                validation_size=3,
                test_size=3,
                step=3,
            )
            result: BacktestResult | WalkForwardResult = walk_result
            aggregate = walk_result.aggregate
            experiment_id = walk_result.experiment_id
            period = "walk-forward OOS"
        else:
            baseline_result = engine.run_buy_and_hold_baseline(tuple(prices), capital=capital)
            result = baseline_result
            aggregate = baseline_result
            experiment_id = str(uuid4())
            period = "deterministic baseline"
        payload = result.model_dump(mode="json")
        payload.update(
            {
                "experiment_id": experiment_id,
                "status": "COMPLETED",
                "period": period,
                "net_pnl": str(aggregate.net_pnl_usd),
                "max_drawdown": str(aggregate.max_drawdown_usd),
            }
        )
        now = datetime.now(UTC)
        await AuditRepository(database).append(
            "experiments",
            payload,
            created_at=now,
            asset="research",
            event_time=now,
            received_time=now,
            processed_time=now,
            record_id=experiment_id,
        )
        return cast(dict[str, Any], _json_value(payload))

    @app.post("/api/v1/backtest/strategies")
    async def backtest_strategies(
        request: StrategyReplayRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Replay every enabled strategy over real public candles (research only)."""

        require_api_token(authorization)
        symbol = request.symbol or settings.public.trading.allowed_symbols[0]
        if symbol not in settings.public.trading.allowed_symbols:
            raise HTTPException(status_code=422, detail={"code": "symbol_not_allowed"})
        capital = SIMULATION_CAPITAL_USD
        try:
            candles = await candle_source(symbol, request.candles)
        except Exception as exc:  # public data outage: report, never guess prices
            raise HTTPException(
                status_code=503, detail={"code": "market_data_unavailable"}
            ) from exc
        try:
            report = replay_strategies(
                strategy_factories(settings.public.strategies), candles, capital=capital
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "insufficient_history"}) from exc
        experiment_id = str(uuid4())
        payload = report.model_dump(mode="json")
        oos = [plugin.out_of_sample for plugin in report.plugins]
        payload.update(
            {
                "experiment_id": experiment_id,
                "status": "COMPLETED",
                "period": "strategy replay",
                # Headline numbers are out-of-sample only; in-sample stays in "plugins".
                "net_pnl": str(sum((stats.net_pnl_usd for stats in oos), Decimal("0"))),
                "max_drawdown": str(
                    max((stats.max_drawdown_usd for stats in oos), default=Decimal("0"))
                ),
            }
        )
        now = datetime.now(UTC)
        await AuditRepository(database).append(
            "experiments",
            payload,
            created_at=now,
            asset=symbol,
            event_time=now,
            received_time=now,
            processed_time=now,
            record_id=experiment_id,
        )
        return cast(dict[str, Any], _json_value(payload))

    @app.post("/api/v1/backtest/challenger")
    async def backtest_challenger(
        request: ChallengerRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Replay production exits vs challenger exits on the same real candles.

        Research only: it never changes configuration. A challenger that wins
        out-of-sample is merely eligible for a reviewed ChangeProposal.
        """

        require_api_token(authorization)
        if request.strategy_id not in settings.public.strategies.enabled:
            raise HTTPException(status_code=422, detail={"code": "strategy_not_enabled"})
        symbol = request.symbol or settings.public.trading.allowed_symbols[0]
        if symbol not in settings.public.trading.allowed_symbols:
            raise HTTPException(status_code=422, detail={"code": "symbol_not_allowed"})
        champion_exit = exit_defaults(request.strategy_id)
        challenger_exit = ExitParams(
            stop_percent=request.stop_percent,
            reward_multiple=request.reward_multiple,
            horizon_seconds=request.horizon_minutes * 60,
        )
        try:
            candles = await candle_source(symbol, request.candles)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail={"code": "market_data_unavailable"}
            ) from exc

        def params(exit_params: ExitParams) -> dict[str, str]:
            return {
                "stop_percent": str(exit_params.stop_percent),
                "reward_multiple": str(exit_params.reward_multiple),
                "horizon_minutes": str(exit_params.horizon_seconds // 60),
            }

        strategies = settings.public.strategies
        try:
            report = compare_champion_challenger(
                strategy_factory(strategies, request.strategy_id),
                strategy_factory(strategies, request.strategy_id, challenger_exit),
                candles,
                capital=SIMULATION_CAPITAL_USD,
                champion_params=params(champion_exit),
                challenger_params=params(challenger_exit),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "insufficient_history"}) from exc
        experiment_id = str(uuid4())
        payload = report.model_dump(mode="json")
        payload.update(
            {
                "experiment_id": experiment_id,
                "status": "COMPLETED",
                "period": "champion challenger",
                "net_pnl": str(report.challenger.out_of_sample.net_pnl_usd),
                "max_drawdown": str(report.challenger.out_of_sample.max_drawdown_usd),
            }
        )
        now = datetime.now(UTC)
        await AuditRepository(database).append(
            "experiments",
            payload,
            created_at=now,
            asset=symbol,
            event_time=now,
            received_time=now,
            processed_time=now,
            record_id=experiment_id,
        )
        return cast(dict[str, Any], _json_value(payload))

    @app.get("/api/v1/learning/proposals")
    async def learning_proposals(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Return bounded change proposals for the native review workspace."""

        require_api_token(authorization)
        rows = await AuditRepository(database).recent("change_proposals", limit=100)
        return [_record(row) for row in rows]

    @app.get("/api/v1/learning/timeline")
    async def learning_timeline(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Latest state of each change proposal with its review history."""

        require_api_token(authorization)
        audit = AuditRepository(database)
        items = proposal_timeline(await audit.recent("change_proposals", limit=500))
        experiments = {
            str(payload.get("experiment_id")): payload
            for payload in (_payload(row) for row in await audit.recent("experiments", limit=200))
        }
        for item in items:
            for evidence in item.get("evidence") or []:
                if isinstance(evidence, str) and evidence.startswith("experiment:"):
                    experiment = experiments.get(evidence.removeprefix("experiment:"))
                    if experiment and "before" in experiment:
                        item["experiment"] = {
                            key: experiment.get(key)
                            for key in ("experiment_id", "symbols", "minutes", "before", "after",
                                        "champion_params", "challenger_params")
                        }
        return cast(list[dict[str, Any]], _json_value(items))

    @app.post("/api/v1/learning/proposals")
    async def create_learning_proposal(
        request: ChangeProposalRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Persist a review candidate; this endpoint never edits code or deploys."""

        require_api_token(authorization)
        supervisor = LearningSupervisor(
            repository=AuditRepository(database),
            clock=SystemClock(),
        )
        try:
            proposal = await supervisor.propose(**request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return proposal.model_dump(mode="json")

    @app.post("/api/v1/learning/proposals/{proposal_id}/transition")
    async def transition_learning_proposal(
        proposal_id: str,
        request: ChangeProposalTransitionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Record an explicit human review transition without deployment side effects."""

        require_api_token(authorization)
        rows = await AuditRepository(database).recent("change_proposals", limit=500)
        proposal = latest_proposal(rows, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="change proposal not found")
        supervisor = LearningSupervisor(
            repository=AuditRepository(database),
            clock=SystemClock(),
        )
        try:
            updated = await supervisor.transition(
                proposal, request.target, reason=request.reason
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        payload = updated.model_dump(mode="json")
        if (
            updated.status == "APPROVED"
            and updated.candidate_spec.get("kind") == "feature_request"
        ):
            # An accepted tool request becomes a task file to build and review;
            # software never implements it on its own.
            payload["request_path"] = str(
                await asyncio.to_thread(write_feature_request, updated, request.reason)
            )
        return payload

    @app.get("/api/v1/market/session")
    async def market_session(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """New York session state for the status bar (NYSE calendar)."""

        require_api_token(authorization)
        return MarketClock(SystemClock()).snapshot().as_dict()

    @app.get("/api/v1/market/candles")
    async def market_candles(
        symbol: str,
        interval: str = "1m",
        limit: int = 120,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Public OHLC candles for charts (approved symbols, fixed intervals)."""

        require_api_token(authorization)
        if symbol not in settings.public.trading.allowed_symbols:
            raise HTTPException(status_code=422, detail={"code": "symbol_not_allowed"})
        if interval not in CHART_INTERVALS:
            raise HTTPException(status_code=422, detail={"code": "interval_not_allowed"})
        if limit < 10 or limit > 500:
            raise HTTPException(status_code=422, detail={"code": "limit_out_of_range"})
        key = (symbol, interval, limit)
        cached = chart_cache.get(key)
        if cached is not None and monotonic() - cached[0] < CHART_CACHE_SECONDS:
            return cached[1]
        try:
            candles = await chart_source(symbol, interval, limit)
        except MarketDataUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": exc.code}) from exc
        except Exception as exc:  # data outage: report, never guess prices
            raise HTTPException(
                status_code=503, detail={"code": "market_data_unavailable"}
            ) from exc
        payload = [
            cast(dict[str, Any], _json_value(candle.model_dump(mode="json")))
            for candle in candles
        ]
        chart_cache[key] = (monotonic(), payload)
        return payload

    @app.get("/api/v1/trades")
    async def trades(
        view: Literal["open", "closed", "attention"] | None = None,
        limit: int = 200,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """One row per trade: lifecycle, latest position state and fills."""

        require_api_token(authorization)
        if limit < 1 or limit > 500:
            raise HTTPException(status_code=422, detail={"code": "limit_out_of_range"})
        repository = AuditRepository(database)
        operation_rows = [
            cast(dict[str, Any], _json_value(row))
            for row in await OperationLifecycleRepository(database).recent(limit=500)
        ]
        positions = _latest_position_records(await repository.recent("positions", limit=500))
        fills = [_record(row) for row in await repository.recent("fills", limit=500)]
        recovery = await TradingStateRepository(database).protective_stop_recovery()
        rows = build_trades(
            operation_rows,
            positions,
            fills,
            unprotected_position_ids=recovery.unprotected_position_ids,
        )
        return filter_trades(rows, view)[:limit]

    @app.get("/api/v1/orders")
    async def orders(
        asset: str | None = None,
        side: str | None = None,
        status: str | None = None,
        limit: int = 200,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Simulated orders with their fills, newest first, optionally filtered."""

        require_api_token(authorization)
        if limit < 1 or limit > 500:
            raise HTTPException(status_code=422, detail={"code": "limit_out_of_range"})
        rows = [_record(row) for row in await AuditRepository(database).recent("orders", limit=500)]
        selected = [
            row
            for row in rows
            if (asset is None or row.get("asset") == asset)
            and (side is None or str(row.get("side") or "").upper() == side.upper())
            and (status is None or str(row.get("status") or "").upper() == status.upper())
        ]
        return selected[:limit]

    @app.get("/api/v1/orders/rejected")
    async def rejected_orders(
        limit: int = 100,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Entries the risk engine stopped, with its reasons and the shadow result."""

        require_api_token(authorization)
        if limit < 1 or limit > 200:
            raise HTTPException(status_code=422, detail={"code": "limit_out_of_range"})
        lifecycle = OperationLifecycleRepository(database)
        operation_rows = [
            row
            for row in await lifecycle.recent(limit=500)
            if str(row.get("state") or "") == "REJECTED"
        ][:limit]
        details: dict[str, dict[str, Any]] = {}
        for row in operation_rows:
            operation_id = str(row["id"])
            for event in reversed(await lifecycle.events(operation_id)):
                if str(event.get("to_state") or "") == "REJECTED":
                    raw = event.get("details")
                    details[operation_id] = raw if isinstance(raw, dict) else {}
                    break
        shadows = {
            str(record.get("proposal_id")): record
            for record in (
                _record(row)
                for row in await AuditRepository(database).recent("shadow_trades", limit=500)
            )
            if record.get("proposal_id")
        }
        return [
            cast(dict[str, Any], _json_value(row))
            for row in rejected_entries(
                [cast(dict[str, Any], _json_value(row)) for row in operation_rows],
                details,
                shadows,
            )
        ]

    def _deployer() -> ChangeDeployer:
        return ChangeDeployer(
            database=database,
            clock=SystemClock(),
            strategies=settings.public.strategies.enabled,
            agents=[item.agent_id for item in AgentRegistry().descriptors()],
        )

    @app.post("/api/v1/learning/proposals/{proposal_id}/apply")
    async def apply_learning_proposal(
        proposal_id: str,
        request: ReviewReasonRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Human approval: record APPROVED/DEPLOYED and activate the new version.

        Only strategy parameters (bounded) and non-safety agent specs can be
        applied; risk limits are rejected with ``forbidden_target``.
        """

        require_api_token(authorization)
        try:
            return await _deployer().apply(proposal_id, reason=request.reason)
        except ChangeNotApplicable as exc:
            status = 404 if exc.code == "proposal_not_found" else 422
            raise HTTPException(status_code=status, detail={"code": exc.code}) from exc

    @app.post("/api/v1/learning/components/{component_id}/rollback")
    async def rollback_component(
        component_id: str,
        request: ReviewReasonRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Undo the active version and restore the previous one."""

        require_api_token(authorization)
        try:
            return await _deployer().rollback(component_id, reason=request.reason)
        except ChangeNotApplicable as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code}) from exc

    @app.get("/api/v1/components")
    async def components(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Active version of every agent and strategy."""

        require_api_token(authorization)
        rows = await VersionRepository(database).active()
        return [
            {
                "component_id": component_id,
                "kind": row["kind"],
                "version": row["version"],
                "params": row["params"] if row["kind"] == "strategy" else None,
                "active_from": _json_value(row["active_from"]),
                "overridden": "spec_markdown" in (row["params"] or {}),
            }
            for component_id, row in sorted(rows.items())
        ]

    @app.get("/api/v1/components/{component_id}/history")
    async def component_history(
        component_id: str,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Every version of one agent or strategy: what, why, who and real results."""

        require_api_token(authorization)
        history = await VersionRepository(database).history(component_id)
        if not history:
            raise HTTPException(status_code=404, detail={"code": "component_not_found"})
        audit = AuditRepository(database)
        timeline = {
            str(item["id"]): item
            for item in proposal_timeline(await audit.recent("change_proposals", limit=500))
        }
        evaluations = [_payload(row) for row in await audit.recent("trade_evaluations", limit=500)]
        strategy_of = {
            str(payload.get("proposal_id")): str(evidence[0].get("source"))
            for payload in (
                _payload(row) for row in await audit.recent("trade_proposals", limit=500)
            )
            if isinstance(evidence := payload.get("evidence"), list)
            and evidence
            and isinstance(evidence[0], dict)
        }
        attributions = [
            {**_payload(row)}
            for row in await audit.recent("decision_attributions", limit=500)
        ]
        items: list[dict[str, Any]] = []
        for row in history:
            start = _as_utc(row["active_from"])
            end = _as_utc(row["active_to"]) if row["active_to"] else None
            proposal = timeline.get(str(row.get("proposal_id") or ""))
            items.append(
                {
                    "version": row["version"],
                    "kind": row["kind"],
                    "active_from": start.isoformat(),
                    "active_to": end.isoformat() if end else None,
                    "active": end is None,
                    "reason": row.get("reason"),
                    "params": row["params"] if row["kind"] == "strategy" else None,
                    "proposal": (
                        {
                            key: proposal.get(key)
                            for key in (
                                "id",
                                "reason",
                                "affected_rules",
                                "expected_improvement",
                                "risk",
                                "status",
                                "history",
                            )
                        }
                        if proposal
                        else None
                    ),
                    "results": window_metrics(
                        component_id=component_id,
                        kind=row["kind"],
                        start=start,
                        end=end,
                        evaluations=evaluations,
                        strategy_of=strategy_of,
                        attributions=attributions,
                    ),
                }
            )
        return cast(list[dict[str, Any]], _json_value(items))

    @app.get("/api/v1/agents/{agent_id}/spec")
    async def agent_spec(
        agent_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """The AGENT.md text in use (the approved override or the bundled file)."""

        require_api_token(authorization)
        overrides = await VersionRepository(database).agent_overrides()
        try:
            descriptor, text = AgentRegistry(overrides=overrides).specification(agent_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "agent_not_found"}) from exc
        return {"agent_id": agent_id, "version": descriptor.version, "spec_markdown": text}

    @app.get("/api/v1/learning/optimizer")
    async def optimizer_status(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Whether the optimizer is running and what its last pass found."""

        require_api_token(authorization)
        task = optimizer_state["task"]
        return cast(
            dict[str, Any],
            _json_value(
                {
                    "running": task is not None and not task.done(),
                    "error": optimizer_state["error"],
                    "last_run": await last_run(database),
                }
            ),
        )

    @app.post("/api/v1/learning/optimizer/run", status_code=202)
    async def optimizer_run(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Start one optimizer pass in the background ("Buscar mejoras")."""

        require_api_token(authorization)
        task = optimizer_state["task"]
        if task is not None and not task.done():
            raise HTTPException(status_code=409, detail={"code": "optimizer_running"})

        async def run() -> None:
            optimizer_state["error"] = None
            with optimizer_lock(engine_data_dir(settings)) as acquired:
                if not acquired:
                    optimizer_state["error"] = "optimizer_running"
                    return
                try:
                    await run_optimizer(
                        database=database,
                        strategies=settings.public.strategies,
                        symbols=settings.public.trading.allowed_symbols,
                        capital=SIMULATION_CAPITAL_USD,
                        candle_history=candle_source,
                        clock=SystemClock(),
                        runner=asyncio.to_thread,
                    )
                except Exception:  # outage or bad data: report, never retry blindly
                    optimizer_state["error"] = "optimizer_failed"

        optimizer_state["task"] = asyncio.create_task(run())
        return {"started": True}

    @app.get("/api/v1/operations/{operation_id}/events")
    async def operation_events(
        operation_id: str,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Return one operation's append-only lifecycle timeline."""

        require_api_token(authorization)
        lifecycle = OperationLifecycleRepository(database)
        if await lifecycle.get(operation_id) is None:
            raise HTTPException(status_code=404, detail="operation not found")
        return [
            cast(dict[str, Any], _json_value(row))
            for row in await lifecycle.events(operation_id)
        ]

    @app.post("/api/v1/operations/{operation_id}/resolve")
    async def resolve_operation(
        operation_id: str,
        request: OperationResolutionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Record an operator's venue-verified resolution; it never places orders."""

        require_api_token(authorization)
        service = OperatorResolutionService(
            lifecycle=OperationLifecycleRepository(database),
            state=TradingStateRepository(database),
            repository=AuditRepository(database),
            clock=SystemClock(),
        )
        try:
            state = await service.resolve(
                operation_id, OperationState(request.target), request.reason
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="operation not found") from exc
        except OperatorResolutionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"operation_id": operation_id, "state": state.value}

    def memory_operations() -> MemoryOperations:
        vault = FileVaultRepository(settings.public.memory.vault_path)
        return MemoryOperations(database=database, clock=SystemClock(), vault=vault)

    @app.get("/api/v1/memory")
    async def memory_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Health of every memory layer, knowledge counts and agent reliability (read-only)."""

        require_api_token(authorization)
        health = await MemoryHealthService(
            settings=settings, database=database, clock=SystemClock()
        ).check()
        overview = await memory_operations().overview()
        return cast(
            dict[str, Any], _json_value({"health": asdict(health), "overview": asdict(overview)})
        )

    @app.get("/api/v1/memory/knowledge")
    async def memory_knowledge(
        text: str | None = None,
        symbol: str | None = None,
        strategy: str | None = None,
        regime: str | None = None,
        status: str | None = None,
        min_reliability: str | None = None,
        limit: int = 100,
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        require_api_token(authorization)
        try:
            statuses = (
                frozenset({KnowledgeStatus(status)}) if status else frozenset(KnowledgeStatus)
            )
            floor = Decimal(min_reliability) if min_reliability else None
            if floor is not None and not Decimal("0") <= floor <= Decimal("1"):
                raise ValueError("min_reliability must be between 0 and 1")
            return await memory_operations().knowledge(
                text=text,
                symbol=symbol,
                strategy=strategy,
                market_regime=regime,
                statuses=statuses,
                min_reliability=floor,
                limit=max(1, min(limit, 500)),
            )
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/memory/knowledge/{identifier}")
    async def memory_detail(
        identifier: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        try:
            return await memory_operations().inspect(identifier)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc

    @app.post("/api/v1/memory/knowledge/{identifier}/decision")
    async def memory_decision(
        identifier: str,
        request: MemoryDecisionRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Human confirmation or retirement; versioned, audited and never a deletion."""

        require_api_token(authorization)
        operations = memory_operations()
        try:
            if request.action == "confirm":
                memory = await operations.confirm(
                    identifier, reason=request.reason, operator="operator-ui"
                )
            else:
                memory = await operations.retire(
                    identifier, reason=request.reason, operator="operator-ui"
                )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        except MemoryOperatorError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return memory_summary(memory)

    @app.get("/api/v1/memory/candidates")
    async def memory_candidates(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        require_api_token(authorization)
        return await memory_operations().candidates()

    @app.get("/api/v1/memory/conflicts")
    async def memory_conflicts(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        require_api_token(authorization)
        return await memory_operations().conflicts()

    @app.post("/api/v1/memory/cycle")
    async def memory_cycle(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Run one deterministic memory maintenance cycle (no orders, no risk changes)."""

        require_api_token(authorization)
        report = await run_memory_cycle(settings, database, SystemClock())
        return report.summary()

    @app.get("/api/v1/memory/vault")
    async def memory_vault(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Knowledge Vault notes and wikilink graph for the in-app vault view."""

        require_api_token(authorization)
        return await asyncio.to_thread(vault_overview, Path(settings.public.memory.vault_path))

    @app.get("/api/v1/memory/vault/note")
    async def memory_vault_note(
        path: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        try:
            return await asyncio.to_thread(read_note, Path(settings.public.memory.vault_path), path)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/v1/memory/vault/note/owner")
    async def memory_vault_owner_notes(
        request: OwnerNotesRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Replace only the owner's section of a note; managed content is untouched."""

        require_api_token(authorization)
        try:
            return await asyncio.to_thread(
                write_owner_notes,
                Path(settings.public.memory.vault_path),
                request.path,
                request.notes,
            )
        except VaultError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/agents")
    async def agents(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        require_api_token(authorization)
        overrides = await VersionRepository(database).agent_overrides()
        return [
            item.model_dump(mode="json")
            for item in AgentRegistry(overrides=overrides).descriptors()
        ]

    @app.get("/api/v1/providers")
    async def providers(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        require_api_token(authorization)
        persisted_health, persisted_subscription = _provider_overrides_from_events(
            await AuditRepository(database).recent("system_events", limit=40)
        )
        return [
            item.model_dump(mode="json")
            for item in provider_statuses(
                settings,
                health_overrides={**persisted_health, **provider_health},
                subscription_overrides={**persisted_subscription, **subscription_health},
            )
        ]

    @app.get("/api/v1/ai/models")
    async def ai_models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Model per role for the primary subscription and what can be picked."""

        require_api_token(authorization)
        return {
            "primary_provider": settings.public.ai.primary_provider,
            "roles": [
                {"role": role, "label": ROLE_LABELS[role]} for role in AI_ROLES
            ],
            "models": {
                role: model or "default"
                for role, model in live_ai_models(settings).model_dump().items()
            },
            "catalog": catalog_payload(),
        }

    @app.get("/api/v1/ai/usage")
    async def ai_usage(
        refresh: bool = False, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """5-hour and weekly limits of every subscription in the chain."""

        require_api_token(authorization)
        chain = [
            item
            for item in (
                settings.public.ai.primary_provider,
                *settings.public.ai.fallback_providers,
            )
            if item != "disabled"
        ]
        providers = [
            (await usage_cache.get(provider_id, refresh=refresh)).as_dict()
            for provider_id in chain
        ]
        return {
            "primary": providers[0] if providers else None,
            "providers": providers,
        }

    @app.post("/api/v1/providers/{provider_id}/switch-account")
    async def provider_switch_account(
        provider_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Sign out of the provider CLI and start its official login again."""

        require_api_token(authorization)
        if provider_id not in SUBSCRIPTION_PROVIDER_IDS:
            raise HTTPException(status_code=404, detail="unknown provider")
        signed_out = await sign_out(provider_id)
        usage_cache.forget(provider_id)
        subscription_health.pop(provider_id, None)
        await _persist_provider_check(
            AuditRepository(database),
            event_type="PROVIDER_ACCOUNT_SWITCH",
            provider_id=provider_id,
            result={"signed_out": signed_out},
        )
        session = await login_flows.start(provider_id)
        return {"signed_out": signed_out, "login": session}

    @app.post("/api/v1/providers/{provider_id}/login")
    async def provider_login(
        provider_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        if settings.public.ai.primary_auth_mode == "subscription":
            # Launch the provider's official browser login (fixed argv) and
            # report progress; the UI polls GET .../login for state and link.
            if provider_id not in SUBSCRIPTION_PROVIDER_IDS:
                raise HTTPException(status_code=404, detail="unknown provider")
            return await login_flows.start(provider_id)
        action = login_action(provider_id)
        payload = asdict(action)
        payload["command"] = list(action.command)
        return payload

    @app.get("/api/v1/providers/{provider_id}/login")
    async def provider_login_status(
        provider_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        return login_flows.status(provider_id)

    @app.post("/api/v1/providers/{provider_id}/login/cancel")
    async def provider_login_cancel(
        provider_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        return await login_flows.cancel(provider_id)

    @app.post("/api/v1/providers/{provider_id}/test")
    async def provider_test(
        provider_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        statuses = {item.provider_id: item for item in provider_statuses(settings)}
        status = statuses.get(provider_id)
        configured = bool(status and status.api_configured)
        presence = api_test_action(provider_id, configured)
        if not presence.supported:
            return asdict(presence)
        secret_name = {
            "anthropic": "anthropic_api_key",
            "openai": "openai_api_key",
            "xai": "xai_api_key",
            "gemini": "gemini_api_key",
        }.get(provider_id)
        if secret_name is None:
            return asdict(presence)
        secret = getattr(settings.secrets, secret_name)
        api_key = secret.get_secret_value() if secret else None
        if not api_key:
            api_key = KeyringSecretStore().get(f"provider:{provider_id}:api_key")
        result = (await check_provider(provider_id, api_key)).as_dict()
        if result.get("status"):
            provider_health[provider_id] = result
            await _persist_provider_check(
                AuditRepository(database),
                event_type="PROVIDER_HEALTH_CHECK",
                provider_id=provider_id,
                result=result,
            )
        return result

    @app.post("/api/v1/providers/{provider_id}/subscription")
    async def provider_subscription(
        provider_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        require_api_token(authorization)
        result = (await check_subscription(provider_id)).as_dict()
        subscription_health[provider_id] = result
        await _persist_provider_check(
            AuditRepository(database),
            event_type="PROVIDER_SUBSCRIPTION_CHECK",
            provider_id=provider_id,
            result=result,
        )
        return result

    @app.post("/api/v1/providers/chain/probe")
    async def provider_chain_probe(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Run one bounded subscription-only structured probe through ModelRouter.

        This endpoint is a connectivity proof, not a market-analysis request. It
        never receives exchange data, never creates an execution intent and is
        deliberately blocked in API mode so a UI click cannot spend API budget.
        """

        require_api_token(authorization)
        primary = settings.public.ai.primary_provider
        if settings.public.ai.primary_auth_mode != "subscription":
            return {
                "status": "BLOCKED",
                "code": "subscription_mode_required",
                "attempted_providers": [],
                "fallback_reason": None,
                "detail": "The chain probe is limited to official subscription CLIs.",
            }
        if primary == "disabled":
            return {
                "status": "BLOCKED",
                "code": "provider_chain_disabled",
                "attempted_providers": [],
                "fallback_reason": None,
                "detail": "Select one primary subscription before probing the chain.",
            }

        repository = AuditRepository(database)
        router = build_model_router(settings, models=live_ai_models(settings))
        try:
            result = await router.invoke(
                agent_id="provider_probe",
                profile="analysis",
                system_spec=(
                    "Connectivity probe only. Do not analyze markets, call tools, read files, "
                    "or propose a trade. Return JSON matching the schema with status READY and "
                    "a short summary that says the structured response was validated."
                ),
                context={"purpose": "subscription_connectivity_probe", "mode": "paper"},
                output_schema=ProviderProbeOutput,
            )
        except ProviderError as exc:
            payload: dict[str, Any] = {
                "status": "NO_TRADE",
                "code": exc.code,
                "attempted_providers": list(exc.attempted_providers),
                "fallback_reason": exc.fallback_reason,
                "detail": "No subscription provider returned a valid structured probe.",
            }
            await _persist_provider_check(
                repository,
                event_type="PROVIDER_CHAIN_PROBE",
                provider_id=primary,
                result=payload,
            )
            return payload

        payload = {
            "status": "SUCCEEDED",
            "code": "subscription_chain_validated",
            "provider": result.provider,
            "billing_mode": result.billing_mode,
            "model": result.model,
            "latency_ms": result.latency_ms,
            "attempted_providers": list(result.attempted_providers),
            "fallback_reason": result.fallback_reason,
            "detail": "Structured subscription response validated; no trade was proposed.",
        }
        await _persist_provider_check(
            repository,
            event_type="PROVIDER_CHAIN_PROBE",
            provider_id=primary,
            result=payload,
        )
        return payload

    @app.get("/api/v1/broker/status")
    async def broker_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Paper broker: connection, masked account, buying power, capabilities (read-only)."""

        require_api_token(authorization)
        try:
            reader = BrokerReader(settings, SystemClock())
        except BrokerUnavailable as exc:
            return {
                "provider": settings.public.broker.provider,
                "environment": "paper",
                "connected": False,
                "detail": exc.code,
                "capabilities": None,
                "account": None,
                "positions": [],
            }
        return await reader.status()

    @app.post("/api/v1/broker/reconcile")
    async def broker_reconcile(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Compare the paper account with local state. Never places or cancels orders."""

        require_api_token(authorization)
        return await reconcile_broker(
            settings, SystemClock(), TradingStateRepository(database)
        )

    intelligence_hub: dict[str, IntelligenceHub] = {}
    intelligence_lock = asyncio.Lock()

    async def _api_hub() -> IntelligenceHub:
        """A hub owned by the API process, used only for explicit refresh and tests."""

        if "hub" not in intelligence_hub:
            try:
                market = build_market_data(settings, SystemClock())
            except MarketDataUnavailable:
                market = None
            intelligence_hub["hub"] = IntelligenceHub(
                settings,
                database,
                SystemClock(),
                build_fetcher(settings.secrets),
                market if isinstance(market, AlpacaMarketData) else None,
            )
        return intelligence_hub["hub"]

    @app.get("/api/v1/intelligence")
    async def intelligence(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Stored Nasdaq-100 breadth, macro calendar and gate, rates, volatility, news."""

        require_api_token(authorization)
        return await stored_intelligence(settings, database, SystemClock())

    @app.post("/api/v1/intelligence/refresh")
    async def intelligence_refresh(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Read every free source now (polite, allowlisted). Never touches the broker."""

        require_api_token(authorization)
        async with intelligence_lock:
            hub = await _api_hub()
            await hub.refresh(force=True)
        return {"errors": dict(hub.state.errors), **await stored_intelligence(
            settings, database, SystemClock()
        )}

    @app.get("/api/v1/sources/status")
    async def sources_state(
        authorization: str | None = Header(default=None),
    ) -> list[dict[str, Any]]:
        """Each free data source: key present, last read, last error. No secret values."""

        require_api_token(authorization)
        return await sources_status(settings, database)

    @app.post("/api/v1/sources/{source_id}/test")
    async def source_test(
        source_id: str, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        """Read one source now so the owner can confirm a key works."""

        require_api_token(authorization)
        steps = SOURCE_TEST_STEPS.get(source_id)
        if steps is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_source"})
        # Keys may have been stored after the API started: rebuild the hub.
        intelligence_hub.pop("hub", None)
        async with intelligence_lock:
            hub = await _api_hub()
            await hub.refresh_only(steps)
        rows = {row["id"]: row for row in await sources_status(settings, database)}
        return rows[source_id]

    def _period_bounds(start: str | None, end: str | None) -> tuple[datetime | None, datetime]:
        """New York calendar dates (YYYY-MM-DD) -> UTC bounds; end is inclusive."""

        new_york = ZoneInfo("America/New_York")
        try:
            # Calendar dates only ("YYYY-MM-DD"); a datetime or offset is rejected.
            first = (
                datetime.combine(date.fromisoformat(start), datetime.min.time(), new_york)
                .astimezone(UTC)
                if start
                else None
            )
            last = (
                datetime.combine(
                    date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), new_york
                ).astimezone(UTC)
                - timedelta(microseconds=1)
                if end
                else SystemClock().now()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_date"}) from exc
        if first is not None and first > last:
            raise HTTPException(status_code=422, detail={"code": "invalid_range"})
        return first, last

    @app.get("/api/v1/reports/period")
    async def report_period(
        start: str | None = None,
        end: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """KPIs for a day, month, year or range: equity, PnL, win rate, drawdown."""

        require_api_token(authorization)
        first, last = _period_bounds(start, end)
        report = await period_report(database, start=first, end=last)
        return report.model_dump(mode="json")

    @app.get("/api/v1/reports/trades-export")
    async def report_trades_export(
        start: str | None = None,
        end: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Closed trades of the period as CSV text (the app saves it as a file)."""

        require_api_token(authorization)
        first, last = _period_bounds(start, end)
        report = await period_report(database, start=first, end=last)
        label = f"{start or 'inicio'}_{end or 'hoy'}"
        return {"filename": f"hyverion-operaciones-{label}.csv", "csv": trades_csv(report)}

    @app.get("/api/v1/reports/daily")
    async def report_daily(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        """Today's paper campaign report (§98): paper vs adjusted PnL, rejections, costs."""

        require_api_token(authorization)
        report = await daily_report(database, now=SystemClock().now())
        return report.model_dump(mode="json")

    @app.get("/api/v1/reports/live-readiness")
    async def report_live_readiness(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """§105 checklist. A report only: it can never enable LIVE."""

        require_api_token(authorization)
        report = await live_readiness(database, now=SystemClock().now())
        return report.model_dump(mode="json")

    @app.get("/api/v1/sources")
    async def sources(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
        require_api_token(authorization)
        return [
            item.model_dump(mode="json")
            for item in default_source_statuses(settings.public.external_data)
        ]

    @app.websocket("/api/v1/stream")
    async def stream(websocket: WebSocket) -> None:
        # Browsers cannot set Authorization on a WebSocket handshake, so the
        # token may also arrive as the subprotocol "hyverion.bearer.<token>".
        offered = [
            item.strip()
            for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if item.strip()
        ]
        header_ok = _bearer_matches(websocket.headers.get("authorization"), api_token)
        protocol_ok = any(
            _token_matches(item.removeprefix(WS_TOKEN_PREFIX), api_token)
            for item in offered
            if item.startswith(WS_TOKEN_PREFIX)
        )
        if not (header_ok or protocol_ok):
            await websocket.close(code=1008)
            return
        await websocket.accept(subprotocol=WS_PROTOCOL if WS_PROTOCOL in offered else None)
        try:
            while True:
                state = (await asyncio.to_thread(engine.status)).state
                await websocket.send_json(
                    await build_snapshot(
                        database,
                        settings,
                        provider_health,
                        subscription_health,
                        engine_running=state in {"running", "external"},
                    )
                )
                await asyncio.sleep(2)
        except WebSocketDisconnect:
            return

    return app
