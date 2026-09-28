"""MacroRiskGate (§22): deterministic, and no LLM can override it.

* a HIGH-impact event within ``pre_block_minutes`` → no new entries;
* a HIGH-impact event released less than ``post_cooldown_minutes`` ago →
  event-driven cooldown, no new entries;
* no calendar refreshed recently → ``macro_calendar_unavailable`` (fail closed:
  trading blind into a CPI print is exactly what this gate exists to stop).

Exits and protective stops are never blocked: the gate only stops *entries*.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from trading_bot.macro.calendar import MacroEvent

MACRO_PRE_BLOCK = "macro_event_pre_block"
MACRO_COOLDOWN = "macro_event_cooldown"
MACRO_CALENDAR_UNAVAILABLE = "macro_calendar_unavailable"


@dataclass(frozen=True, slots=True)
class MacroGateDecision:
    reason: str | None
    event: MacroEvent | None = None

    @property
    def blocked(self) -> bool:
        return self.reason is not None


def evaluate_macro_gate(
    events: Sequence[MacroEvent],
    *,
    now: datetime,
    pre_block_minutes: int,
    post_cooldown_minutes: int,
    calendar_refreshed_at: datetime | None,
    calendar_max_age: timedelta,
) -> MacroGateDecision:
    if calendar_refreshed_at is None or now - calendar_refreshed_at > calendar_max_age:
        return MacroGateDecision(MACRO_CALENDAR_UNAVAILABLE)
    pre = timedelta(minutes=pre_block_minutes)
    post = timedelta(minutes=post_cooldown_minutes)
    for event in sorted(events, key=lambda item: item.scheduled_at):
        if event.importance != "HIGH" or not event.time_known:
            continue
        delta = event.scheduled_at - now
        if timedelta(0) <= delta <= pre:
            return MacroGateDecision(MACRO_PRE_BLOCK, event)
        if -post <= delta < timedelta(0):
            return MacroGateDecision(MACRO_COOLDOWN, event)
    return MacroGateDecision(None)
