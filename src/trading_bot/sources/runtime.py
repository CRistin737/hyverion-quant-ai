"""Shared plumbing for data-source services: the fetcher and run bookkeeping."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from trading_bot.config.models import SecretSettings
from trading_bot.db.repositories import AuditRepository
from trading_bot.sources.fetch import SafeFetcher
from trading_bot.sources.registry import data_source_key, load_source_registry


def build_fetcher(
    secrets: SecretSettings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    min_interval_seconds: float = 1.0,
) -> SafeFetcher:
    """A fetcher limited to the registry's hosts, identified with the owner's email."""

    registry = load_source_registry()
    return SafeFetcher(
        allowed_hosts=registry.hosts,
        contact_email=data_source_key("data:contact_email", secrets),
        min_interval_seconds=min_interval_seconds,
        transport=transport,
    )


async def record_source_run(
    audit: AuditRepository,
    *,
    source_id: str,
    ok: bool,
    records: int,
    started_at: datetime,
    finished_at: datetime,
    error: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await audit.append(
        "source_runs",
        {
            "source_id": source_id,
            "status": "SUCCEEDED" if ok else "FAILED",
            "records_count": records,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "error": error,
            **(detail or {}),
        },
        created_at=finished_at,
        # The source id doubles as the row's key so status lookups stay bounded.
        asset=source_id,
        event_time=started_at,
        received_time=finished_at,
        processed_time=finished_at,
    )


async def last_success(audit: AuditRepository, source_id: str) -> datetime | None:
    for row in await audit.recent("source_runs", limit=200, asset=source_id):
        payload = row.get("payload") or {}
        if payload.get("status") == "SUCCEEDED":
            return datetime.fromisoformat(str(payload["finished_at"]))
    return None
