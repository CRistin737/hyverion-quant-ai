"""``python -m trading_bot memory ...``: operator CLI over ``MemoryOperations``.

Reads print JSON. ``promote`` and ``retire`` are human decisions and require a
reason; every other command is deterministic maintenance.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from trading_bot.config import load_settings
from trading_bot.config.models import Settings
from trading_bot.core.clock import SystemClock
from trading_bot.db.database import Database
from trading_bot.db.lifecycle import OperationLifecycleRepository
from trading_bot.db.repositories import AuditRepository
from trading_bot.db.state import TradingStateRepository
from trading_bot.memory.distiller import TradeReviewDistiller
from trading_bot.memory.knowledge_lifecycle import KnowledgeLifecycleManager
from trading_bot.memory.maintenance import (
    MemoryIndexer,
    WorkingMemoryRebuilder,
    build_memory_gateway,
    semantic_stack,
)
from trading_bot.memory.models import KnowledgeStatus
from trading_bot.memory.operations import (
    MemoryHealthService,
    MemoryOperations,
    MemoryOperatorError,
    memory_summary,
)
from trading_bot.memory.repository import SqlMemoryRepository
from trading_bot.memory.research import MemoryReplayEvaluator
from trading_bot.memory.vault import FileVaultRepository
from trading_bot.memory.working import WorkingMemoryUnavailable, build_working_memory

app = typer.Typer(no_args_is_help=True, help="Inspect and operate the three-layer memory.")
console = Console()
OPERATOR = "operator-cli"


def _run[T](work: Callable[[Settings, Database], Awaitable[T]]) -> T:
    async def _main() -> T:
        settings = load_settings()
        database = Database(settings.public.database.url)
        await database.initialize()
        try:
            return await work(settings, database)
        finally:
            await database.close()

    return asyncio.run(_main())


def _operations(settings: Settings, database: Database) -> MemoryOperations:
    vault = FileVaultRepository(settings.public.memory.vault_path)
    vault.ensure_layout()
    return MemoryOperations(database=database, clock=SystemClock(), vault=vault)


def _print(value: Any) -> None:
    console.print_json(json.dumps(value, ensure_ascii=False, default=str))


@app.command()
def status() -> None:
    """Health of every memory layer plus knowledge counts and agent reliability."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        health = await MemoryHealthService(
            settings=settings, database=database, clock=SystemClock()
        ).check()
        overview = await _operations(settings, database).overview()
        return {"health": asdict(health), "overview": asdict(overview)}

    report = _run(work)
    _print(report)
    raise typer.Exit(1 if report["health"]["overall"] == "FAILED" else 0)


@app.command()
def search(
    text: str = typer.Argument("", help="Words in the title, summary or KNOW- id."),
    symbol: str | None = typer.Option(None),
    strategy: str | None = typer.Option(None),
    regime: str | None = typer.Option(None),
    status: list[KnowledgeStatus] = typer.Option(  # noqa: B008 - typer option declaration
        [], "--status", help="Repeatable; defaults to every status."
    ),
    min_reliability: float | None = typer.Option(None, min=0, max=1),
    limit: int = typer.Option(50, min=1, max=500),
) -> None:
    """Filter knowledge by text, symbol, strategy, regime, status and reliability."""

    async def work(settings: Settings, database: Database) -> list[dict[str, Any]]:
        return await _operations(settings, database).knowledge(
            text=text or None,
            symbol=symbol,
            strategy=strategy,
            market_regime=regime,
            statuses=frozenset(status) if status else frozenset(KnowledgeStatus),
            min_reliability=(
                Decimal(str(min_reliability)) if min_reliability is not None else None
            ),
            limit=limit,
        )

    _print(_run(work))


@app.command()
def inspect(identifier: str) -> None:
    """Evidence, versions, performance, usage, related trades and conflicts of one memory."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        return await _operations(settings, database).inspect(identifier)

    try:
        _print(_run(work))
    except LookupError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def candidates(
    include_closed: bool = typer.Option(False, "--all", help="Include promoted/rejected."),
) -> None:
    """Memory candidates waiting for (or past) curation."""

    async def work(settings: Settings, database: Database) -> list[dict[str, Any]]:
        return await _operations(settings, database).candidates(include_closed=include_closed)

    _print(_run(work))


@app.command()
def conflicts() -> None:
    """Recorded contradictions between knowledge items."""

    async def work(settings: Settings, database: Database) -> list[dict[str, Any]]:
        return await _operations(settings, database).conflicts()

    _print(_run(work))


def _decision(action: str, identifier: str, reason: str) -> None:
    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        operations = _operations(settings, database)
        if action == "promote":
            memory = await operations.confirm(identifier, reason=reason, operator=OPERATOR)
        else:
            memory = await operations.retire(identifier, reason=reason, operator=OPERATOR)
        return memory_summary(memory)

    try:
        _print(_run(work))
    except (LookupError, MemoryOperatorError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def promote(
    identifier: str,
    reason: str = typer.Option(..., "--reason", help="Why a human confirms this knowledge."),
) -> None:
    """Confirm ACTIVE knowledge with enough evidence as CONFIRMED_KNOWLEDGE."""

    _decision("promote", identifier, reason)


@app.command()
def retire(
    identifier: str,
    reason: str = typer.Option(..., "--reason", help="Why this knowledge stops being used."),
) -> None:
    """Retire knowledge permanently (versioned, audited, never deleted)."""

    _decision("retire", identifier, reason)


@app.command()
def distill() -> None:
    """Daily review: distill the last 7 days of evaluated trades into candidates."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        return asdict(await (await _distiller(settings, database)).daily_review())

    _print(_run(work))


