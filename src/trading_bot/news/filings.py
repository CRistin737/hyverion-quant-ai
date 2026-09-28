"""SEC filings (§29) and the earnings calendar (§30) for Nasdaq-100 components.

* **SEC EDGAR** ``submissions`` JSON per company: 8-K, 10-Q, 10-K, 6-K. An 8-K
  with item 2.02 is an earnings release. Metadata only; documents are not sent
  to an LLM unless a later step selects a relevant section (§29).
* **Finnhub** earnings calendar (free key): date and session (before/after
  the open). Earnings raise context risk; they never block automatically (§30).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_bot.schemas.common import StrictSchema
from trading_bot.sources.fetch import FetchError, SafeFetcher

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FINNHUB_EARNINGS_URL = "https://finnhub.io/api/v1/calendar/earnings"
MATERIAL_FORMS = frozenset({"8-K", "10-Q", "10-K", "6-K", "8-K/A", "10-K/A", "10-Q/A"})


class SecFiling(StrictSchema):
    symbol: str
    cik: int
    form: str
    accession: str
    filed_at: datetime  # SEC acceptance time (when it became public)
    report_date: date | None
    items: tuple[str, ...]
    is_earnings_release: bool
    url: str


class EarningsEvent(StrictSchema):
    symbol: str
    date: date
    session: str  # "before_open" | "after_close" | "during" | "unknown"
    eps_estimate: Decimal | None = None
    revenue_estimate: Decimal | None = None
    source: str = "finnhub"


def cik_index(raw: bytes) -> dict[str, int]:
    data: dict[str, Any] = json.loads(raw)
    return {str(row["ticker"]).upper(): int(row["cik_str"]) for row in data.values()}


def parse_submissions(symbol: str, cik: int, raw: bytes, *, since: datetime) -> list[SecFiling]:
    recent = json.loads(raw)["filings"]["recent"]
    filings: list[SecFiling] = []
    for index, form in enumerate(recent["form"]):
        if form not in MATERIAL_FORMS:
            continue
        try:
            accepted = datetime.fromisoformat(
                str(recent["acceptanceDateTime"][index]).replace("Z", "+00:00")
            ).astimezone(UTC)
        except (ValueError, IndexError):
            continue
        if accepted < since:
            continue
        accession = recent["accessionNumber"][index]
        items = tuple(
            item.strip() for item in str(recent.get("items", [""] * 9999)[index]).split(",")
            if item.strip()
        )
        report = recent.get("reportDate", [""] * 9999)[index]
        document = recent["primaryDocument"][index]
        filings.append(
            SecFiling(
                symbol=symbol,
                cik=cik,
                form=form,
                accession=accession,
                filed_at=accepted,
                report_date=date.fromisoformat(report) if report else None,
                items=items,
                is_earnings_release=form.startswith("8-K") and "2.02" in items,
                url=(
                    f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                    f"{accession.replace('-', '')}/{document}"
                ),
            )
        )
    return filings


async def fetch_filings(
    fetcher: SafeFetcher,
    symbols: Sequence[str],
    ciks: dict[str, int],
    *,
    since: datetime,
) -> tuple[list[SecFiling], list[str]]:
    filings: list[SecFiling] = []
    errors: list[str] = []
    for symbol in symbols:
        cik = ciks.get(symbol)
        if cik is None:
            errors.append(f"{symbol}:cik_unknown")
            continue
        try:
            raw = await fetcher.get(SUBMISSIONS_URL.format(cik=cik), check_robots=False)
            filings.extend(parse_submissions(symbol, cik, raw, since=since))
        except (FetchError, ValueError, KeyError) as exc:
            errors.append(f"{symbol}:{getattr(exc, 'code', type(exc).__name__)}")
    filings.sort(key=lambda item: item.filed_at, reverse=True)
    return filings, errors


_SESSION = {"bmo": "before_open", "amc": "after_close", "dmh": "during"}


def _decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def parse_finnhub_earnings(raw: bytes, members: set[str]) -> list[EarningsEvent]:
    rows = json.loads(raw).get("earningsCalendar") or []
    events: list[EarningsEvent] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if symbol not in members:
            continue
        try:
            day = date.fromisoformat(str(row["date"]))
        except (KeyError, ValueError):
            continue
        events.append(
            EarningsEvent(
                symbol=symbol,
                date=day,
                session=_SESSION.get(str(row.get("hour") or "").lower(), "unknown"),
                eps_estimate=_decimal(row.get("epsEstimate")),
                revenue_estimate=_decimal(row.get("revenueEstimate")),
            )
        )
    return sorted(events, key=lambda item: (item.date, item.symbol))


async def fetch_earnings(
    fetcher: SafeFetcher, api_key: str | None, members: set[str], *, start: date, end: date
) -> list[EarningsEvent]:
    if not api_key:
        raise FetchError("key_missing", "finnhub")
    raw = await fetcher.get(
        FINNHUB_EARNINGS_URL,
        params={"from": start.isoformat(), "to": end.isoformat(), "token": api_key},
        check_robots=False,
    )
    return parse_finnhub_earnings(raw, members)
