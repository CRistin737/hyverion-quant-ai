from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
import typer
import uvicorn
import yaml
from rich.console import Console
from rich.table import Table

from trading_bot.agents.critic import DeterministicCritic
from trading_bot.agents.registry import AgentRegistry
from trading_bot.agents.runtime import AgentRuntime
from trading_bot.broker import ExecutionEngine
from trading_bot.broker.factory import build_broker
from trading_bot.broker.readonly import BrokerReader, reconcile_broker
from trading_bot.config import load_settings
from trading_bot.config.loader import live_ai_models, local_config_path
from trading_bot.config.models import Settings
from trading_bot.control_api import create_control_api
from trading_bot.core.agent_pipeline import SpecialistAgentPipeline
from trading_bot.core.clock import Clock, SystemClock
from trading_bot.core.orchestrator import MasterOrchestrator
from trading_bot.core.recovery import OperationRecoveryService
from trading_bot.core.routines import run_due_routines
from trading_bot.data.event_detector import EventDetector
from trading_bot.data.external_collector import ExternalIntelligenceCollector
from trading_bot.data.features import FeatureEngine
from trading_bot.data.sources import ConnectorSchedule, ConnectorScheduler, ConnectorState
from trading_bot.data.stream import PublicStreamCoordinator, ReplayAssessment, ReplayGapMonitor
from trading_bot.db import (
    AuditRepository,
    Database,
    OperationLifecycleRepository,
    TradingStateRepository,
    create_sqlite_backup,
)
from trading_bot.engine_supervisor import (
    EngineStartError,
    clear_flatten,
    engine_data_dir,
    flatten_pending,
    hold_engine_lock,
)
from trading_bot.intelligence.hub import IntelligenceHub, intelligence_event
from trading_bot.intelligence.reader import sources_status
from trading_bot.learning.ai_improver import run_ai_improvement
from trading_bot.learning.auto_promote import promote_proven_changes
from trading_bot.learning.optimizer_job import due as optimizer_due
from trading_bot.learning.optimizer_job import optimizer_lock, run_optimizer
from trading_bot.learning.versions import VersionRepository
from trading_bot.macro.calendar import MacroEvent
from trading_bot.macro.gate import MACRO_CALENDAR_UNAVAILABLE
from trading_bot.macro.service import MacroCalendarService
from trading_bot.market.clock import (
    NEW_YORK,
    MarketClock,
    SessionSnapshot,
    regular_session_only,
    trading_day,
)
from trading_bot.market_data import MarketDataUnavailable, build_market_data
from trading_bot.market_data.alpaca import AlpacaMarketData
from trading_bot.memory import cli as memory_cli
from trading_bot.memory.cycle import run_memory_cycle
from trading_bot.memory.maintenance import build_memory_gateway
from trading_bot.monitoring.logging import configure_logging
from trading_bot.monitoring.metrics import MetricsRegistry
from trading_bot.monitoring.plan_audit import build_plan_audit
from trading_bot.monitoring.retention import (
    DEFAULT_RETENTION_DAYS,
    apply_retention,
    export_audit_bundle,
)
from trading_bot.providers.circuit import PROCESS_HEALTH
from trading_bot.providers.cli_path import ensure_cli_path
from trading_bot.providers.factory import build_model_router
from trading_bot.reports.campaign import daily_report, live_readiness
from trading_bot.reports.period import period_report
from trading_bot.risk.engine import RiskEngine
from trading_bot.risk.session_guardian import SessionGuardian
from trading_bot.schemas.common import TradingMode
from trading_bot.schemas.trading import Candle, MarketSnapshot
from trading_bot.security.credentials import alpaca_paper_credentials
from trading_bot.service import launchd
from trading_bot.simulation.backtest import BacktestEngine, BacktestResult, WalkForwardResult
from trading_bot.simulation.costs import SIMULATION_CAPITAL_USD
from trading_bot.simulation.strategy_replay import StrategyReplayReport, replay_strategies
from trading_bot.sources.runtime import build_fetcher
from trading_bot.strategies.registry import build_strategy_ensemble, strategy_factories
from trading_bot.terminal import run_terminal

# Finder/launchd start with a bare PATH: make the AI CLIs findable.
ensure_cli_path()

app = typer.Typer(
    no_args_is_help=True, help="Hyverion Quant AI safe financial intelligence platform"
)
console = Console()
STREAM_MAINTENANCE_SECONDS = 30.0
SESSION_CANDLES = 400
PRICE_WINDOW = 60
STORED_CANDLES_PER_CYCLE = 3
app.add_typer(memory_cli.app, name="memory")
market_app = typer.Typer(no_args_is_help=True, help="US equity market session (NYSE calendar).")
app.add_typer(market_app, name="market")


broker_app = typer.Typer(no_args_is_help=True, help="Paper broker status and reconciliation.")
app.add_typer(broker_app, name="broker")


@broker_app.command("status")
def broker_status() -> None:
    """Show the paper broker: connection, masked account, buying power, capabilities."""

    async def _run() -> dict[str, object]:
        return await BrokerReader(load_settings(), SystemClock()).status()

    console.print_json(json.dumps(asyncio.run(_run()), default=str))


@broker_app.command("reconcile")
def broker_reconcile() -> None:
    """Compare the paper account with local state (read-only; SAFE MODE on mismatch)."""

    async def _run() -> dict[str, object]:
        settings = load_settings()
        database = Database(settings.public.database.url)
        await database.initialize()
        try:
            return await reconcile_broker(
                settings, SystemClock(), TradingStateRepository(database)
            )
        finally:
            await database.close()

    result = asyncio.run(_run())
    console.print_json(json.dumps(result, default=str))
    raise typer.Exit(0 if result["status"] in {"OK", "SIMULATOR"} else 1)


macro_app = typer.Typer(no_args_is_help=True, help="Economic calendar and the macro gate.")
app.add_typer(macro_app, name="macro")
sources_app = typer.Typer(no_args_is_help=True, help="Free data sources: keys, status, refresh.")
app.add_typer(sources_app, name="sources")


async def _with_database(work: Callable[[Settings, Database], Awaitable[Any]]) -> Any:
    settings = load_settings()
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        return await work(settings, database)
    finally:
        await database.close()


@macro_app.command("next")
def macro_next(days: int = typer.Option(14, min=1, max=60)) -> None:
    """Upcoming FOMC, CPI, NFP, PCE, GDP and Fed speeches (New York time) and the gate."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        clock = SystemClock()
        service = MacroCalendarService(database, build_fetcher(settings.secrets), clock)
        if await service.refreshed_at() is None:
            await service.refresh()
        now = clock.now()
        events = await service.events(start=now, end=now + timedelta(days=days))
        gate = await service.gate(settings.public.risk, now=now)
        return {
            "gate": gate.reason,
            "events": [
                {
                    "new_york": event.scheduled_at.astimezone(NEW_YORK).strftime("%a %d %b %H:%M"),
                    "importance": event.importance,
                    "type": event.event_type,
                    "title": event.title,
                }
                for event in events
                if event.importance != "LOW"
            ],
        }

    console.print_json(json.dumps(asyncio.run(_with_database(work)), default=str))


service_app = typer.Typer(
    no_args_is_help=True, help="Run the PAPER engine in the background (macOS launchd)."
)
app.add_typer(service_app, name="service")


@service_app.command("install")
def service_install(
    interval_seconds: int = typer.Option(60, "--interval-seconds", min=5, max=3600),
) -> None:
    """Start the engine now and at every login, even with the app closed."""

    typer.echo(json.dumps(launchd.install(interval_seconds).as_dict(), indent=2))


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop and remove the background engine (broker-side stops stay in place)."""

    typer.echo(json.dumps(launchd.uninstall().as_dict(), indent=2))


@service_app.command("status")
def service_status() -> None:
    typer.echo(json.dumps(launchd.status().as_dict(), indent=2))


report_app = typer.Typer(no_args_is_help=True, help="Paper campaign and live-readiness reports.")
app.add_typer(report_app, name="report")


@report_app.command("daily")
def report_daily_command() -> None:
    """Today's paper report: paper vs adjusted PnL, rejections, shadow, AI cost."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        report = await daily_report(database, now=SystemClock().now())
        return report.model_dump(mode="json")

    console.print_json(json.dumps(asyncio.run(_with_database(work)), default=str))


@report_app.command("live-readiness")
def report_live_readiness_command() -> None:
    """§105 checklist with evidence. Report only: LIVE stays disabled."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        report = await live_readiness(database, now=SystemClock().now())
        return report.model_dump(mode="json")

    console.print_json(json.dumps(asyncio.run(_with_database(work)), default=str))


@sources_app.command("status")
def sources_status_command() -> None:
    """Each free source: whether its key is stored, last read and last error."""

    async def work(settings: Settings, database: Database) -> list[dict[str, Any]]:
        return await sources_status(settings, database)

    rows = asyncio.run(_with_database(work))
    console.print_json(
        json.dumps(
            [
                {k: row[k] for k in ("id", "state", "key_present", "last_run_at", "last_error")}
                for row in rows
            ],
            default=str,
        )
    )