@app.command()
def synthesize() -> None:
    """Weekly synthesis: distill the last 30 days of evaluated trades into candidates."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        return asdict(await (await _distiller(settings, database)).weekly_synthesis())

    _print(_run(work))


@app.command()
def validate() -> None:
    """Record outcomes, apply decay/revalidation and detect contradictions."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        vault = FileVaultRepository(settings.public.memory.vault_path)
        vault.ensure_layout()
        report = await KnowledgeLifecycleManager(
            repository=SqlMemoryRepository(database),
            audit=AuditRepository(database),
            clock=SystemClock(),
            vault=vault,
        ).run()
        return asdict(report)

    _print(_run(work))


@app.command()
def replay(
    days: int = typer.Option(90, min=1, max=3650, help="Decisions made in the last N days."),
) -> None:
    """Leakage-free replay: which knowledge existed at each past decision, and how it did."""

    async def work(_: Settings, database: Database) -> dict[str, Any]:
        report = await MemoryReplayEvaluator(
            repository=SqlMemoryRepository(database),
            audit=AuditRepository(database),
            clock=SystemClock(),
        ).replay(window=timedelta(days=days))
        return report.summary()

    result = _run(work)
    _print(result)
    raise typer.Exit(1 if result["leaks"] else 0)


@app.command()
def backup(
    destination: str | None = typer.Option(
        None, "--destination", help="JSON file; defaults to backups/memory-<UTC>.json."
    ),
) -> None:
    """Portable, SHA-256 stamped JSON export of every memory table."""

    target = Path(destination) if destination else Path("backups") / (
        f"memory-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    )

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        return await _operations(settings, database).backup(target)

    _print(_run(work))


async def _distiller(settings: Settings, database: Database) -> TradeReviewDistiller:
    return TradeReviewDistiller(
        audit=AuditRepository(database),
        repository=SqlMemoryRepository(database),
        gateway=await build_memory_gateway(settings, database),
        clock=SystemClock(),
    )


@app.command()
def restore(
    source: str = typer.Argument(..., help="A file written by `memory backup`."),
    sha256: str | None = typer.Option(None, "--sha256", help="Expected SHA-256 of the file."),
) -> None:
    """Restore a backup into an EMPTY memory store (all-or-nothing, fails closed)."""

    async def work(settings: Settings, database: Database) -> dict[str, int]:
        return await _operations(settings, database).restore(
            Path(source), expected_sha256=sha256
        )

    try:
        _print(_run(work))
    except (MemoryOperatorError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command(name="export-vault")
def export_vault() -> None:
    """Regenerate every Knowledge Vault note from the database."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        written = await _operations(settings, database).export_vault()
        return {"notes_written": written, "vault": str(settings.public.memory.vault_path)}

    _print(_run(work))


@app.command()
def reindex(
    force: bool = typer.Option(False, "--force", help="Re-embed every indexed memory."),
) -> None:
    """Embed new or changed knowledge into pgvector (no-op without PostgreSQL + model)."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        vector, embedder = await semantic_stack(settings, database)
        report = await MemoryIndexer(
            repository=SqlMemoryRepository(database), vector=vector, embedder=embedder
        ).run(force=force)
        return asdict(report)

    _print(_run(work))


@app.command(name="rebuild-working")
def rebuild_working() -> None:
    """Rebuild disposable working memory (Redis or in-process) from the database."""

    async def work(settings: Settings, database: Database) -> dict[str, Any]:
        config = settings.public.memory
        secret = settings.secrets.redis_url
        backend = build_working_memory(
            environment=settings.public.app.environment,
            backend=config.working_backend,
            clock=SystemClock(),
            redis_url=secret.get_secret_value() if secret is not None else None,
            prefix=config.working_key_prefix,
        )
        try:
            return await WorkingMemoryRebuilder(
                backend=backend,
                state=TradingStateRepository(database),
                lifecycle=OperationLifecycleRepository(database),
                clock=SystemClock(),
            ).rebuild()
        finally:
            close = getattr(backend, "aclose", None)
            if close is not None:
                await close()

    try:
        _print(_run(work))
    except WorkingMemoryUnavailable as exc:
        console.print(f"[red]working memory unavailable: {exc}[/red]")
        raise typer.Exit(1) from exc
