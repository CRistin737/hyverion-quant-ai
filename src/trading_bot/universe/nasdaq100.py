"""Nasdaq-100 universe from QQQ's official holdings (SEC Form N-PORT), point-in-time.

Invesco files QQQ's full portfolio with the SEC every quarter (``NPORT-P``). It
is free, official and dated, so membership can be asked "as of" any day: a
report only counts from the day it was *filed* (no look-ahead, §16, §69).

Limitations, stated rather than hidden:

* holdings are quarterly and published with a lag; index changes between two
  reports are not seen until the next one;
* the history starts with the first report Hyverion stored (survivorship);
* current weights are **estimates**: reported shares * today's price.

Tickers are resolved from the SEC's own ``company_tickers.json``. A holding
that cannot be resolved keeps ``symbol=None`` and is reported, never guessed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import and_, delete, insert, select

from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.models import index_holdings
from trading_bot.db.repositories import AuditRepository
from trading_bot.schemas.common import StrictSchema
from trading_bot.sources.fetch import FetchError, SafeFetcher
from trading_bot.sources.runtime import record_source_run

INDEX_NAME = "NASDAQ100"
QQQ_TRUST_CIK = "0001067839"
SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{QQQ_TRUST_CIK}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SOURCE_ID = "sec-edgar"
# Share classes the name alone cannot tell apart (CUSIP → ticker).
CUSIP_TICKERS = {"02079K305": "GOOGL", "02079K107": "GOOG", "35137L105": "FOXA", "35137L204": "FOX"}

_SUFFIX = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|plc|ltd|limited|holdings?|group|nv|sa|"
    r"ag|se|the|new|de|class [a-c]|common stock|ordinary shares|ads|adr)\b"
)


class Holding(StrictSchema):
    symbol: str | None
    name: str
    cusip: str
    isin: str | None = None
    shares: Decimal
    weight: Decimal  # fraction of net assets at the report date (0-1)


class HoldingsReport(StrictSchema):
    report_date: date
    filed_at: date
    accession: str
    holdings: tuple[Holding, ...]

    @property
    def unmapped(self) -> tuple[Holding, ...]:
        return tuple(item for item in self.holdings if item.symbol is None)


def normalize_company(name: str) -> str:
    text = name.split("/")[0].lower().replace("&", "and")
    text = re.sub(r"[.,'()\-]", " ", text)
    text = re.sub(r"\bn v\b|\bs a\b", " ", text)
    text = _SUFFIX.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def ticker_index(raw: bytes) -> dict[str, str]:
    """Normalized company name → primary ticker (first listed by the SEC)."""

    data: dict[str, Any] = json.loads(raw)
    index: dict[str, str] = {}
    for row in data.values():
        key = normalize_company(str(row.get("title") or ""))
        if key and key not in index:
            index[key] = str(row["ticker"]).upper()
    return index


def _tag(block: str, name: str) -> str | None:
    match = re.search(rf"<{name}>([^<]*)</{name}>", block)
    return match.group(1).strip() if match else None


def parse_nport(xml: str, tickers: dict[str, str]) -> list[Holding]:
    holdings: list[Holding] = []
    for block in re.findall(r"(?s)<invstOrSec>(.*?)</invstOrSec>", xml):
        if _tag(block, "assetCat") != "EC":
            continue  # equities only; cash and collateral are not index members
        name = _tag(block, "name") or ""
        cusip = _tag(block, "cusip") or ""
        isin = re.search(r'<isin value="([^"]+)"', block)
        try:
            shares = Decimal(_tag(block, "balance") or "")
            weight = Decimal(_tag(block, "pctVal") or "") / 100
        except InvalidOperation:
            continue
        symbol = CUSIP_TICKERS.get(cusip) or tickers.get(normalize_company(name))
        holdings.append(
            Holding(
                symbol=symbol,
                name=name,
                cusip=cusip or (isin.group(1) if isin else name)[:16],
                isin=isin.group(1) if isin else None,
                shares=shares,
                weight=weight,
            )
        )
    return holdings


def latest_nport(submissions: bytes) -> tuple[str, date, date] | None:
    """(accession, report date, filing date) of the newest NPORT-P."""

    recent = json.loads(submissions)["filings"]["recent"]
    for index, form in enumerate(recent["form"]):
        if form == "NPORT-P":
            return (
                recent["accessionNumber"][index],
                date.fromisoformat(recent["reportDate"][index]),
                date.fromisoformat(recent["filingDate"][index]),
            )
    return None


def estimated_weights(
    holdings: Sequence[Holding], prices: dict[str, Decimal]
) -> dict[str, Decimal]:
    """Today's weights ≈ reported shares * current price (an estimate, §17)."""

    values = {
        item.symbol: item.shares * prices[item.symbol]
        for item in holdings
        if item.symbol and item.symbol in prices
    }
    total = sum(values.values(), Decimal("0"))
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in values.items()}