@sources_app.command("refresh")
def sources_refresh() -> None:
    """Read every free source now (polite, allowlisted, read-only) and store the results."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        clock = SystemClock()
        try:
            market = build_market_data(settings, clock)
        except MarketDataUnavailable:
            market = None
        hub = IntelligenceHub(
            settings,
            database,
            clock,
            build_fetcher(settings.secrets),
            market if isinstance(market, AlpacaMarketData) else None,
        )
        await hub.refresh(force=True)
        context = await hub.context()
        breadth = context.get("breadth") or {}
        return {
            "errors": context["errors"],
            "macro_gate": context["macro"]["gate"],
            "upcoming_high": [
                f"{e['scheduled_at']} {e['event_type']}"
                for e in context["macro"]["upcoming"]
                if e["importance"] == "HIGH"
            ][:8],
            "universe": context["universe"],
            "breadth": {
                key: breadth.get(key)
                for key in ("components", "pct_green", "weighted_breadth", "divergences")
            },
            "news_clusters": len(context["news"]),
            "filings": len(context["filings"]),
            "earnings": len(context["earnings"]),
            "rates": (context.get("rates") or {}).get("status"),
            "volatility": (context.get("volatility") or {}).get("status"),
        }

    console.print_json(json.dumps(asyncio.run(_with_database(work)), default=str))


@market_app.command("status")
def market_status() -> None:
    """Show the current New York session, next open/close and time bucket."""

    console.print_json(json.dumps(MarketClock(SystemClock()).snapshot().as_dict()))


@app.command()
def setup() -> None:
    """Create non-secret local configuration. Secrets are never echoed or persisted here."""
    console.print(
        "[bold]Hyverion Quant AI safe setup[/bold]\n"
        "Hyverion trades QQQ only, long-only, regular session, PAPER."
    )
    console.print(
        "Equity comes from your Alpaca PAPER account. Store its keys in the app "
        "(Ajustes > Cuenta de trading) or set ALPACA_PAPER_KEY_ID / "
        "ALPACA_PAPER_SECRET_KEY; they are never asked for here."
    )
    region = typer.prompt("Operating country code", default="US").strip().upper()
    provider = typer.prompt("Primary AI provider", default="disabled").strip().lower()
    auth_mode = typer.prompt(
        "AI authentication mode (subscription/api)", default="subscription"
    ).strip().lower()
    if auth_mode not in {"subscription", "api"}:
        raise typer.BadParameter("AI authentication mode must be subscription or api")
    news_provider = typer.prompt("News provider (or disabled)", default="disabled").strip().lower()
    social_provider = typer.prompt(
        "Social provider (or disabled)", default="disabled"
    ).strip().lower()
    local = {
        "config_version": 2,
        "trading": {
            "mode": "paper",
            "live_trading": False,
            "operating_region": region,
            "primary_instrument": "QQQ",
            "allowed_symbols": ["QQQ"],
        },
        "broker": {"provider": "alpaca", "environment": "paper"},
        "ai": {"primary_provider": provider, "primary_auth_mode": auth_mode},
        "external_data": {
            "news_provider": news_provider,
            "social_provider": social_provider,
        },
    }
    path = local_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(local, sort_keys=False), encoding="utf-8")
    console.print(f"[green]Saved non-secret {path}. LIVE remains disabled.[/green]")


@app.command()
def doctor() -> None:
    """Validate configuration, database, runtime, and safety defaults."""
    raise typer.Exit(asyncio.run(_doctor()))


@app.command(name="memory-cycle")
def memory_cycle() -> None:
    """Distill, curate, age knowledge, score agents/memories and export the Vault."""

    async def _run() -> dict[str, object]:
        settings = load_settings()
        database = Database(settings.public.database.url)
        await database.initialize()
        try:
            report = await run_memory_cycle(settings, database, SystemClock())
        finally:
            await database.close()
        return report.as_dict()

    console.print_json(json.dumps(asyncio.run(_run()), ensure_ascii=False))


maintenance_app = typer.Typer(no_args_is_help=True, help="Database housekeeping.")
app.add_typer(maintenance_app, name="maintenance")


@maintenance_app.command("purge-legacy")
def purge_legacy(
    apply: bool = typer.Option(
        False, "--apply", help="Delete the rows (a verified backup is made first)."
    ),
) -> None:
    """Remove every crypto-era row (BTC/USDT, ETH/USDT...). Dry run by default."""

    from trading_bot.db.purge import purge_legacy_assets

    settings = load_settings()

    async def run() -> None:
        database = Database(settings.public.database.url)
        try:
            await database.initialize()
            report = await purge_legacy_assets(database, apply=apply)
        finally:
            await database.close()
        console.print_json(
            json.dumps(
                {
                    "applied": report.applied,
                    "rows": report.counts,
                    "total": report.total,
                    "backup": str(report.backup_path) if report.backup_path else None,
                }
            )
        )

    asyncio.run(run())


@app.command(name="plan-audit")
def plan_audit() -> None:
    """Verify the 43-row plan matrix, agent specs and completed guides."""

    report = build_plan_audit()
    console.print_json(json.dumps(report.model_dump(), ensure_ascii=False))
    raise typer.Exit(0 if report.overall == "PASS" else 1)


@app.command(name="backup")
def backup(
    destination: str | None = typer.Option(
        None,
        "--destination",
        help="Destination SQLite file; defaults to backups/trading-bot-<UTC timestamp>.db.",
    )
) -> None:
    """Create and verify an explicit local SQLite control-plane backup."""

    settings = load_settings()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = Path(destination) if destination else Path("backups") / f"trading-bot-{timestamp}.db"
    raise typer.Exit(asyncio.run(_backup(settings.public.database.url, target)))


async def _backup(database_url: str, destination: Path) -> int:
    database = Database(database_url)
    try:
        await database.initialize()
        result = await create_sqlite_backup(database, destination)
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[bold red]BACKUP FAILED[/bold red] {type(exc).__name__}: {exc}")
        return 1
    finally:
        await database.close()
    console.print(
        {
            "status": "BACKUP_VERIFIED" if result.verified else "BACKUP_UNVERIFIED",
            "path": str(result.path),
            "size_bytes": result.size_bytes,
            "verified": result.verified,
        }
    )
    return 0


@app.command(name="retention")
def retention(
    export: str | None = typer.Option(
        None,
        "--export",
        help="Write a bounded JSONL audit export to this path and a SHA-256 manifest.",
    ),
    purge: bool = typer.Option(
        False,
        "--purge",
        help="Delete expired allowlisted audit rows; without --apply this is a dry run.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply the purge after reviewing the dry-run counts.",
    ),
) -> None:
    """Inspect or explicitly apply bounded audit retention.

    The command never purges business state. It is a dry run unless both
    ``--purge`` and ``--apply`` are supplied. Export and purge can be run
    together so an operator can archive before deleting.
    """

    if apply and not purge:
        raise typer.BadParameter("--apply requires --purge")
    settings = load_settings()
    raise typer.Exit(asyncio.run(_retention(settings.public.database.url, export, purge, apply)))


async def _retention(
    database_url: str,
    export_path: str | None,
    purge: bool,
    apply: bool,
) -> int:
    database = Database(database_url)
    try:
        await database.initialize()
        if export_path:
            exported = await export_audit_bundle(database, Path(export_path))
            console.print(
                {
                    "status": "RETENTION_EXPORT_VERIFIED",
                    "path": str(exported.data_path),
                    "manifest": str(exported.manifest_path),
                    "rows": exported.rows,
                    "sha256": exported.sha256,
                }
            )
        if purge:
            result = await apply_retention(database, dry_run=not apply)
            console.print(
                {
                    "status": "RETENTION_APPLIED" if apply else "RETENTION_DRY_RUN",
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
            )
        if not export_path and not purge:
            console.print(
                {
                    "status": "RETENTION_NOOP",
                    "eligible_tables": len(DEFAULT_RETENTION_DAYS),
                    "detail": (
                        "Use --export or --purge; purge is dry-run unless "
                        "--apply is explicit."
                    ),
                }
            )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        console.print(f"[bold red]RETENTION FAILED[/bold red] {type(exc).__name__}: {exc}")
        return 1
    finally:
        await database.close()


async def _doctor() -> int:
    """Readiness summary (§96): Broker, Environment, Instrument, Market, Data, Risk, AI, Live."""

    checks: list[tuple[str, str, str]] = []
    try:
        settings = load_settings()
        version = settings.public.config_version
        checks.append(("Configuration", "PASS", f"valid (config v{version})"))
    except Exception as exc:
        checks.append(("Configuration", "FAIL", type(exc).__name__))
        _print_checks(checks)
        return 1
    clock = SystemClock()
    trading = settings.public.trading
    checks.append(
        ("Python", "PASS" if sys.version_info[:2] == (3, 12) else "FAIL", platform.python_version())
    )
    checks.append(await _doctor_broker(settings, clock))
    checks.append(
        (
            "Environment",
            "PASS" if settings.public.broker.environment == "paper" else "FAIL",
            settings.public.broker.environment.upper(),
        )
    )
    checks.append(
        (
            "Instrument",
            "PASS" if trading.primary_instrument == "QQQ" else "FAIL",
            f"{trading.primary_instrument} (long-only, regular session)",
        )
    )
    try:
        session = MarketClock(clock).snapshot()
        market = "OPEN" if session.is_open else f"CLOSED ({session.state.value})"
        checks.append(
            ("Market", "PASS", f"{market} · next open {session.next_open.isoformat()} (NYSE)")
        )
    except Exception as exc:  # calendar data missing: sessions could not be gated
        checks.append(("Market", "FAIL", type(exc).__name__))
    checks.append(await _doctor_data(settings, clock))
    database = Database(settings.public.database.url)
    await database.initialize()
    healthy, detail = await database.healthcheck()
    await database.close()
    checks.append(("Database", "PASS" if healthy else "FAIL", detail))
    risk = settings.public.risk
    checks.append(
        (
            "Risk",
            "PASS",
            f"READY · profile {risk.profile}: {risk.base_risk_percent}% per trade, "
            f"{risk.daily_loss_percent}% daily, {risk.weekly_loss_percent}% weekly, "
            f"max {risk.max_trades_per_day} trades/day",
        )
    )
    primary = settings.public.ai.primary_provider
    fallbacks = settings.public.ai.fallback_providers
    chain_valid = (primary == "disabled" and not fallbacks) or (
        primary != "disabled" and primary not in fallbacks and len(fallbacks) == len(set(fallbacks))
    )
    chain = " -> ".join((primary, *fallbacks)) if primary != "disabled" else "OPTIONAL (disabled)"
    auth_mode = settings.public.ai.primary_auth_mode
    checks.append(("AI", "PASS" if chain_valid else "FAIL", f"{chain} ({auth_mode})"))
    checks.append(
        ("Live", "PASS" if not trading.live_trading else "FAIL", "DISABLED")
    )
    _print_checks(checks)
    return 0 if all(status != "FAIL" for _, status, _ in checks) else 1


async def _doctor_broker(settings: Settings, clock: SystemClock) -> tuple[str, str, str]:
    try:
        reader = BrokerReader(settings, clock)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        return ("Broker", "WARN", f"{settings.public.broker.provider}: {code}")
    status = await reader.status()
    if not reader.external:
        return ("Broker", "PASS", "SIMULATOR (in-process paper)")
    if not status["connected"]:
        return ("Broker", "FAIL", f"{reader.provider}: {status['detail']}")
    account = status["account"] or {}
    return (
        "Broker",
        "PASS",
        f"CONNECTED · {reader.provider} paper {account.get('account_label', '')} "
        f"· buying power ${account.get('buying_power', '?')}",
    )


async def _doctor_data(settings: Settings, clock: SystemClock) -> tuple[str, str, str]:
    try:
        snapshot = await build_market_data(settings, clock).fetch_snapshot(
            settings.public.trading.primary_instrument
        )
    except MarketDataUnavailable as exc:
        return ("Data", "WARN", f"UNAVAILABLE · {exc.code}")
    age = int((clock.now() - snapshot.event_time).total_seconds())
    return (
        "Data",
        "PASS",
        f"HEALTHY · {snapshot.provider}/{snapshot.feed} QQQ {snapshot.last} ({age}s old)",
    )


def _print_checks(checks: list[tuple[str, str, str]]) -> None:
    table = Table("Check", "Status", "Detail")
    for name, status, detail in checks:
        table.add_row(name, status, detail)
    console.print(table)


@app.command()
def collect(symbol: str = "QQQ", once: bool = typer.Option(True, "--once")) -> None:
    """Collect one market snapshot from the configured data provider."""
    if not once:
        raise typer.BadParameter("continuous collection is available through the run command")
    asyncio.run(_collect_once(symbol))


async def _collect_once(symbol: str) -> None:
    settings = load_settings()
    clock = SystemClock()
    adapter = build_market_data(settings, clock)
    snapshot = await adapter.fetch_snapshot(symbol.upper())
    database = Database(settings.public.database.url)
    await database.initialize()
    await AuditRepository(database).append(
        "market_snapshots",
        snapshot,
        created_at=clock.now(),
        asset=snapshot.symbol,
        event_time=snapshot.event_time,
        received_time=snapshot.received_time,
        processed_time=snapshot.processed_time,
    )
    await database.close()
    console.print_json(snapshot.model_dump_json())


@app.command()
def backtest(
    capital: str = "1000",
    walk_forward: bool = typer.Option(False, "--walk-forward"),
) -> None:
    """Run a deterministic cost-aware replay or OOS walk-forward replay."""
    parsed_capital = _parse_positive_decimal(capital, "capital")
    if walk_forward:
        prices = tuple(Decimal(str(value)) for value in range(100, 116))
        walk_result = BacktestEngine().run_walk_forward(
            prices,
            capital=parsed_capital,
            train_size=4,
            validation_size=3,
            test_size=3,
            step=3,
        )
        asyncio.run(_persist_backtest_result(walk_result))
        console.print_json(walk_result.model_dump_json())
        return
    prices = tuple(Decimal(value) for value in ("100", "101", "99", "103", "105", "104"))
    baseline_result = BacktestEngine().run_buy_and_hold_baseline(prices, capital=parsed_capital)
    asyncio.run(_persist_backtest_result(baseline_result))
    console.print_json(baseline_result.model_dump_json())


async def _persist_backtest_result(result: BacktestResult | WalkForwardResult) -> None:
    """Persist research metadata without treating it as a live signal."""

    settings = load_settings()
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        if isinstance(result, WalkForwardResult):
            aggregate = result.aggregate
            experiment_id = result.experiment_id
            period = "walk-forward OOS"
            payload = result.model_dump(mode="json")
        else:
            aggregate = result
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
        await AuditRepository(database).append(
            "experiments",
            payload,
            created_at=datetime.now(UTC),
        )
    finally:
        await database.close()


@app.command()
def replay(
    sessions: int = typer.Option(20, "--sessions", min=2, max=120),
    fixture: bool = typer.Option(False, "--fixture", help="Synthetic QQQ data (offline)."),
    capital: str = "1000",
) -> None:
    """Replay the QQQ strategies over recent regular sessions (research only, no orders)."""

    parsed = _parse_positive_decimal(capital, "capital")
    report = asyncio.run(_replay(sessions=sessions, fixture=fixture, capital=parsed))
    table = Table(
        "Strategy", "Trades OOS", "Win %", "Expectancy", "Profit factor", "Avg R",
        "Max DD", "Costs", "Walk-forward +", "MC DD p95", "Exp. 90% CI", "Macro skip",
    )
    for plugin in report.plugins:
        oos = plugin.out_of_sample
        positive_folds = sum(1 for fold in plugin.walk_forward if fold.net_pnl_usd > 0)
        table.add_row(
            plugin.strategy_id,
            str(oos.trades),
            f"{(oos.win_rate or 0) * 100:.0f}",
            f"{oos.expectancy_usd:.2f}" if oos.expectancy_usd is not None else "—",
            f"{oos.profit_factor:.2f}" if oos.profit_factor is not None else "—",
            f"{oos.average_r:.2f}" if oos.average_r is not None else "—",
            f"{oos.max_drawdown_usd:.2f}",
            plugin.cost_verdict,
            f"{positive_folds}/{len(plugin.walk_forward)}",
            f"{plugin.monte_carlo.max_drawdown_p95:.2f}" if plugin.monte_carlo else "—",
            (
                f"{plugin.monte_carlo.expectancy_ci_low:.2f}…"
                f"{plugin.monte_carlo.expectancy_ci_high:.2f}"
                if plugin.monte_carlo
                else "—"
            ),
            str(plugin.macro_blocked),
        )
    console.print(
        f"{report.symbol} · {report.sessions} sessions · {report.candles} bars · "
        f"{report.first_bar} → {report.last_bar}"
    )
    console.print(table)


async def _replay(*, sessions: int, fixture: bool, capital: Decimal) -> StrategyReplayReport:
    settings = load_settings()
    if fixture:
        settings = settings.model_copy(
            update={
                "public": settings.public.model_copy(
                    update={
                        "market_data": settings.public.market_data.model_copy(
                            update={"provider": "fixture"}
                        )
                    }
                )
            }
        )
    clock = SystemClock()
    end = clock.now()
    history = await build_market_data(settings, clock).fetch_candle_range(
        settings.public.trading.primary_instrument,
        start=end - timedelta(days=sessions * 7 // 5 + 4),
        end=end,
    )
    candles = regular_session_only(history)
    days = sorted({trading_day(candle.event_time) for candle in candles})[-sessions:]
    candles = tuple(c for c in candles if trading_day(c.event_time) >= days[0]) if days else ()
    config = settings.public.strategies.model_copy(update={"enabled": REPLAY_STRATEGIES})
    macro_events = () if fixture else await _replay_macro_events(settings, clock, days)
    report = replay_strategies(
        strategy_factories(config), candles, capital=capital, macro_events=macro_events
    )
    await _persist_replay(report, settings, source="fixture" if fixture else "market_data")
    return report


REPLAY_STRATEGIES = (
    "trend_pullback",
    "opening_range_breakout",
    "mean_reversion",
    "trend_momentum",
    "hyverion_strategy",
)


async def _replay_macro_events(
    settings: Settings, clock: Clock, days: list[Any]
) -> tuple[MacroEvent, ...]:
    """Official calendar for the replayed days (refreshed first if never read)."""

    if not days:
        return ()
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        service = MacroCalendarService(database, build_fetcher(settings.secrets), clock)
        if await service.refreshed_at() is None:
            await service.refresh()
        start = datetime.combine(days[0], datetime.min.time(), tzinfo=UTC)
        end = datetime.combine(days[-1], datetime.max.time(), tzinfo=UTC)
        return tuple(await service.events(start=start, end=end))
    finally:
        await database.close()


async def _persist_replay(
    report: StrategyReplayReport, settings: Settings, *, source: str
) -> None:
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        payload = report.model_dump(mode="json")
        payload.update(
            {
                "experiment_id": str(uuid4()),
                "status": "COMPLETED",
                "period": "strategy replay",
                "data_source": source,
                "net_pnl": str(
                    sum((p.out_of_sample.net_pnl_usd for p in report.plugins), Decimal("0"))
                ),
                "max_drawdown": str(
                    max((p.out_of_sample.max_drawdown_usd for p in report.plugins), default=0)
                ),
            }
        )
        await AuditRepository(database).append(
            "experiments", payload, created_at=datetime.now(UTC), asset=report.symbol
        )
    finally:
        await database.close()


@app.command()
def paper(
    fixture: bool = typer.Option(False, "--fixture"),
    capital: str | None = None,
    symbol: str | None = typer.Option(None, "--symbol"),
) -> None:
    """Run one complete PAPER decision cycle."""
    parsed = _parse_positive_decimal(capital, "capital") if capital is not None else None
    asyncio.run(
        _paper_cycle(
            fixture=fixture,
            capital_override=parsed,
            mode=TradingMode.PAPER,
            symbol_override=symbol,
        )
    )


@app.command()
def shadow(
    fixture: bool = typer.Option(False, "--fixture"),
    capital: str | None = None,
    symbol: str | None = typer.Option(None, "--symbol"),
) -> None:
    """Run one complete SHADOW decision cycle."""
    parsed = _parse_positive_decimal(capital, "capital") if capital is not None else None
    asyncio.run(
        _paper_cycle(
            fixture=fixture,
            capital_override=parsed,
            mode=TradingMode.SHADOW,
            symbol_override=symbol,
        )
    )


async def _paper_cycle(
    *,
    fixture: bool,
    capital_override: Decimal | None,
    mode: TradingMode,
    symbol_override: str | None = None,
    collect_external: bool = True,
    metrics: MetricsRegistry | None = None,
    snapshot_override: MarketSnapshot | None = None,
    prices_override: tuple[Decimal, ...] | None = None,
    protective_only: bool = False,
) -> tuple[str, ...]:
    settings = _runtime_settings()
    # An invalid config on disk: keep protecting open positions, open nothing new.
    protective_only = protective_only or _SETTINGS_INVALID is not None
    configure_logging(settings.public.app.log_level)
    # Only the internal simulator (tests, fixtures) needs a notional capital;
    # an external broker's equity is synced from its account.
    capital = capital_override or SIMULATION_CAPITAL_USD
    clock = SystemClock()
    runtime_metrics = metrics or MetricsRegistry()
    if protective_only:
        # Only deterministic exits for an asset that left the trading universe.
        if symbol_override is None:
            raise ValueError("protective_only requires an explicit symbol")
        symbol = symbol_override
        collect_external = False
    else:
        symbol = _resolve_symbol(settings.public.trading.allowed_symbols, symbol_override)
    candles: tuple[Candle, ...] = ()
    # Live cycles re-read the quote right before the risk decision (fixtures
    # and injected snapshots are frozen on purpose).
    quote_refresher: Callable[[str], Awaitable[MarketSnapshot]] | None = None
    if snapshot_override is not None or prices_override is not None:
        if snapshot_override is None or prices_override is None:
            raise ValueError("snapshot_override and prices_override must be supplied together")
        snapshot = snapshot_override
        prices = prices_override
    elif fixture:
        snapshot = _fixture_snapshot(clock.now(), symbol=symbol)
        prices = tuple(Decimal(value) for value in ("100", "101", "102", "103", "105"))
    else:
        market_data = build_market_data(settings, clock)
        quote_refresher = market_data.fetch_snapshot
        # A full regular session of 1-minute bars anchors VWAP at the 09:30 open.
        snapshot, candles = await asyncio.gather(
            market_data.fetch_snapshot(symbol),
            market_data.fetch_candles(symbol, limit=SESSION_CANDLES),
        )
        prices = tuple(candle.close for candle in candles[-PRICE_WINDOW:])
    database = Database(settings.public.database.url)
    await database.initialize()
    repository = AuditRepository(database)
    state_repository = TradingStateRepository(database)
    # Earlier cycles already stored older bars; keep only the newest ones.
    for candle in candles[-STORED_CANDLES_PER_CYCLE:]:
        await repository.append(
            "candles",
            candle,
            created_at=clock.now(),
            asset=candle.symbol,
            event_time=candle.event_time,
            received_time=candle.received_time,
            processed_time=candle.processed_time,
        )
    external_context: dict[str, object] | None = None
    collection_errors: tuple[str, ...] = ()
    # Nasdaq-100, macro, rates, news, filings and options: read-only sensors.
    intelligence = None if (fixture or protective_only) else await _intelligence(
        settings, clock, candles
    )
    macro_block_reason = (
        _macro_gate_reason(intelligence) if intelligence is not None else None
    )
    external_config = settings.public.external_data
    if collect_external and any(
        provider not in {"disabled", ""}
        for provider in (
            external_config.news_provider,
            external_config.social_provider,
        )
    ):
        collected = await ExternalIntelligenceCollector(
            external_config,
            settings.secrets,
            clock,
        ).collect(asset=symbol, now=clock.now())
        collection_errors = collected.errors
        for item in collected.news_items:
            await repository.append(
                "news_items",
                item,
                created_at=clock.now(),
                asset=item.asset,
                event_time=item.published_at,
                received_time=item.received_at,
            )
        for post in collected.social_posts:
            await repository.append(
                "social_items",
                post,
                created_at=clock.now(),
                asset=post.asset,
                event_time=post.published_at,
                received_time=clock.now(),
            )
        for source_run in collected.source_runs:
            await repository.append(
                "source_runs",
                {
                    "source_id": source_run.source_id,
                    "status": source_run.status,
                    "records_count": source_run.records_count,
                    "started_at": source_run.started_at.isoformat(),
                    "finished_at": source_run.finished_at.isoformat(),
                    "error": source_run.error,
                },
                created_at=source_run.finished_at,
                asset=symbol,
                event_time=source_run.started_at,
                received_time=source_run.finished_at,
                processed_time=source_run.finished_at,
            )
        external_context = collected.context
        if collected.errors:
            await repository.append(
                "system_events",
                {
                    "status": "EXTERNAL_COLLECTION_DEGRADED",
                    "detail": "; ".join(collected.errors),
                },
                created_at=clock.now(),
                asset=symbol,
            )
    if intelligence is not None:
        external_context = {**(external_context or {}), "intelligence": intelligence}
    preview_features = FeatureEngine().compute(snapshot.symbol, prices)
    event_detected = EventDetector(
        max_spread_bps=settings.public.risk.max_spread_bps,
    ).detect(snapshot, preview_features)
    external_has_evidence = bool(
        external_context
        and (
            external_context.get("news")
            or external_context.get("social")
            or external_context.get("errors")
        )
    ) or intelligence_event(intelligence)
    specialist_pipeline: SpecialistAgentPipeline | None = None
    if (
        not protective_only
        and settings.public.ai.primary_provider != "disabled"
        and (event_detected.interesting or external_has_evidence)
    ):
        specialist_pipeline = SpecialistAgentPipeline(
            AgentRuntime(
                registry=AgentRegistry(
                    overrides=await VersionRepository(database).agent_overrides()
                ),
                router=build_model_router(
                    settings,
                    models=live_ai_models(settings),
                    metrics=runtime_metrics,
                ),
                repository=repository,
                clock=clock,
                metrics=runtime_metrics,
            ),
            memory_gateway=(
                await build_memory_gateway(settings, database)
                if settings.public.memory.enabled
                else None
            ),
            audit=repository,
            social_enabled=settings.public.external_data.social_provider != "disabled",
        )
    # --fixture always uses the simulator; otherwise the configured paper broker.
    broker = build_broker(settings, clock, force_simulator=fixture)
    execution_engine = ExecutionEngine(
        broker,
        clock=clock,
        max_decision_age=timedelta(seconds=settings.public.risk.max_decision_age_seconds),
    )
    lifecycle_repository = OperationLifecycleRepository(database)
    # Every cycle starts from persisted state, so resolve interrupted operations
    # first. Unresolved ones stay counted and block new entries in RiskEngine.
    await OperationRecoveryService(
        lifecycle=lifecycle_repository,
        state=state_repository,
        execution_engine=execution_engine,
        repository=repository,
        clock=clock,
    ).recover()
    orchestrator = MasterOrchestrator(
        feature_engine=FeatureEngine(),
        strategy=build_strategy_ensemble(
            settings.public.strategies,
            clock=clock,
            # Human-approved strategy versions (Aprendizaje > Cambios) apply here.
            overrides=await VersionRepository(database).plugin_overrides(),
        ),
        critic=DeterministicCritic(clock, settings.public.risk.max_spread_bps),
        risk_engine=RiskEngine(
            settings.public.risk,
            clock,
            autonomous=settings.public.autonomy.mode == "paper_autonomous",
        ),
        execution_engine=execution_engine,
        repository=repository,
        clock=clock,
        specialist_pipeline=specialist_pipeline,
        state_repository=state_repository,
        lifecycle_repository=lifecycle_repository,
        max_position_hold=timedelta(minutes=settings.public.risk.max_position_hold_minutes),
        eod_flatten_minutes=settings.public.risk.eod_flatten_minutes_before_close,
        quote_refresher=quote_refresher,
        max_quote_age=timedelta(seconds=settings.public.market_data.stale_after_seconds),
        metrics=runtime_metrics,
        signal_weights=settings.public.strategies.signal_weights,
        signal_weights_version=settings.public.strategies.signal_weights_version,
    )
    external_broker = not execution_engine.simulated_venue
    starting_equity = None if external_broker else capital
    context = await state_repository.risk_context(
        mode=mode,
        starting_equity=starting_equity,
        asset=symbol,
        now=clock.now(),
        require_reconciliation=external_broker,
        macro_block_reason=macro_block_reason,
    )
    # The first risk-context read creates the account/daily projection on a
    # fresh local database. Mark-to-market can then safely persist unrealized
    # PnL without violating the daily_pnl foreign key; read once more so the
    # guardian evaluates the current mark.
    await state_repository.mark_to_market(
        asset=snapshot.symbol,
        current_price=snapshot.last,
        now=clock.now(),
    )
    context = await state_repository.risk_context(
        mode=mode,
        starting_equity=starting_equity,
        asset=symbol,
        now=clock.now(),
        require_reconciliation=external_broker,
        macro_block_reason=macro_block_reason,
    )
    session_decision = SessionGuardian(settings.public.risk, clock).evaluate(context)
    await state_repository.persist_session_decision(
        action=session_decision.action,
        reasons=session_decision.reasons,
        now=clock.now(),
    )
    result = await orchestrator.run_cycle(
        snapshot,
        prices,
        context,
        external_data=external_context,
        protective_only=protective_only,
        flatten=flatten_pending(settings),
        candles=candles,
    )
    # Re-evaluate the session after deterministic exits/entries have been
    # projected. This keeps giveback, losing-streak and high-water-mark latches
    # durable on the same cycle as the fill instead of waiting for the next
    # process tick or restart.
    post_context = await state_repository.risk_context(
        mode=mode,
        starting_equity=starting_equity,
        asset=symbol,
        now=clock.now(),
        require_reconciliation=external_broker,
        macro_block_reason=macro_block_reason,
    )
    post_session = SessionGuardian(settings.public.risk, clock).evaluate(post_context)
    await state_repository.persist_session_decision(
        action=post_session.action,
        reasons=post_session.reasons,
        now=clock.now(),
    )
    await repository.append(
        "system_events",
        {
            "status": "RUNTIME_METRICS",
            "samples": [
                {
                    "name": sample.name,
                    "value": str(sample.value),
                    "labels": dict(sample.labels),
                }
                for sample in runtime_metrics.snapshot()
            ],
        },
        created_at=clock.now(),
        asset=symbol,
    )
    await _publish_provider_circuits(repository, clock)
    await database.close()
    console.print(
        {
            "status": result.status,
            "risk": result.risk_decision.model_dump(mode="json")
            if result.risk_decision
            else None,
            "order": result.order.model_dump(mode="json") if result.order else None,
        }
    )
    return collection_errors


def _fixture_snapshot(now: datetime, *, symbol: str = "QQQ") -> MarketSnapshot:
    timestamp = now.astimezone(UTC)
    return MarketSnapshot(
        symbol=symbol,
        bid=Decimal("104.99"),
        ask=Decimal("105.00"),
        last=Decimal("105.00"),
        session_volume=Decimal("250000"),
        session_dollar_volume=Decimal("26250000"),
        provider="fixture",
        event_time=timestamp,
        received_time=timestamp,
        processed_time=timestamp,
    )


def _resolve_symbol(allowed_symbols: tuple[str, ...], requested: str | None) -> str:
    symbol = (requested or allowed_symbols[0]).strip().upper()
    if symbol not in allowed_symbols:
        raise typer.BadParameter(f"symbol must be one of: {', '.join(allowed_symbols)}")
    return symbol


def _parse_positive_decimal(value: str, name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise typer.BadParameter(f"{name} must be a decimal number") from exc
    if parsed <= 0 or not parsed.is_finite():
        raise typer.BadParameter(f"{name} must be a finite positive number")
    return parsed


@app.command(name="run")
def run_service(
    interval_seconds: int = 60,
    stream: bool = typer.Option(
        False,
        "--stream/--poll",
        help="Use the public WebSocket with replay-gap admission instead of REST polling.",
    ),
) -> None:
    """Continuously evaluate public data in PAPER mode."""
    if interval_seconds < 5:
        raise typer.BadParameter("interval-seconds must be at least 5")
    settings = load_settings()
    if settings.public.broker.provider != "simulator" and (
        alpaca_paper_credentials(settings.secrets) is None
    ):
        raise typer.BadParameter("broker_not_connected: connect the Alpaca PAPER account")
    configure_logging(settings.public.app.log_level)
    try:
        with hold_engine_lock(settings):
            # Started by the control API: stop when that process disappears.
            _watch_parent_shell()
            if stream:
                asyncio.run(_until_stopped(_run_stream_forever()))
            else:
                asyncio.run(_until_stopped(_run_forever(interval_seconds)))
    except EngineStartError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=3) from exc


async def _until_stopped(work: Coroutine[Any, Any, None]) -> None:
    """Run the engine; "Detener" (SIGTERM/SIGINT) cancels it cleanly.

    Cancelling unwinds the current step: running AI CLI calls are killed, no new
    entry starts, and an order already sent stays recorded so the next start
    reconciles it with Alpaca. Open positions keep their GTC stop at the broker.
    """

    await _close_leftover_agent_runs("engine_restarted")
    task = asyncio.ensure_future(work)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        structlog.get_logger("runtime").info("engine_stopped_by_signal")
    finally:
        await _close_leftover_agent_runs("engine_stopped")


async def _close_leftover_agent_runs(reason: str) -> None:
    """No engine means no agent is running: close any RUNNING row left behind."""

    database = Database(_runtime_settings().public.database.url)
    try:
        closed = await AuditRepository(database).cancel_running_agent_runs(reason=reason)
    except Exception as exc:
        structlog.get_logger("runtime").warning(
            "agent_runs_cleanup_failed", error=type(exc).__name__
        )
        closed = 0
    finally:
        await database.close()
    if closed:
        structlog.get_logger("runtime").info("agent_runs_closed", count=closed, reason=reason)


async def _run_stream_forever() -> None:
    """Run PAPER cycles from the public WebSocket after replay admission.

    The stream path is deliberately opt-in until an operator has observed its
    reconnect and gap events locally. It never changes the PAPER/LIVE boundary:
    a rejected or stale frame is audited and cannot reach agents or RiskEngine.
    """

    settings = load_settings()
    clock = SystemClock()
    adapter = build_market_data(settings, clock)
    symbol_prices: dict[str, deque[Decimal]] = {}
    for symbol in settings.public.trading.allowed_symbols:
        candles = await adapter.fetch_candles(symbol)
        symbol_prices[symbol] = deque((candle.close for candle in candles), maxlen=120)
    metrics = MetricsRegistry()
    monitor = ReplayGapMonitor(
        max_gap_seconds=Decimal(
            str(max(30, settings.public.market_data.stale_after_seconds * 3))
        ),
        stale_after_seconds=Decimal(str(settings.public.market_data.stale_after_seconds)),
    )
    database = Database(settings.public.database.url)
    await database.initialize()
    repository = AuditRepository(database)
    stop_event = asyncio.Event()

    async def handle(snapshot: MarketSnapshot, assessment: ReplayAssessment) -> None:
        if not MarketClock(clock).is_open():
            # Outside the regular session frames only feed protective exits.
            if snapshot.symbol in await _open_position_assets(settings):
                with contextlib.suppress(Exception):
                    await _paper_cycle(
                        fixture=False,
                        capital_override=None,
                        mode=TradingMode.PAPER,
                        symbol_override=snapshot.symbol,
                        collect_external=False,
                        metrics=metrics,
                        protective_only=True,
                    )
            return
        if not assessment.accepted:
            metrics.increment(
                "market_stream_rejections_total",
                labels={"symbol": snapshot.symbol, "reason": assessment.status.lower()},
            )
            await repository.append(
                "system_events",
                {
                    "status": "MARKET_STREAM_REJECTED",
                    "symbol": snapshot.symbol,
                    "reason": assessment.status,
                    "detail": assessment.detail,
                    "event_time": assessment.event_time.isoformat(),
                    "previous_event_time": (
                        assessment.previous_event_time.isoformat()
                        if assessment.previous_event_time
                        else None
                    ),
                    "gap_seconds": str(assessment.gap_seconds),
                    "age_seconds": str(assessment.age_seconds),
                },
                created_at=clock.now(),
                asset=snapshot.symbol,
                event_time=snapshot.event_time,
                received_time=snapshot.received_time,
                processed_time=snapshot.processed_time,
            )
            return
        prices = symbol_prices[snapshot.symbol]
        prices.append(snapshot.last)
        if len(prices) < 5:
            return
        try:
            await _paper_cycle(
                fixture=False,
                capital_override=None,
                mode=TradingMode.PAPER,
                symbol_override=snapshot.symbol,
                collect_external=False,
                metrics=metrics,
                snapshot_override=snapshot,
                prices_override=tuple(prices),
            )
        except Exception as exc:  # a stream frame must fail closed, not stop other symbols
            metrics.increment(
                "market_stream_cycle_failures_total",
                labels={"symbol": snapshot.symbol, "error": type(exc).__name__},
            )
            await repository.append(
                "system_events",
                {
                    "status": "MARKET_STREAM_CYCLE_FAILED",
                    "detail": type(exc).__name__,
                },
                created_at=clock.now(),
                asset=snapshot.symbol,
            )

    async def maintenance() -> None:
        # The stream only fires for subscribed symbols; orphans and flatten
        # completion need their own cadence.
        while not stop_event.is_set():
            try:
                await _maintenance_tick(settings, settings.public.trading.allowed_symbols, metrics)
            except Exception as exc:  # never let the safety loop die silently
                await repository.append(
                    "system_events",
                    {"status": "PAPER_CYCLE_FAILED", "detail": type(exc).__name__},
                    created_at=clock.now(),
                )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), STREAM_MAINTENANCE_SECONDS)

    maintenance_task = asyncio.create_task(maintenance())
    try:
        await PublicStreamCoordinator(
            adapter,
            monitor=monitor,
            max_reconnect_delay_seconds=settings.public.market_data.max_reconnect_delay_seconds,
        ).run(
            settings.public.trading.allowed_symbols,
            stop_event=stop_event,
            handler=handle,
        )
    finally:
        stop_event.set()
        with contextlib.suppress(Exception):
            await maintenance_task
        await database.close()


_LAST_GOOD_SETTINGS: Settings | None = None
_SETTINGS_INVALID: str | None = None


def _runtime_settings() -> Settings:
    """Settings for the running engine; never let a bad edit take it down.

    If ``local.yaml``/``autonomy.yaml`` becomes invalid while the engine runs
    (out of the envelope, LIVE without USD caps, a typo), keep the last valid
    settings and flag it: only protective exits run until the file is fixed.
    Without this the engine would exit and launchd would restart it in a loop,
    leaving open positions without their time and end-of-day exits.
    """

    global _LAST_GOOD_SETTINGS, _SETTINGS_INVALID
    try:
        settings = load_settings()
    except (ValueError, OSError, yaml.YAMLError) as exc:
        if _LAST_GOOD_SETTINGS is None:
            raise
        _SETTINGS_INVALID = type(exc).__name__
        return _LAST_GOOD_SETTINGS
    _LAST_GOOD_SETTINGS, _SETTINGS_INVALID = settings, None
    return settings


async def _run_forever(interval_seconds: int) -> None:
    logger = structlog.get_logger("runtime")
    runtime_metrics = MetricsRegistry()
    scheduler: ConnectorScheduler | None = None
    scheduled_symbols: tuple[str, ...] = ()
    reported_invalid: str | None = None
    while True:
        settings = _runtime_settings()
        if _SETTINGS_INVALID != reported_invalid:
            reported_invalid = _SETTINGS_INVALID
            if reported_invalid:
                logger.error("config_invalid_protective_only", error=reported_invalid)
                await _record_runtime_event(
                    settings, "CONFIG_INVALID", {"error": reported_invalid, "entries": "blocked"}
                )
        # Restart reconciliation before the first entry (entries stay blocked until OK).
        await _reconcile_broker_if_due(settings)
        symbols = settings.public.trading.allowed_symbols
        external_enabled = any(
            provider not in {"disabled", ""}
            for provider in (
                settings.public.external_data.news_provider,
                settings.public.external_data.social_provider,
            )
        )
        session = MarketClock(SystemClock()).snapshot()
        if not session.is_open:
            # US equities are not 24/7: outside the regular session there are no
            # new entries. Keep protecting any open position, then sleep until
            # the next pre-market instead of failing every cycle on stale data.
            await _wait_for_session(settings, session, runtime_metrics)
            continue
        if external_enabled and symbols != scheduled_symbols:
            now = datetime.now(UTC)
            scheduler = ConnectorScheduler(
                tuple(
                    ConnectorSchedule(
                        source_id=f"external:{symbol}",
                        interval_seconds=settings.public.external_data.collection_interval_seconds,
                        max_backoff_seconds=settings.public.external_data.collection_max_backoff_seconds,
                    )
                    for symbol in symbols
                ),
                now=now,
            )
            await _restore_connector_scheduler_state(settings, scheduler)
            scheduled_symbols = symbols
        elif not external_enabled:
            scheduler = None
            scheduled_symbols = ()
        for symbol in symbols:
            now = datetime.now(UTC)
            schedule_id = f"external:{symbol}"
            collect_external = scheduler is not None and scheduler.due(schedule_id, now=now)
            try:
                collection_errors = await _paper_cycle(
                    fixture=False,
                    capital_override=None,
                    mode=TradingMode.PAPER,
                    symbol_override=symbol,
                    collect_external=collect_external,
                    metrics=runtime_metrics,
                )
                if scheduler is not None and collect_external:
                    if collection_errors:
                        state = scheduler.mark_failure(
                            schedule_id,
                            now=datetime.now(UTC),
                            error="; ".join(collection_errors),
                        )
                    else:
                        state = scheduler.mark_success(schedule_id, now=datetime.now(UTC))
                    await _persist_connector_scheduler_state(settings, state)
            except Exception as exc:
                if scheduler is not None and collect_external:
                    state = scheduler.mark_failure(
                        schedule_id,
                        now=datetime.now(UTC),
                        error=type(exc).__name__,
                    )
                    await _persist_connector_scheduler_state(settings, state)
                error = exc.code if isinstance(exc, MarketDataUnavailable) else type(exc).__name__
                logger.error("paper_cycle_failed_closed", symbol=symbol, error_type=error)
                await _record_runtime_event(
                    settings,
                    "PAPER_CYCLE_FAILED",
                    {"symbol": symbol, "error": error},
                )
        await _maintenance_tick(settings, symbols, runtime_metrics)
        await _protect_until_next_cycle(
            settings,
            interval_seconds,
            runtime_metrics,
            sleep=asyncio.sleep,
            monotonic=time.monotonic,
        )


MARKET_CLOSED_MAX_SLEEP_SECONDS = 900.0
_last_closed_event: str | None = None


async def _wait_for_session(
    settings: Settings,
    session: SessionSnapshot,
    metrics: MetricsRegistry,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Market closed: protect open positions, record the wait once, then sleep."""

    global _last_closed_event
    for asset in await _open_position_assets(settings):
        try:
            await _paper_cycle(
                fixture=False,
                capital_override=None,
                mode=TradingMode.PAPER,
                symbol_override=asset,
                collect_external=False,
                metrics=metrics,
                protective_only=True,
            )
        except Exception as exc:
            await _record_runtime_event(
                settings,
                "POSITION_PROTECTION_FAILED",
                {"symbol": asset, "error": type(exc).__name__},
            )
    marker = f"{session.state.value}:{session.next_open.isoformat()}"
    if marker != _last_closed_event:
        _last_closed_event = marker
        await _record_runtime_event(
            settings,
            "MARKET_CLOSED_WAITING",
            {"session": session.state.value, "next_open": session.next_open.isoformat()},
        )
    await _maintenance_tick(settings, settings.public.trading.allowed_symbols, metrics)
    wait = MarketClock(SystemClock()).seconds_until_pre_market()
    # Short naps keep stop/config changes responsive; never below the check cadence.
    await sleep(
        max(
            float(settings.public.risk.position_check_seconds),
            min(wait, MARKET_CLOSED_MAX_SLEEP_SECONDS),
        )
    )


