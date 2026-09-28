"""MacroCalendarService: refresh official calendars, keep them point-in-time.

Each event is upserted by ``event_id``; ``first_seen_at`` is written once, so a
replay can ask "what did the calendar look like at T?" and never see an event
before Hyverion knew about it (§70). One source failing never erases what the
others provided; the gate decides with what was last known and fails closed
when the calendar is too old.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, insert, select, update

from trading_bot.config.models import RiskConfig
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.models import macro_events
from trading_bot.db.repositories import AuditRepository
from trading_bot.macro.calendar import (
    BEA_SCHEDULE_URL,
    BLS_CALENDAR_URL,
    FED_CALENDAR_URL,
    MacroEvent,
    parse_bea_schedule,
    parse_bls_ics,
    parse_fed_calendar,
)
from trading_bot.macro.gate import MacroGateDecision, evaluate_macro_gate
from trading_bot.market.clock import trading_day
from trading_bot.sources.fetch import FetchError, SafeFetcher
from trading_bot.sources.runtime import last_success, record_source_run

# The gate needs FOMC (Fed) and CPI/NFP (BLS); BEA is useful but not required.
REQUIRED_SOURCES = ("fed-calendar", "bls-schedule")


def _utc(value: datetime) -> datetime:
    # SQLite returns naive datetimes; every stored timestamp is UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class MacroCalendarService:
    def __init__(self, database: Database, fetcher: SafeFetcher, clock: Clock) -> None:
        self._database = database
        self._fetcher = fetcher
        self._clock = clock
        self._audit = AuditRepository(database)

    async def refresh(self) -> dict[str, Any]:
        now = self._clock.now()
        # Keep a year of past events: replays apply the gate on published schedules.
        since = trading_day(now) - timedelta(days=400)
        summary: dict[str, Any] = {}
        for source_id, loader in (
            ("fed-calendar", self._fed),
            ("bls-schedule", self._bls),
            ("bea-schedule", self._bea),
        ):
            started = self._clock.now()
            try:
                events = [e for e in await loader() if e.scheduled_at.date() >= since]
            except (FetchError, ValueError) as exc:
                code = exc.code if isinstance(exc, FetchError) else "source_parse_failed"
                await record_source_run(
                    self._audit, source_id=source_id, ok=False, records=0,
                    started_at=started, finished_at=self._clock.now(), error=code,
                )
                summary[source_id] = {"ok": False, "error": code}
                continue
            stored = await self.upsert(events, seen_at=now)
            await record_source_run(
                self._audit, source_id=source_id, ok=True, records=len(events),
                started_at=started, finished_at=self._clock.now(),
            )
            summary[source_id] = {"ok": True, "events": len(events), "new": stored}
        return summary

    async def _fed(self) -> list[MacroEvent]:
        return parse_fed_calendar(await self._fetcher.get(FED_CALENDAR_URL, check_robots=True))

    async def _bls(self) -> list[MacroEvent]:
        raw = await self._fetcher.get(BLS_CALENDAR_URL)
        return parse_bls_ics(raw.decode("utf-8", "replace"))

    async def _bea(self) -> list[MacroEvent]:
        raw = await self._fetcher.get(BEA_SCHEDULE_URL)
        today = trading_day(self._clock.now())
        return parse_bea_schedule(raw.decode("utf-8", "replace"), today=today)

    async def upsert(self, events: Sequence[MacroEvent], *, seen_at: datetime) -> int:
        """Insert new events; refresh known ones without touching ``first_seen_at``."""

        new = 0
        async with self._database.engine.begin() as connection:
            existing = {
                row[0]
                for row in await connection.execute(
                    select(macro_events.c.event_id).where(
                        macro_events.c.event_id.in_([event.event_id for event in events])
                    )
                )
            }
            for event in events:
                values = {
                    "event_type": event.event_type,
                    "importance": event.importance,
                    "scheduled_at": event.scheduled_at,
                    "time_known": event.time_known,
                    "source": event.source,
                    "title": event.title[:255],
                    "payload": event.model_dump(mode="json"),
                    "updated_at": seen_at,
                }
                if event.event_id in existing:
                    await connection.execute(
                        update(macro_events)
                        .where(macro_events.c.event_id == event.event_id)
                        .values(**values)
                    )
                    continue
                await connection.execute(
                    insert(macro_events).values(
                        event_id=event.event_id, first_seen_at=seen_at, **values
                    )
                )
                new += 1
        return new

    async def events(
        self, *, start: datetime, end: datetime, as_of: datetime | None = None
    ) -> list[MacroEvent]:
        """Events scheduled in ``[start, end]`` that were known at ``as_of``."""

        known_at = as_of or self._clock.now()
        statement = (
            select(macro_events)
            .where(
                and_(
                    macro_events.c.scheduled_at >= start,
                    macro_events.c.scheduled_at <= end,
                    macro_events.c.first_seen_at <= known_at,
                )
            )
            .order_by(macro_events.c.scheduled_at)
        )
        async with self._database.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            MacroEvent.model_validate(
                {**row["payload"], "first_seen_at": _utc(row["first_seen_at"])}
            )
            for row in rows
        ]

    async def refreshed_at(self) -> datetime | None:
        """Oldest of the required sources' last successful refresh."""

        stamps = [await last_success(self._audit, source) for source in REQUIRED_SOURCES]
        if any(stamp is None for stamp in stamps):
            return None
        return min(stamp for stamp in stamps if stamp is not None)

    async def gate(self, config: RiskConfig, *, now: datetime | None = None) -> MacroGateDecision:
        moment = now or self._clock.now()
        window = timedelta(minutes=max(config.macro_pre_block_minutes,
                                       config.macro_post_cooldown_minutes) + 1)
        events = await self.events(start=moment - window, end=moment + window, as_of=moment)
        return evaluate_macro_gate(
            events,
            now=moment,
            pre_block_minutes=config.macro_pre_block_minutes,
            post_cooldown_minutes=config.macro_post_cooldown_minutes,
            calendar_refreshed_at=await self.refreshed_at(),
            calendar_max_age=timedelta(hours=config.macro_calendar_max_age_hours),
        )