class Nasdaq100UniverseService:
    def __init__(self, database: Database, fetcher: SafeFetcher, clock: Clock) -> None:
        self._database = database
        self._fetcher = fetcher
        self._clock = clock
        self._audit = AuditRepository(database)

    async def refresh(self) -> dict[str, Any]:
        started = self._clock.now()
        if not self._fetcher.contact_email:
            # The SEC asks every automated client to identify itself with an email.
            return {"ok": False, "error": "key_missing"}
        try:
            latest = latest_nport(await self._fetcher.get(SUBMISSIONS_URL, check_robots=False))
            if latest is None:
                raise ValueError("no_nport_filing")
            accession, report_date, filed_at = latest
            if await self._has_report(report_date):
                return {"ok": True, "report_date": report_date.isoformat(), "new": False}
            tickers = ticker_index(await self._fetcher.get(TICKERS_URL))
            folder = accession.replace("-", "")
            cik = str(int(QQQ_TRUST_CIK))
            xml = await self._fetcher.get(
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/primary_doc.xml"
            )
            holdings = parse_nport(xml.decode("utf-8", "replace"), tickers)
            if len(holdings) < 90:
                raise ValueError("nport_incomplete")
        except (FetchError, ValueError, KeyError) as exc:
            code = getattr(exc, "code", None) or str(exc) or "source_parse_failed"
            await record_source_run(
                self._audit, source_id="sec-nport", ok=False, records=0,
                started_at=started, finished_at=self._clock.now(), error=code,
            )
            return {"ok": False, "error": code}
        report = HoldingsReport(
            report_date=report_date, filed_at=filed_at, accession=accession,
            holdings=tuple(holdings),
        )
        await self.store(report)
        await record_source_run(
            self._audit, source_id="sec-nport", ok=True, records=len(holdings),
            started_at=started, finished_at=self._clock.now(),
            detail={"report_date": report_date.isoformat(), "unmapped": len(report.unmapped)},
        )
        return {
            "ok": True,
            "report_date": report_date.isoformat(),
            "new": True,
            "holdings": len(holdings),
            "unmapped": [item.name for item in report.unmapped],
        }

    async def _has_report(self, report_date: date) -> bool:
        async with self._database.engine.connect() as connection:
            row = (
                await connection.execute(
                    select(index_holdings.c.cusip)
                    .where(
                        and_(
                            index_holdings.c.index_name == INDEX_NAME,
                            index_holdings.c.report_date == report_date,
                        )
                    )
                    .limit(1)
                )
            ).first()
        return row is not None

    async def store(self, report: HoldingsReport) -> None:
        now = self._clock.now().astimezone(UTC)
        async with self._database.engine.begin() as connection:
            await connection.execute(
                delete(index_holdings).where(
                    and_(
                        index_holdings.c.index_name == INDEX_NAME,
                        index_holdings.c.report_date == report.report_date,
                    )
                )
            )
            seen: set[str] = set()
            for item in report.holdings:
                if item.cusip in seen:
                    continue
                seen.add(item.cusip)
                await connection.execute(
                    insert(index_holdings).values(
                        index_name=INDEX_NAME,
                        report_date=report.report_date,
                        cusip=item.cusip,
                        symbol=item.symbol,
                        name=item.name[:255],
                        isin=item.isin,
                        shares=item.shares,
                        weight=item.weight,
                        source=f"SEC N-PORT {report.accession}",
                        filed_at=report.filed_at,
                        first_seen_at=now,
                    )
                )

    async def members(self, as_of: date | None = None) -> tuple[date | None, list[Holding]]:
        """Holdings of the newest report *filed* on or before ``as_of``."""

        day = as_of or self._clock.now().date()
        async with self._database.engine.connect() as connection:
            latest = (
                await connection.execute(
                    select(index_holdings.c.report_date)
                    .where(
                        and_(
                            index_holdings.c.index_name == INDEX_NAME,
                            index_holdings.c.filed_at <= day,
                        )
                    )
                    .order_by(index_holdings.c.report_date.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest is None:
                return None, []
            rows = (
                await connection.execute(
                    select(index_holdings).where(
                        and_(
                            index_holdings.c.index_name == INDEX_NAME,
                            index_holdings.c.report_date == latest,
                        )
                    )
                )
            ).mappings().all()
        holdings = [
            Holding(
                symbol=row["symbol"],
                name=row["name"],
                cusip=row["cusip"],
                isin=row["isin"],
                shares=Decimal(str(row["shares"])),
                weight=Decimal(str(row["weight"])),
            )
            for row in rows
        ]
        holdings.sort(key=lambda item: item.weight, reverse=True)
        return latest, holdings