async def _protect_until_next_cycle(
    settings: Settings,
    interval_seconds: int,
    metrics: MetricsRegistry,
    *,
    sleep: Callable[[float], Awaitable[None]],
    monotonic: Callable[[], float],
) -> None:
    """Wait for the next entry cycle while re-checking open positions.

    Entry analysis runs every ``interval_seconds``; deterministic exits for open
    positions run every ``risk.position_check_seconds`` in between (protective
    only: no AI, no new entries).
    """

    step = settings.public.risk.position_check_seconds
    deadline = monotonic() + interval_seconds
    while (remaining := deadline - monotonic()) > 0:
        await sleep(min(step, remaining))
        if deadline - monotonic() <= 0:
            return
        for asset in await _open_position_assets(settings):
            try:
                await _paper_cycle(
                    fixture=False,
                    capital_override=None,
                    mode=TradingMode.PAPER,
                    symbol_override=asset,
                    collect_external=False,
                    metrics=metrics,
                    protective_only=True,
                )
            except Exception as exc:
                await _record_runtime_event(
                    settings,
                    "POSITION_PROTECTION_FAILED",
                    {"symbol": asset, "error": type(exc).__name__},
                )


async def _open_position_assets(settings: Settings) -> tuple[str, ...]:
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        positions = await TradingStateRepository(database).open_positions()
    finally:
        await database.close()
    return tuple(sorted({str(row["asset"]) for row in positions if row.get("asset")}))


