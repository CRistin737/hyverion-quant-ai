"""Free official sources: safe fetching, macro calendar and gate, Nasdaq-100, news, options."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from conftest import make_context, make_critic, make_proposal

from trading_bot.core.clock import FixedClock
from trading_bot.db.database import Database
from trading_bot.macro.calendar import (
    MacroEvent,
    parse_bea_schedule,
    parse_bls_ics,
    parse_fed_calendar,
)
from trading_bot.macro.gate import (
    MACRO_CALENDAR_UNAVAILABLE,
    MACRO_COOLDOWN,
    MACRO_PRE_BLOCK,
    evaluate_macro_gate,
)
from trading_bot.macro.rates import parse_fred_observations
from trading_bot.macro.service import MacroCalendarService
from trading_bot.market_data.base import ComponentQuote, NewsArticle, OptionQuote
from trading_bot.news.filings import parse_finnhub_earnings, parse_submissions
from trading_bot.news.pipeline import cluster_news
from trading_bot.options.volatility import volatility_context
from trading_bot.risk.engine import RiskEngine
from trading_bot.sources.fetch import FetchError, SafeFetcher
from trading_bot.sources.registry import data_source_key, load_source_registry
from trading_bot.universe.breadth import compute_breadth
from trading_bot.universe.nasdaq100 import (
    Holding,
    HoldingsReport,
    Nasdaq100UniverseService,
    estimated_weights,
    parse_nport,
    ticker_index,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sources"
NOW = datetime(2026, 9, 28, 14, 0, tzinfo=UTC)  # Monday 10:00 New York


def _fetcher(handler, **kwargs) -> SafeFetcher:
    return SafeFetcher(
        allowed_hosts=frozenset({"www.example.gov", "api.example.gov"}),
        contact_email="owner@example.com",
        min_interval_seconds=0,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


# ------------------------------------------------------------------ fetching


@pytest.mark.asyncio
async def test_fetcher_is_https_allowlisted_and_honours_robots() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, content=b"ok")

    fetcher = _fetcher(handler)
    assert await fetcher.get("https://www.example.gov/public/data.json") == b"ok"
    # The SEC asks for an identifying User-Agent with a contact email.
    assert "owner@example.com" in seen[-1].headers["user-agent"]
    with pytest.raises(FetchError) as blocked:
        await fetcher.get("https://www.example.gov/private/page")
    assert blocked.value.code == "robots_disallowed"
    with pytest.raises(FetchError) as scheme:
        await fetcher.get("http://www.example.gov/public/data.json")
    assert scheme.value.code == "source_scheme_forbidden"
    with pytest.raises(FetchError) as host:
        await fetcher.get("https://evil.example.com/x")
    assert host.value.code == "source_host_not_allowlisted"


@pytest.mark.asyncio
async def test_fetcher_fails_closed_when_robots_is_unreachable_and_uses_etags() -> None:
    calls = {"data": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.example.gov":
            return httpx.Response(503)
        calls["data"] += 1
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"body", headers={"etag": '"v1"'})

    fetcher = _fetcher(handler)
    with pytest.raises(FetchError) as exc:
        await fetcher.get("https://www.example.gov/page")
    assert exc.value.code == "robots_disallowed"
    first = await fetcher.get("https://api.example.gov/v1", check_robots=False)
    second = await fetcher.get("https://api.example.gov/v1", check_robots=False)
    assert first == second == b"body" and calls["data"] == 2


def test_registry_lists_only_free_sources_with_known_keys(monkeypatch) -> None:
    registry = load_source_registry()
    ids = {spec.id for spec in registry.sources}
    assert {"fed-calendar", "bls-schedule", "bea-schedule", "sec-edgar", "fred"} <= ids
    assert registry.get("fed-calendar").trust == 1.0
    assert registry.get("finnhub-earnings").trust < registry.get("sec-edgar").trust
    monkeypatch.setenv("FRED_API_KEY", "abc")
    assert data_source_key("data:fred:api_key") == "abc"
    monkeypatch.delenv("FRED_API_KEY")
    assert data_source_key("data:fred:api_key") is None  # the test keychain is empty


# --------------------------------------------------------------- macro calendar


def test_official_calendars_parse_with_new_york_time_and_importance() -> None:
    bls = parse_bls_ics((FIXTURES / "bls_sample.ics").read_text())
    nfp = next(
        e for e in bls if e.event_type == "NFP" and e.scheduled_at.date() == date(2026, 11, 6)
    )
    # 08:30 New York after the DST change is 13:30 UTC (never a fixed offset).
    assert nfp.scheduled_at == datetime(2026, 11, 6, 13, 30, tzinfo=UTC)
    assert nfp.importance == "HIGH"
    assert any(e.event_type == "CPI" for e in bls)
    fed = parse_fed_calendar((FIXTURES / "fed_calendar_sample.json").read_bytes())
    decision = next(e for e in fed if e.event_type == "FOMC_DECISION")
    presser = next(e for e in fed if e.event_type == "FOMC_PRESS_CONFERENCE")
    assert presser.scheduled_at - decision.scheduled_at == timedelta(minutes=30)
    assert any(e.event_type == "FED_SPEECH" and e.speaker for e in fed)
    assert all(e.consensus is None for e in fed)  # never invented (§21)
    bea_page = (FIXTURES / "bea_schedule_sample.html").read_text()
    bea = parse_bea_schedule(bea_page, today=date(2026, 9, 27))
    pce = next(e for e in bea if e.event_type == "PCE")
    assert pce.scheduled_at == datetime(2026, 9, 30, 12, 30, tzinfo=UTC)


def _event(minutes: int, importance: str = "HIGH") -> MacroEvent:
    when = NOW + timedelta(minutes=minutes)
    return MacroEvent(
        event_id=f"e{minutes}", event_type="CPI", title="CPI", scheduled_at=when,
        importance=importance, source="bls",  # type: ignore[arg-type]
    )


def test_macro_gate_blocks_before_and_after_high_impact_events() -> None:
    kwargs = {
        "now": NOW, "pre_block_minutes": 15, "post_cooldown_minutes": 15,
        "calendar_refreshed_at": NOW - timedelta(hours=1), "calendar_max_age": timedelta(days=7),
    }
    assert evaluate_macro_gate([_event(10)], **kwargs).reason == MACRO_PRE_BLOCK
    assert evaluate_macro_gate([_event(-5)], **kwargs).reason == MACRO_COOLDOWN
    assert evaluate_macro_gate([_event(40)], **kwargs).reason is None
    assert evaluate_macro_gate([_event(5, "MEDIUM")], **kwargs).reason is None
    stale = {**kwargs, "calendar_refreshed_at": NOW - timedelta(days=8)}
    assert evaluate_macro_gate([], **stale).reason == MACRO_CALENDAR_UNAVAILABLE


def test_risk_engine_denies_entries_on_a_macro_block(risk_config) -> None:
    engine = RiskEngine(risk_config, clock=FixedClock(NOW))
    decision = engine.evaluate_entry(
        make_proposal(NOW), make_critic(NOW), make_context(macro_block_reason=MACRO_PRE_BLOCK)
    )
    assert decision.verdict == "DENY" and MACRO_PRE_BLOCK in decision.reasons


@pytest.mark.asyncio
async def test_macro_calendar_is_point_in_time(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'macro.db'}")
    await database.initialize()
    try:
        offline = _fetcher(lambda r: httpx.Response(404))
        service = MacroCalendarService(database, offline, FixedClock(NOW))
        event = _event(60 * 24)
        assert await service.upsert([event], seen_at=NOW) == 1
        assert await service.upsert([event], seen_at=NOW + timedelta(hours=2)) == 0
        window = {"start": NOW, "end": NOW + timedelta(days=2)}
        assert len(await service.events(**window, as_of=NOW)) == 1
        # A replay at a time before Hyverion first saw the event must not see it (§70).
        assert await service.events(**window, as_of=NOW - timedelta(minutes=1)) == []
    finally:
        await database.close()


def test_fred_observations_skip_missing_days() -> None:
    raw = json.dumps({"observations": [
        {"date": "2026-09-22", "value": "4.10"}, {"date": "2026-09-23", "value": "."},
        {"date": "2026-09-24", "value": "4.05"}, {"date": "2026-09-25", "value": "4.12"},
    ]}).encode()
    point = parse_fred_observations("DGS10", raw)
    assert point is not None and point.value == Decimal("4.12")
    assert point.change_1d == Decimal("0.07")


# --------------------------------------------------------------- Nasdaq-100

NPORT = """<edgarSubmission>
<invstOrSec><name>Apple Inc.</name><title>Apple Inc.</title><cusip>037833100</cusip>
<identifiers><isin value="US0378331005"/></identifiers><balance>1000</balance>
<pctVal>9.0</pctVal><assetCat>EC</assetCat></invstOrSec>
<invstOrSec><name>Alphabet Inc.</name><title>Alphabet Inc., Class C</title>
<cusip>02079K107</cusip><balance>500</balance><pctVal>3.0</pctVal><assetCat>EC</assetCat></invstOrSec>
<invstOrSec><name>Cash</name><cusip>000000000</cusip><balance>1</balance>
<pctVal>0.1</pctVal><assetCat>STIV</assetCat></invstOrSec>
<invstOrSec><name>Unknown Widgets Inc.</name><cusip>999999999</cusip><balance>10</balance>
<pctVal>0.2</pctVal><assetCat>EC</assetCat></invstOrSec>
</edgarSubmission>"""
TICKERS = json.dumps({
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet Inc."},
    "2": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."},
}).encode()


def test_nport_holdings_resolve_tickers_and_never_guess() -> None:
    holdings = parse_nport(NPORT, ticker_index(TICKERS))
    by_cusip = {item.cusip: item for item in holdings}
    assert by_cusip["037833100"].symbol == "AAPL"
    assert by_cusip["037833100"].weight == Decimal("0.09")
    assert by_cusip["02079K107"].symbol == "GOOG"  # share class from the CUSIP
    assert by_cusip["999999999"].symbol is None  # reported, not guessed
    assert "000000000" not in by_cusip  # cash is not an index member
    weights = estimated_weights(holdings, {"AAPL": Decimal("200"), "GOOG": Decimal("100")})
    assert weights["AAPL"] == Decimal("0.8")


@pytest.mark.asyncio
async def test_index_membership_only_counts_after_the_filing_date(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'universe.db'}")
    await database.initialize()
    try:
        offline = _fetcher(lambda r: httpx.Response(404))
        service = Nasdaq100UniverseService(database, offline, FixedClock(NOW))
        report = HoldingsReport(
            report_date=date(2026, 6, 30), filed_at=date(2026, 8, 28), accession="x",
            holdings=(Holding(symbol="AAPL", name="Apple", cusip="1", shares=Decimal("1"),
                              weight=Decimal("0.1")),),
        )
        await service.store(report)
        assert (await service.members(date(2026, 8, 27)))[1] == []  # not yet public
        report_date, members = await service.members(date(2026, 9, 1))
        assert report_date == date(2026, 6, 30) and members[0].symbol == "AAPL"
    finally:
        await database.close()


def _quote(symbol: str, change: str, vwap_above: bool = True) -> ComponentQuote:
    last = Decimal("100") * (1 + Decimal(change))
    return ComponentQuote(
        symbol=symbol, last=last, previous_close=Decimal("100"),
        session_vwap=last - (1 if vwap_above else -1), as_of=NOW,
    )


def test_breadth_flags_a_megacap_only_rally() -> None:
    weights = {f"M{i}": Decimal("0.08") for i in range(10)}
    weights.update({f"S{i}": Decimal("0.002") for i in range(20)})
    quotes = [_quote(f"M{i}", "0.01") for i in range(10)]
    quotes += [_quote(f"S{i}", "-0.01", vwap_above=False) for i in range(20)]
    snapshot = compute_breadth(quotes, weights, as_of=NOW)
    assert snapshot is not None
    assert snapshot.estimated_index_change > 0
    assert snapshot.pct_green < Decimal("0.4")
    assert snapshot.weighted_breadth > Decimal("0.9")
    assert "megacap_only_rally" in snapshot.divergences
    assert snapshot.estimated is True


# --------------------------------------------------------------- news & filings


def _article(i: int, headline: str, source: str = "benzinga") -> NewsArticle:
    return NewsArticle(
        article_id=f"a{i}", headline=headline, source=source, symbols=("NVDA",),
        created_at=NOW - timedelta(minutes=30 - i),
    )


def test_duplicate_stories_are_one_cluster_weighted_by_index_weight() -> None:
    articles = [
        _article(1, "Nvidia beats earnings estimates and raises guidance"),
        _article(2, "Nvidia beats earnings estimates, raises guidance", "reuters"),
        _article(3, "NVIDIA beats earnings estimates and raises its guidance"),
        _article(4, "Nvidia opens new office in Taipei"),
    ]
    clusters = cluster_news(articles, {"NVDA": Decimal("0.09")})
    assert len(clusters) == 2
    earnings = next(c for c in clusters if c.copies == 3)
    assert earnings.materiality == "HIGH" and earnings.sentiment == 1
    assert earnings.qqq_relevance == Decimal("0.09")
    assert earnings.sources == ("benzinga", "reuters")
    assert earnings.decay(earnings.first_published_at + timedelta(hours=6)) == Decimal("0.5")


def test_sec_8k_item_202_is_an_earnings_release() -> None:
    raw = json.dumps({"filings": {"recent": {
        "form": ["8-K", "4", "10-Q"],
        "acceptanceDateTime": ["2026-09-28T16:05:00.000Z", "2026-09-28T16:00:00.000Z",
                               "2026-09-01T20:00:00.000Z"],
        "accessionNumber": ["0001-26-1", "0001-26-2", "0001-26-3"],
        "items": ["2.02,9.01", "", ""],
        "reportDate": ["2026-09-28", "", "2026-06-30"],
        "primaryDocument": ["a.htm", "b.xml", "c.htm"],
    }}}).encode()
    filings = parse_submissions("AAPL", 320193, raw, since=NOW - timedelta(days=3))
    assert [f.form for f in filings] == ["8-K"]
    assert filings[0].is_earnings_release is True
    earnings = parse_finnhub_earnings(json.dumps({"earningsCalendar": [
        {"symbol": "AAPL", "date": "2026-10-29", "hour": "amc", "epsEstimate": 1.8},
        {"symbol": "ZZZZ", "date": "2026-10-29", "hour": "bmo"},
    ]}).encode(), {"AAPL"})
    assert [(e.symbol, e.session) for e in earnings] == [("AAPL", "after_close")]


# --------------------------------------------------------------- volatility


def _option(
    kind: str, strike: str, iv: str, delta: str, expiration: str = "2026-10-02"
) -> OptionQuote:
    return OptionQuote(
        contract=f"QQQ{kind}{strike}", underlying="QQQ", expiration=expiration, option_type=kind,
        strike=Decimal(strike), bid=Decimal("4"), ask=Decimal("4.2"),
        implied_volatility=Decimal(iv), delta=Decimal(delta),
    )


def test_volatility_keeps_implied_and_realized_apart_and_never_invents() -> None:
    chain = [
        _option("call", "480", "0.20", "0.52"), _option("put", "480", "0.22", "-0.48"),
        _option("call", "495", "0.17", "0.25"), _option("put", "465", "0.26", "-0.25"),
        _option("call", "480", "0.21", "0.51", "2026-10-09"),
        _option("put", "480", "0.23", "-0.49", "2026-10-09"),
    ]
    context = volatility_context(chain, spot=Decimal("480"), today=date(2026, 9, 28))
    assert context.status == "ok"
    assert context.atm_iv == Decimal("0.21")
    assert context.skew_25d == Decimal("0.09")
    assert context.term_slope == Decimal("0.01")
    assert context.expected_move_pct == Decimal("8.2") / Decimal("480")
    empty = volatility_context([], spot=Decimal("480"), today=date(2026, 9, 28))
    assert empty.status == "unavailable" and empty.atm_iv is None


# --------------------------------------------------------------- analogs & events


def _session(day: date, drift: float, bars: int = 60) -> list:
    from trading_bot.schemas.trading import Candle

    start = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    out = []
    price = 480.0
    for minute in range(bars):
        price *= 1 + drift + (0.0004 if minute % 2 else -0.0004)
        end = start + timedelta(minutes=minute + 1)
        value = Decimal(str(round(price, 4)))
        out.append(Candle(symbol="QQQ", interval="1m", open=value, high=value + Decimal("0.2"),
                          low=value - Decimal("0.2"), close=value, volume=Decimal("1000"),
                          vwap=value, trades=5, event_time=end, received_time=end,
                          processed_time=end))
    return out


def test_analogs_only_use_past_sessions_and_report_sample_quality() -> None:
    from trading_bot.intelligence.analogs import find_analogs

    history = {date(2026, 8, 3) + timedelta(days=i): _session(date(2026, 8, 3) + timedelta(days=i),
                                                             0.0003 if i % 3 else -0.0003)
               for i in range(30)}
    today = date(2026, 9, 10)
    # A future session in the store must never be used.
    history[date(2026, 9, 20)] = _session(date(2026, 9, 20), 0.01)
    report = find_analogs(history, _session(today, 0.0003)[:20], today_day=today, minute=20)
    assert report.sample == 25 and report.quality == "ok"
    assert "2026-09-20" not in report.days
    assert report.up_rate is not None and report.median_mfe is not None
    few = find_analogs({d: history[d] for d in list(history)[:3]}, _session(today, 0)[:20],
                       today_day=today, minute=20)
    assert few.quality == "insufficient" and few.up_rate is None


def test_event_memory_measures_reactions_inside_the_session() -> None:
    from trading_bot.intelligence.analogs import event_reactions

    bars = _session(date(2026, 9, 16), 0.0005, bars=200)
    fomc = MacroEvent(event_id="f", event_type="FOMC_DECISION", title="FOMC",
                      scheduled_at=datetime(2026, 9, 16, 14, 0, tzinfo=UTC), importance="HIGH",
                      source="fed")
    premarket = MacroEvent(event_id="c", event_type="CPI", title="CPI",
                           scheduled_at=datetime(2026, 9, 16, 12, 30, tzinfo=UTC),
                           importance="HIGH", source="bls")
    reactions = event_reactions([fomc, premarket], bars, as_of=datetime(2026, 9, 17, tzinfo=UTC))
    assert [r.event_type for r in reactions] == ["FOMC_DECISION"]  # 08:30 CPI is pre-market
    assert reactions[0].samples == 1 and reactions[0].small_sample is True
