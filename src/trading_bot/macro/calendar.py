"""Economic calendar from official, free sources (§21, §70).

* **Federal Reserve** ``/json/calendar.json`` (the data behind its public
  calendar): FOMC decisions, press conferences, minutes, speeches, testimony,
  Beige Book.
* **BLS** ``bls.ics``: CPI, PPI, Employment Situation (NFP), JOLTS, ECI.
* **BEA** release schedule page (robots.txt allows it): GDP, Personal Income
  and Outlays (PCE).

Parsers are pure and strict: anything that does not parse is skipped, never
guessed. ``consensus``/``actual`` stay ``None`` — no free official source
publishes a consensus and §21 forbids inventing one. Times are converted from
New York to UTC with ``zoneinfo`` (never a fixed offset).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from trading_bot.market.clock import NEW_YORK
from trading_bot.schemas.common import StrictSchema

Importance = Literal["HIGH", "MEDIUM", "LOW"]

FED_CALENDAR_URL = "https://www.federalreserve.gov/json/calendar.json"
BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_SCHEDULE_URL = "https://www.bea.gov/news/schedule"


class MacroEvent(StrictSchema):
    event_id: str
    event_type: str
    title: str
    scheduled_at: datetime
    time_known: bool = True
    importance: Importance
    source: str
    speaker: str | None = None
    url: str | None = None
    # Never invented: None until a licensed provider supplies them (§21).
    consensus: Decimal | None = None
    previous: Decimal | None = None
    actual: Decimal | None = None
    surprise: Decimal | None = None
    first_seen_at: datetime | None = None

    def as_public(self, now: datetime) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["minutes_to_event"] = int((self.scheduled_at - now).total_seconds() // 60)
        return payload


def _event_id(source: str, event_type: str, when: datetime, title: str) -> str:
    digest = hashlib.sha256(f"{source}|{event_type}|{when.isoformat()}|{title}".encode())
    return f"{source}:{event_type}:{when:%Y%m%dT%H%M}:{digest.hexdigest()[:8]}"


def _new_york(day: date, clock_time: time) -> datetime:
    return datetime.combine(day, clock_time, tzinfo=NEW_YORK).astimezone(UTC)


def _event(
    source: str,
    event_type: str,
    title: str,
    when: datetime,
    importance: Importance,
    *,
    time_known: bool = True,
    speaker: str | None = None,
    url: str | None = None,
) -> MacroEvent:
    return MacroEvent(
        event_id=_event_id(source, event_type, when, title),
        event_type=event_type,
        title=title,
        scheduled_at=when,
        time_known=time_known,
        importance=importance,
        source=source,
        speaker=speaker,
        url=url,
    )


# ------------------------------------------------------------------- BLS ICS

_BLS_TYPES: tuple[tuple[str, str, Importance], ...] = (
    ("consumer price index", "CPI", "HIGH"),
    ("employment situation", "NFP", "HIGH"),
    ("producer price index", "PPI", "MEDIUM"),
    ("job openings and labor turnover", "JOLTS", "MEDIUM"),
    ("employment cost index", "ECI", "MEDIUM"),
    ("real earnings", "REAL_EARNINGS", "LOW"),
    ("productivity and costs", "PRODUCTIVITY", "LOW"),
    ("export and import price", "IMPORT_PRICES", "LOW"),
)


def _classify(title: str, table: Iterable[tuple[str, str, Importance]]) -> tuple[str, Importance]:
    lowered = title.lower()
    for needle, event_type, importance in table:
        if needle in lowered:
            return event_type, importance
    return "OTHER", "LOW"


def parse_bls_ics(text: str) -> list[MacroEvent]:
    events: list[MacroEvent] = []
    # Unfold folded ICS lines (RFC 5545 §3.1).
    unfolded = re.sub(r"\r?\n[ \t]", "", text)
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, flags=re.S):
        start = re.search(r"DTSTART;TZID=[^:]+:(\d{8})T(\d{4})", block)
        summary = re.search(r"SUMMARY:(.+)", block)
        if not start or not summary:
            continue
        title = summary.group(1).strip().replace("\\,", ",")
        day = datetime.strptime(start.group(1), "%Y%m%d").date()
        clock_time = datetime.strptime(start.group(2), "%H%M").time()
        event_type, importance = _classify(title, _BLS_TYPES)
        events.append(_event("bls", event_type, title, _new_york(day, clock_time), importance))
    return events


# --------------------------------------------------------------- Fed calendar

_FED_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.I)


def _fed_time(value: str) -> time | None:
    match = _FED_TIME.search(value or "")
    if not match:
        return None
    hour = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "p" else 0)
    return time(hour, int(match.group(2)))


def _clean(value: object) -> str:
    text = html.unescape(html.unescape(str(value or "")))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def parse_fed_calendar(raw: bytes | str, *, since: date | None = None) -> list[MacroEvent]:
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("﻿")
    data = json.loads(text)
    rows = data.get("events", []) if isinstance(data, dict) else []
    events: list[MacroEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("type") or "")
        title = _clean(row.get("title"))
        month = str(row.get("month") or "")
        days = re.findall(r"\d{1,2}", str(row.get("days") or ""))
        if not re.fullmatch(r"\d{4}-\d{2}", month) or not days:
            continue
        # Multi-day meetings list their days; the decision is on the last one.
        day_number = int(days[-1] if kind == "FOMC" else days[0])
        try:
            day = date(int(month[:4]), int(month[5:]), day_number)
        except ValueError:
            continue
        if since is not None and day < since:
            continue
        clock_time = _fed_time(str(row.get("time") or ""))
        time_known = clock_time is not None
        when = _new_york(day, clock_time or time(0, 0))
        description = _clean(row.get("description"))
        lowered = title.lower()
        speaker = None
        if kind == "FOMC":
            if "meeting" in lowered:
                events.append(
                    _event("fed", "FOMC_DECISION", "Decisión de tipos del FOMC", when, "HIGH",
                           time_known=time_known)
                )
                if "press conference" in description.lower():
                    events.append(
                        _event("fed", "FOMC_PRESS_CONFERENCE", "Rueda de prensa del FOMC",
                               when + timedelta(minutes=30), "HIGH", time_known=time_known)
                    )
                continue
            if "minutes" in lowered:
                events.append(_event("fed", "FOMC_MINUTES", "Actas del FOMC", when, "MEDIUM",
                                     time_known=time_known))
                continue
            if "press conference" in lowered:
                continue  # already emitted with its meeting
        if kind in {"Speeches", "Testimony"}:
            speaker = re.sub(r"^(speech|testimony|discussion|remarks)\s*-+\s*", "", title,
                             flags=re.I).strip()
            chair = re.search(r"\bchair\b", speaker, re.I) and not re.search(
                r"vice chair", speaker, re.I
            )
            event_type = "FED_TESTIMONY" if kind == "Testimony" else "FED_SPEECH"
            label = f"{'Comparecencia' if kind == 'Testimony' else 'Discurso'}: {speaker}"
            if description:
                label = f"{label} — {description}"
            events.append(
                _event("fed", event_type, label[:240], when, "HIGH" if chair else "MEDIUM",
                       time_known=time_known, speaker=speaker)
            )
            continue
        if kind == "Beige":
            events.append(_event("fed", "BEIGE_BOOK", "Beige Book", when, "LOW",
                                 time_known=time_known))
    return events


# --------------------------------------------------------------- BEA schedule

_BEA_TYPES: tuple[tuple[str, str, Importance], ...] = (
    ("gdp (advance", "GDP_ADVANCE", "HIGH"),
    ("personal income and outlays", "PCE", "HIGH"),
    ("gdp (", "GDP", "MEDIUM"),
    ("international trade in goods and services", "TRADE_BALANCE", "LOW"),
)


def parse_bea_schedule(page: str, *, today: date) -> list[MacroEvent]:
    events: list[MacroEvent] = []
    for row in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", page):
        date_match = re.search(r'class="release-date">\s*([A-Za-z]+)\s+(\d{1,2})\s*<', row)
        time_match = re.search(r"(\d{1,2}):(\d{2})\s*([AP])M", row)
        title_match = re.search(r'(?s)class="release-title[^"]*"[^>]*>(.*?)</td>', row)
        if not (date_match and title_match):
            continue
        # Only "News" releases (not data/visual/article updates).
        if not re.search(r'<span class="caps">N</span><span class="tail">ews', row):
            continue
        try:
            month = datetime.strptime(date_match.group(1), "%B").month
        except ValueError:
            continue
        day_number = int(date_match.group(2))
        # The page shows no year: take the one closest to today.
        candidates = []
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidates.append(date(year, month, day_number))
            except ValueError:
                continue
        if not candidates:
            continue
        day = min(candidates, key=lambda candidate: abs((candidate - today).days))
        clock_time = (
            time(int(time_match.group(1)) % 12 + (12 if time_match.group(3) == "P" else 0),
                 int(time_match.group(2)))
            if time_match
            else None
        )
        title = _clean(title_match.group(1))
        event_type, importance = _classify(title, _BEA_TYPES)
        events.append(
            _event("bea", event_type, title[:240], _new_york(day, clock_time or time(0, 0)),
                   importance, time_known=clock_time is not None)
        )
    return events


def merge_events(*groups: Iterable[MacroEvent]) -> list[MacroEvent]:
    seen: dict[str, MacroEvent] = {}
    for group in groups:
        for event in group:
            seen.setdefault(event.event_id, event)
    return sorted(seen.values(), key=lambda event: event.scheduled_at)