async def _maintenance_tick(
    settings: Settings, symbols: tuple[str, ...], metrics: MetricsRegistry
) -> None:
    """Safety work shared by the poll and stream loops.

    A position must never be abandoned because its symbol left the trading
    universe (config change): keep running its deterministic exits. Then close
    out an operator flatten request once everything is flat.
    """

    logger = structlog.get_logger("runtime")
    for orphan in await _orphan_position_assets(settings, symbols):
        try:
            await _paper_cycle(
                fixture=False,
                capital_override=None,
                mode=TradingMode.PAPER,
                symbol_override=orphan,
                collect_external=False,
                metrics=metrics,
                protective_only=True,
            )
        except Exception as exc:
            logger.error(
                "orphan_position_protection_failed",
                symbol=orphan,
                error_type=type(exc).__name__,
            )
            await _record_runtime_event(
                settings,
                "POSITION_PROTECTION_FAILED",
                {"symbol": orphan, "error": type(exc).__name__},
            )
    await _complete_flatten_if_flat(settings)
    await _reconcile_broker_if_due(settings)
    _schedule_daily_optimizer(settings)
    _schedule_daily_routines(settings)


_hub: IntelligenceHub | None = None
_hub_database: Database | None = None


async def _intelligence(
    settings: Settings, clock: Clock, candles: tuple[Candle, ...]
) -> dict[str, Any] | None:
    """Refresh due sensors and return the intelligence envelope (never raises).

    A failing sensor is reported inside the envelope; a failing hub leaves the
    cycle without intelligence, and the macro gate then fails closed.
    """

    global _hub, _hub_database
    if os.getenv("HYVERION_INTELLIGENCE", "1") == "0":
        return None  # tests and offline runs: no network sensors
    try:
        if _hub is None:
            _hub_database = Database(settings.public.database.url)
            await _hub_database.initialize()
            try:
                market = build_market_data(settings, clock)
            except MarketDataUnavailable:
                market = None
            _hub = IntelligenceHub(
                settings,
                _hub_database,
                clock,
                build_fetcher(settings.secrets),
                market if isinstance(market, AlpacaMarketData) else None,
            )
        await _hub.refresh(candles=candles)
        return await _hub.context()
    except Exception as exc:  # a sensor outage must never stop protective exits
        structlog.get_logger("runtime").warning(
            "intelligence_unavailable", error_type=type(exc).__name__
        )
        return {"macro": {"gate": MACRO_CALENDAR_UNAVAILABLE}, "errors": {"hub": "unavailable"}}


