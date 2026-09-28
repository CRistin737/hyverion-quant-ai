"""The engine's daily routine, in New York time.

* ``premarket`` (08:30 ET, trading days): refresh every source and store the
  day's context, before the open.
* ``close_review`` (16:15 ET, trading days): daily report and memory cycle
  (distil lessons, score agents), automatically; no button needed.
* ``nightly_improvement`` (20:00 ET, weekdays): the AI improvement review.
* ``weekly_review`` (Friday 17:00 ET): the week's summary.

Each routine runs at most once per New York date; the record lives in
``system_events`` (``DAILY_ROUTINE``) so a restart never repeats one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class Routine:
    name: str
    at: time
    trading_days_only: bool = True
    weekday: int | None = None  # 0 = Monday … 4 = Friday


ROUTINES: tuple[Routine, ...] = (
    Routine("premarket", time(8, 30)),
    Routine("close_review", time(16, 15)),
    Routine("nightly_improvement", time(20, 0), trading_days_only=False),
    Routine("weekly_review", time(17, 0), trading_days_only=False, weekday=4),
)


def due_routines(
    now: datetime,
    *,
    is_trading_day: bool,
    done: set[tuple[str, str]],
) -> tuple[str, ...]:
    """Routines whose time has passed today (New York) and that have not run."""

    local = now.astimezone(NEW_YORK)
    today: date = local.date()
    due: list[str] = []
    for routine in ROUTINES:
        if routine.weekday is not None and local.weekday() != routine.weekday:
            continue
        if routine.weekday is None and local.weekday() >= 5:
            continue
        if routine.trading_days_only and not is_trading_day:
            continue
        if local.time() < routine.at:
            continue
        if (routine.name, today.isoformat()) in done:
            continue
        due.append(routine.name)
    return tuple(due)


def session_date(now: datetime) -> str:
    return now.astimezone(NEW_YORK).date().isoformat()


ROUTINE_EVENT = "DAILY_ROUTINE"


async def run_due_routines(
    audit: Any,
    now: datetime,
    *,
    is_trading_day: bool,
    actions: Mapping[str, Callable[[], Awaitable[dict[str, Any]]]],
) -> tuple[str, ...]:
    """Run each due routine once and record it. One failing routine never
    stops the others or the trading loop; its error is recorded instead."""

    local_midnight = datetime.combine(now.astimezone(NEW_YORK).date(), time(0), tzinfo=NEW_YORK)
    rows = await audit.with_status(
        "system_events", [ROUTINE_EVENT], since=local_midnight.astimezone(UTC), limit=50
    )
    done = {
        (str(payload.get("routine")), str(payload.get("session_date")))
        for payload in (row.get("payload") or {} for row in rows)
        if isinstance(payload, dict) and payload.get("status") == ROUTINE_EVENT
    }
    ran: list[str] = []
    for name in due_routines(now, is_trading_day=is_trading_day, done=done):
        action = actions.get(name)
        if action is None:
            continue
        try:
            result: dict[str, Any] = await action()
            outcome = "OK"
        except Exception as exc:  # a routine must never take the engine down
            result = {"error": type(exc).__name__}
            outcome = "FAILED"
        await audit.append(
            "system_events",
            {
                "status": ROUTINE_EVENT,
                "routine": name,
                "session_date": session_date(now),
                "outcome": outcome,
                "result": result,
            },
            created_at=now,
        )
        ran.append(name)
    return tuple(ran)
