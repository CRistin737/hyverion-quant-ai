"""Read-only view of stored intelligence for the control API and the UI.

The engine refreshes the sensors; the API only reads what was stored (latest
breadth, rates, volatility, news clusters, filings, earnings and the macro
calendar), so opening the app never hits an external source by itself.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.macro.service import MacroCalendarService
from trading_bot.sources.fetch import SafeFetcher
from trading_bot.sources.registry import load_source_registry, source_key_present

# Source ids used in source_runs that are not registry entries.
RUN_ALIASES = {"sec-edgar": ("sec-nport",)}


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


async def _latest(audit: AuditRepository, table: str, limit: int = 1) -> list[dict[str, Any]]:
    return [
        {**_payload(row), "_stored_at": str(row.get("created_at"))}
        for row in await audit.recent(table, limit=limit)
    ]


async def stored_intelligence(
    settings: Settings, database: Database, clock: Clock
) -> dict[str, Any]:
    audit = AuditRepository(database)
    now = clock.now()
    macro = MacroCalendarService(database, SafeFetcher(allowed_hosts=frozenset()), clock)
    gate = await macro.gate(settings.public.risk, now=now)
    upcoming = await macro.events(start=now - timedelta(hours=2), end=now + timedelta(days=21))
    refreshed = await macro.refreshed_at()
    breadth = await _latest(audit, "breadth_snapshots")
    rates = await _latest(audit, "rates_snapshots")
    volatility = await _latest(audit, "options_snapshots")
    news = await _latest(audit, "news_clusters", 30)
    filings = await _latest(audit, "sec_filings", 30)
    earnings = await _latest(audit, "earnings_events", 60)
    today = now.date().isoformat()
    upcoming_earnings: dict[str, dict[str, Any]] = {}
    for row in earnings:
        if str(row.get("date") or "") >= today:
            upcoming_earnings.setdefault(f"{row.get('symbol')}:{row.get('date')}", row)
    return {
        "as_of": now.isoformat(),
        "macro": {
            "gate": gate.reason,
            "gate_event": gate.event.as_public(now) if gate.event else None,
            "calendar_refreshed_at": refreshed.isoformat() if refreshed else None,
            "upcoming": [event.as_public(now) for event in upcoming[:60]],
        },
        "breadth": breadth[0] if breadth else None,
        "rates": rates[0] if rates else None,
        "volatility": volatility[0] if volatility else None,
        "news": news,
        "filings": filings,
        "earnings": sorted(upcoming_earnings.values(), key=lambda row: str(row.get("date"))),
    }


async def sources_status(settings: Settings, database: Database) -> list[dict[str, Any]]:
    audit = AuditRepository(database)
    rows: list[dict[str, Any]] = []
    for spec in load_source_registry().sources:
        runs: list[dict[str, Any]] = []
        for run_id in (spec.id, *RUN_ALIASES.get(spec.id, ())):
            runs.extend(_payload(row) for row in await audit.recent("source_runs", 5, asset=run_id))
        runs.sort(key=lambda run: str(run.get("finished_at") or ""), reverse=True)
        last = runs[0] if runs else None
        key_present = source_key_present(spec, settings.secrets)
        if not key_present:
            state = "key_missing"
        elif last is None:
            state = "never_run"
        else:
            state = "ok" if last.get("status") == "SUCCEEDED" else "error"
        rows.append(
            {
                **spec.model_dump(mode="json"),
                "trust": spec.trust,
                "key_present": key_present,
                "state": state,
                "last_run_at": last.get("finished_at") if last else None,
                "last_error": last.get("error") if last else None,
                "last_records": last.get("records_count") if last else None,
            }
        )
    return rows