def _macro_gate_reason(intelligence: dict[str, Any]) -> str | None:
    macro = intelligence.get("macro") or {}
    reason = macro.get("gate") if isinstance(macro, dict) else MACRO_CALENDAR_UNAVAILABLE
    return str(reason) if reason else None


RECONCILE_EVERY_SECONDS = 600.0
_last_reconciliation: float | None = None


async def _reconcile_broker_if_due(
    settings: Settings, *, monotonic: Callable[[], float] = time.monotonic
) -> None:
    """External paper broker: reconcile at start and every 10 minutes (fail closed)."""

    global _last_reconciliation
    if settings.public.broker.provider == "simulator":
        return
    now = monotonic()
    if _last_reconciliation is not None and now - _last_reconciliation < RECONCILE_EVERY_SECONDS:
        return
    _last_reconciliation = now
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        state = TradingStateRepository(database)
        try:
            result = await reconcile_broker(settings, SystemClock(), state)
        except Exception as exc:  # never let reconciliation take the loop down
            # Recorded as a failed reconciliation, so entries stay blocked.
            await state.record_reconciliation_error(type(exc).__name__, now=SystemClock().now())
            result = {"status": "ERROR", "detail": type(exc).__name__}
    finally:
        await database.close()
    if result["status"] not in {"OK", "SIMULATOR"}:
        structlog.get_logger("runtime").warning(
            "broker_reconciliation_blocked", status=result["status"], detail=result.get("detail")
        )


_OPTIMIZER_TASKS: set[asyncio.Task[None]] = set()


def _schedule_daily_optimizer(settings: Settings) -> None:
    """Start the once-a-day optimizer pass in the background (never blocks cycles).

    It only writes reviewable proposals; the owner applies them in the app.
    """

    if _OPTIMIZER_TASKS:
        return
    task = asyncio.create_task(_daily_optimizer(settings))
    _OPTIMIZER_TASKS.add(task)
    task.add_done_callback(_OPTIMIZER_TASKS.discard)


_ROUTINE_TASKS: set[asyncio.Task[None]] = set()


def _schedule_daily_routines(settings: Settings) -> None:
    """Pre-market, close review, nightly improvement and weekly review (background)."""

    if _ROUTINE_TASKS:
        return
    task = asyncio.create_task(_daily_routines(settings))
    _ROUTINE_TASKS.add(task)
    task.add_done_callback(_ROUTINE_TASKS.discard)


async def _daily_routines(settings: Settings) -> None:
    clock = SystemClock()
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        now = clock.now()

        async def premarket() -> dict[str, Any]:
            try:
                market = build_market_data(settings, clock)
            except MarketDataUnavailable:
                market = None
            hub = IntelligenceHub(
                settings,
                database,
                clock,
                build_fetcher(settings.secrets),
                market if isinstance(market, AlpacaMarketData) else None,
            )
            await hub.refresh(force=True)
            context = await hub.context()
            return {"errors": len(context.get("errors") or ())}

        async def close_review() -> dict[str, Any]:
            report = await daily_report(database, now=clock.now())
            memory = await run_memory_cycle(settings, database, clock)
            return {
                "entries": report.entries,
                "rejected": report.rejected,
                "broker_paper_pnl_usd": str(report.broker_paper_pnl_usd),
                "memory": memory.as_dict(),
            }

        async def history(symbol: str, minutes: int) -> tuple[Candle, ...]:
            # Built lazily: a market-data outage must not skip the other routines.
            adapter = build_market_data(settings, clock)
            end = clock.now()
            return await adapter.fetch_candle_range(
                symbol, start=end - timedelta(minutes=minutes), end=end
            )

        async def nightly_improvement() -> dict[str, Any]:
            improvement = await run_ai_improvement(
                settings, database, clock, candle_history=history, runner=asyncio.to_thread
            )
            promoted = await promote_proven_changes(
                settings, database, clock, candle_history=history
            )
            return {**improvement, "auto_promotion": promoted}

        async def weekly_review() -> dict[str, Any]:
            report = await period_report(database, start=None, end=clock.now(), days=7)
            return report.summary()

        await run_due_routines(
            AuditRepository(database),
            now,
            is_trading_day=MarketClock(clock).snapshot().is_session_day,
            actions={
                "premarket": premarket,
                "close_review": close_review,
                "nightly_improvement": nightly_improvement,
                "weekly_review": weekly_review,
            },
        )
    except Exception as exc:  # never break the trading loop
        structlog.get_logger("runtime").error("daily_routines_failed", error=type(exc).__name__)
    finally:
        await database.close()


async def _daily_optimizer(settings: Settings) -> None:
    clock = SystemClock()
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        if not await optimizer_due(database, clock.now()):
            return
        with optimizer_lock(engine_data_dir(settings)) as acquired:
            if not acquired:
                return
            adapter = build_market_data(settings, clock)

            async def history(symbol: str, minutes: int) -> tuple[Candle, ...]:
                end = clock.now()
                return await adapter.fetch_candle_range(
                    symbol, start=end - timedelta(minutes=minutes), end=end
                )

            await run_optimizer(
                database=database,
                strategies=settings.public.strategies,
                symbols=settings.public.trading.allowed_symbols,
                capital=SIMULATION_CAPITAL_USD,
                candle_history=history,
                clock=clock,
                runner=asyncio.to_thread,
            )
    except Exception as exc:  # data outage: try again on the next due check
        structlog.get_logger("runtime").warning(
            "optimizer_failed", error_type=type(exc).__name__
        )
    finally:
        await database.close()


async def _complete_flatten_if_flat(settings: Settings) -> None:
    """Close out an operator flatten request once no position remains open."""

    if not flatten_pending(settings):
        return
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        if await TradingStateRepository(database).open_positions():
            return
        clear_flatten(settings)
        await AuditRepository(database).append(
            "system_events",
            {"status": "OPERATOR_FLATTEN_COMPLETED", "source": "engine"},
            created_at=datetime.now(UTC),
        )
    finally:
        await database.close()


_last_published_circuits: str | None = None


async def _publish_provider_circuits(repository: AuditRepository, clock: SystemClock) -> None:
    """Tell the control API which subscriptions are paused (it runs in another process)."""

    global _last_published_circuits
    now = clock.now()
    circuits = {
        provider_id: {
            "state": entry["state"],
            "failures": entry["failures"],
            "last_code": entry["last_code"],
            "retry_at": (now + timedelta(seconds=int(entry["retry_in_seconds"]))).isoformat(),
        }
        for provider_id, entry in PROCESS_HEALTH.snapshot().items()
    }
    fingerprint = json.dumps(sorted(circuits), sort_keys=True)
    # While anything is paused keep the event fresh; publish one "cleared" event after.
    if not circuits and _last_published_circuits in (None, "[]"):
        return
    _last_published_circuits = fingerprint
    await repository.append(
        "system_events",
        {"status": "PROVIDER_CIRCUIT", "circuits": circuits},
        created_at=now,
    )


async def _orphan_position_assets(settings: Settings, symbols: tuple[str, ...]) -> tuple[str, ...]:
    """Assets with an open position that the current universe no longer covers."""

    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        positions = await TradingStateRepository(database).open_positions()
    finally:
        await database.close()
    assets = {str(row.get("asset")) for row in positions if row.get("asset")}
    return tuple(sorted(assets - set(symbols)))


async def _record_runtime_event(settings: Settings, status: str, detail: dict[str, str]) -> None:
    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        await AuditRepository(database).append(
            "system_events",
            {"status": status, **detail},
            created_at=datetime.now(UTC),
        )
    finally:
        await database.close()


async def _restore_connector_scheduler_state(
    settings: Settings, scheduler: ConnectorScheduler
) -> None:
    """Restore the last bounded source state without trusting arbitrary payloads."""

    database = Database(settings.public.database.url)
    await database.initialize()
    try:
        rows = await AuditRepository(database).with_status(
            "system_events", ["CONNECTOR_SCHEDULER_STATE"], limit=200
        )
    finally:
        await database.close()
    restored: set[str] = set()
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict) or payload.get("status") != "CONNECTOR_SCHEDULER_STATE":
            continue
        source_id = str(payload.get("source_id") or "")
        if not source_id or source_id in restored:
            continue
        try:
            next_run_at = datetime.fromisoformat(str(payload["next_run_at"]).replace("Z", "+00:00"))
            paused_value = payload.get("paused_until")
            paused_until = (
                datetime.fromisoformat(str(paused_value).replace("Z", "+00:00"))
                if paused_value
                else None
            )
            state = ConnectorState(
                source_id=source_id,
                next_run_at=next_run_at,
                consecutive_failures=int(payload.get("consecutive_failures") or 0),
                paused_until=paused_until,
                last_error=str(payload.get("last_error")) if payload.get("last_error") else None,
            )
            scheduler.restore(state)
            restored.add(source_id)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            # A malformed persisted scheduler record must not create a new
            # source or bypass the default due-time; the safe state is simply
            # to leave that configured source at its initial schedule.
            continue


async def _persist_connector_scheduler_state(settings: Settings, state: ConnectorState) -> None:
    """Persist only scheduling metadata; credentials and external text stay out."""

    database = Database(settings.public.database.url)
    now = datetime.now(UTC)
    await database.initialize()
    try:
        await AuditRepository(database).append(
            "system_events",
            {
                "status": "CONNECTOR_SCHEDULER_STATE",
                "source_id": state.source_id,
                "next_run_at": state.next_run_at.isoformat(),
                "consecutive_failures": state.consecutive_failures,
                "paused_until": state.paused_until.isoformat() if state.paused_until else None,
                "last_error": state.last_error,
            },
            created_at=now,
            asset=state.source_id,
            event_time=now,
            received_time=now,
            processed_time=now,
        )
    finally:
        await database.close()


@app.command(name="api")
def api(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Start the internal JSON/WebSocket control API (no browser UI)."""
    remote_bind_allowed = os.getenv("API_ALLOW_REMOTE_BIND") == "true"
    settings = load_settings()
    if host not in {"127.0.0.1", "localhost"} and not remote_bind_allowed:
        raise typer.BadParameter("remote API binds require an authenticated private network")
    if (
        settings.secrets.control_api_token is None
        or not settings.secrets.control_api_token.get_secret_value().strip()
    ):
        # Fail closed: an unauthenticated loopback API is reachable by any local web page.
        raise typer.BadParameter("define CONTROL_API_TOKEN antes de iniciar la API de control")
    _watch_parent_shell()
    uvicorn.run(create_control_api(settings), host=host, port=port)


def _watch_parent_shell(interval_seconds: float = 2.0) -> None:
    """Stop the API when the desktop shell that launched it is gone.

    The Tauri shell keeps our stdin open (HYVERION_SHELL_STDIN_WATCH=1) and
    passes its PID in HYVERION_SHELL_PID. When the pipe reaches EOF or the PID
    disappears (crash or force quit), this sends SIGTERM to ourselves so uvicorn
    shuts down gracefully instead of leaving an orphaned control API.
    """
    if os.getenv("HYVERION_SHELL_STDIN_WATCH") == "1":
        # The shell holds the write end of our stdin; EOF means it is gone,
        # with no PID-reuse window.
        def watch_stdin() -> None:
            while sys.stdin.buffer.read(1):
                pass
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(target=watch_stdin, name="hyverion-shell-pipe", daemon=True).start()
    raw = os.getenv("HYVERION_SHELL_PID", "").strip()
    if not raw.isdigit():
        return
    shell_pid = int(raw)

    def watch() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                os.kill(shell_pid, 0)
            except ProcessLookupError:
                os.kill(os.getpid(), signal.SIGTERM)
                return
            except PermissionError:
                continue

    threading.Thread(target=watch, name="hyverion-shell-watchdog", daemon=True).start()


DESKTOP_APP_CANDIDATES = (
    Path("/Applications/Hyverion Quant AI.app"),
    Path.home() / "Applications" / "Hyverion Quant AI.app",
    Path(__file__).resolve().parents[2]
    / "app/src-tauri/target/release/bundle/macos/Hyverion Quant AI.app",
)


@app.command()
def desktop() -> None:
    """Abrir la app de escritorio (Tauri). La app inicia su propio núcleo local."""
    if sys.platform != "darwin":
        raise typer.BadParameter("la app de escritorio solo está disponible en macOS")
    for candidate in DESKTOP_APP_CANDIDATES:
        if candidate.exists():
            subprocess.run(["/usr/bin/open", str(candidate)], check=True)  # noqa: S603
            return
    console.print(
        "No se encontró la app de escritorio. Compílala con:\n"
        "  cd app && pnpm install && pnpm build:app\n"
        "o ejecútala en modo desarrollo con: cd app && pnpm tauri dev",
        highlight=False,
        markup=False,
    )
    raise typer.Exit(1)


@app.command(name="terminal")
def terminal(interval_seconds: int = 3, once: bool = False) -> None:
    """Start the primary local terminal control surface."""
    if interval_seconds < 1:
        raise typer.BadParameter("interval-seconds must be at least 1")
    asyncio.run(run_terminal(load_settings(), interval_seconds=interval_seconds, once=once))


@app.command()
def live(confirm_live: bool = typer.Option(False, "--confirm-live")) -> None:
    """Evaluate LIVE gates. This release intentionally cannot submit real orders."""
    settings = load_settings()
    blockers = []
    if not confirm_live:
        blockers.append("missing --confirm-live")
    if not settings.public.trading.live_trading:
        blockers.append("LIVE_TRADING is false")
    blockers.append(f"broker environment is {settings.public.broker.environment}")
    blockers.append("no live broker adapter or live credential profile exists in this release")
    console.print("[bold red]LIVE BLOCKED[/bold red]")
    for blocker in blockers:
        console.print(f"- {blocker}")
    raise typer.Exit(2)


if __name__ == "__main__":
    app()
