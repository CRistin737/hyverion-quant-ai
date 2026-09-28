"""IntelligenceHub: refresh every sensor on its own cadence, build one context.

One place owns *when* each source is read (calendars daily, news every few
minutes, breadth every cycle in session) and *what* the agents and the risk
gate see. Each source fails on its own: a dead news feed never hides the macro
calendar, and missing data is reported as missing (``unavailable`` /
``key_missing``), never filled in.

Everything here is read-only market intelligence. It holds no broker and can
submit nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from trading_bot.config.models import Settings
from trading_bot.core.clock import Clock
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.intelligence.analogs import (
    AnalogReport,
    event_reactions,
    find_analogs,
    group_sessions,
)
from trading_bot.macro.rates import RatesContext, fetch_rates_context
from trading_bot.macro.service import MacroCalendarService
from trading_bot.market.clock import regular_session_only, trading_day
from trading_bot.market_data.alpaca import AlpacaMarketData
from trading_bot.market_data.base import MarketDataUnavailable
from trading_bot.news.filings import cik_index, fetch_earnings, fetch_filings
from trading_bot.news.pipeline import cluster_news
from trading_bot.options.volatility import VolatilityContext, volatility_context
from trading_bot.schemas.trading import Candle
from trading_bot.sources.fetch import FetchError, SafeFetcher
from trading_bot.sources.registry import data_source_key
from trading_bot.sources.runtime import record_source_run
from trading_bot.universe.breadth import BreadthSnapshot, compute_breadth
from trading_bot.universe.nasdaq100 import (
    TICKERS_URL,
    Nasdaq100UniverseService,
    estimated_weights,
)

# How often each source is read. Official calendars change rarely; prices do.
CADENCE = {
    "macro": timedelta(hours=6),
    "universe": timedelta(hours=24),
    "rates": timedelta(hours=1),
    "earnings": timedelta(hours=12),
    "filings": timedelta(minutes=30),
    "news": timedelta(minutes=5),
    "options": timedelta(minutes=15),
    "breadth": timedelta(minutes=1),
    "history": timedelta(hours=24),
}
HISTORY_SESSIONS_DAYS = 100  # calendar days of 1-minute QQQ bars for analogs
FILINGS_TOP_N = 20
# Registry source id → hub steps that exercise it ("Probar" in Ajustes).
SOURCE_TEST_STEPS: dict[str, tuple[str, ...]] = {
    "alpaca-market-data": ("universe", "breadth"),
    "alpaca-news": ("universe", "news"),
    "alpaca-options": ("options",),
    "sec-edgar": ("universe", "filings"),
    "fed-calendar": ("macro",),
    "bls-schedule": ("macro",),
    "bea-schedule": ("macro",),
    "fred": ("rates",),
    "finnhub-earnings": ("universe", "earnings"),
}
# Hub step → registry source id, for the status shown in Ajustes > Fuentes.
# (macro and universe record their own per-source runs.)
RUN_SOURCE = {
    "rates": "fred",
    "earnings": "finnhub-earnings",
    "filings": "sec-edgar",
    "news": "alpaca-news",
    "breadth": "alpaca-market-data",
    "options": "alpaca-options",
}  # the largest weights explain most of QQQ's move


@dataclass
class IntelligenceState:
    """Last known value of every sensor, with the time it was read."""

    breadth: BreadthSnapshot | None = None
    rates: RatesContext | None = None
    volatility: VolatilityContext | None = None
    weights: dict[str, Decimal] = field(default_factory=dict)
    holdings_report: str | None = None
    news: list[dict[str, Any]] = field(default_factory=list)
    filings: list[dict[str, Any]] = field(default_factory=list)
    earnings: list[dict[str, Any]] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    refreshed: dict[str, datetime] = field(default_factory=dict)
    history: dict[date, list[Candle]] = field(default_factory=dict)
    analogs: AnalogReport | None = None
    event_memory: list[dict[str, Any]] = field(default_factory=list)


class IntelligenceHub:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        clock: Clock,
        fetcher: SafeFetcher,
        market_data: AlpacaMarketData | None,
    ) -> None:
        self._settings = settings
        self._database = database
        self._clock = clock
        self._fetcher = fetcher
        self._market = market_data
        self._audit = AuditRepository(database)
        self.macro = MacroCalendarService(database, fetcher, clock)
        self.universe = Nasdaq100UniverseService(database, fetcher, clock)
        self.state = IntelligenceState()
        self._ciks: dict[str, int] = {}

    def _due(self, name: str, now: datetime) -> bool:
        last = self.state.refreshed.get(name)
        return last is None or now - last >= CADENCE[name]

    async def refresh(self, *, candles: tuple[Candle, ...] = (), force: bool = False) -> None:
        now = self._clock.now()
        steps = (
            ("macro", self._refresh_macro),
            ("universe", self._refresh_universe),
            ("rates", self._refresh_rates),
            ("earnings", self._refresh_earnings),
            ("filings", self._refresh_filings),
            ("news", self._refresh_news),
            ("breadth", self._refresh_breadth),
        )
        for name, step in steps:
            if force or self._due(name, now):
                await self._run(name, step)
        if force or self._due("options", now):
            await self._run("options", lambda: self._refresh_options(candles))
        if force or self._due("history", now):
            await self._run("history", self._refresh_history)
        self._update_analogs(candles)

    async def refresh_only(self, names: tuple[str, ...]) -> None:
        steps = {
            "macro": self._refresh_macro,
            "universe": self._refresh_universe,
            "rates": self._refresh_rates,
            "earnings": self._refresh_earnings,
            "filings": self._refresh_filings,
            "news": self._refresh_news,
            "breadth": self._refresh_breadth,
            "options": lambda: self._refresh_options(()),
        }
        for name in names:
            await self._run(name, steps[name])

    async def _run(self, name: str, step: Any) -> None:
        started = self._clock.now()
        error: str | None = None
        try:
            await step()
            self.state.errors.pop(name, None)
        except (FetchError, MarketDataUnavailable) as exc:
            error = exc.code
        except (ValueError, KeyError) as exc:
            error = f"parse_error:{type(exc).__name__}"
        if error is not None:
            self.state.errors[name] = error
        source_id = RUN_SOURCE.get(name)
        if source_id is not None:
            await record_source_run(
                self._audit, source_id=source_id, ok=error is None, records=0,
                started_at=started, finished_at=self._clock.now(), error=error,
            )
        # Even a failure waits for the next cadence: never hammer a source.
        self.state.refreshed[name] = self._clock.now()

    async def _refresh_macro(self) -> None:
        summary = await self.macro.refresh()
        failed = [source for source, result in summary.items() if not result.get("ok")]
        if failed:
            self.state.errors["macro"] = ",".join(failed)

    async def _refresh_universe(self) -> None:
        result = await self.universe.refresh()
        if not result.get("ok"):
            raise FetchError(str(result.get("error") or "universe_unavailable"))
        report_date, holdings = await self.universe.members()
        self.state.holdings_report = report_date.isoformat() if report_date else None
        # Report weights until today's prices refine them.
        self.state.weights = {h.symbol: h.weight for h in holdings if h.symbol}

    async def _refresh_rates(self) -> None:
        key = data_source_key("data:fred:api_key", self._settings.secrets)
        rates = await fetch_rates_context(self._fetcher, key, today=trading_day(self._clock.now()))
        self.state.rates = rates
        if rates.status != "ok":
            self.state.errors["rates"] = rates.error or rates.status
        await self._audit.append(
            "rates_snapshots", rates, created_at=self._clock.now(), asset="QQQ"
        )

    async def _members(self) -> list[str]:
        _, holdings = await self.universe.members()
        return [h.symbol for h in holdings if h.symbol]

    async def _refresh_earnings(self) -> None:
        key = data_source_key("data:finnhub:api_key", self._settings.secrets)
        members = set(await self._members())
        today = trading_day(self._clock.now())
        events = await fetch_earnings(
            self._fetcher, key, members, start=today, end=today + timedelta(days=21)
        )
        weights = self.state.weights
        self.state.earnings = [
            {**event.model_dump(mode="json"), "weight": str(weights.get(event.symbol, ""))}
            for event in events
        ]
        for event in events:
            await self._audit.append(
                "earnings_events", event, created_at=self._clock.now(), asset=event.symbol
            )

    async def _refresh_filings(self) -> None:
        if not self._fetcher.contact_email:
            raise FetchError("key_missing", "sec contact email")
        if not self._ciks:
            self._ciks = cik_index(await self._fetcher.get(TICKERS_URL))
        top = sorted(self.state.weights, key=lambda s: self.state.weights[s], reverse=True)
        since = self._clock.now() - timedelta(days=3)
        filings, errors = await fetch_filings(
            self._fetcher, top[:FILINGS_TOP_N], self._ciks, since=since
        )
        known = {row["accession"] for row in self.state.filings}
        for filing in filings:
            if filing.accession not in known:
                await self._audit.append(
                    "sec_filings",
                    filing,
                    created_at=self._clock.now(),
                    asset=filing.symbol,
                    event_time=filing.filed_at,
                    received_time=self._clock.now(),
                )
        self.state.filings = [filing.model_dump(mode="json") for filing in filings[:40]]
        if errors:
            self.state.errors["filings"] = f"{len(errors)} sin datos"

    async def _refresh_news(self) -> None:
        if self._market is None:
            raise FetchError("key_missing", "alpaca")
        top = sorted(self.state.weights, key=lambda s: self.state.weights[s], reverse=True)
        symbols = ["QQQ", *top[:15]]
        articles = await self._market.fetch_news(
            symbols, start=self._clock.now() - timedelta(hours=24)
        )
        clusters = cluster_news(articles, self.state.weights)
        known = {row["cluster_id"] for row in self.state.news}
        for cluster in clusters:
            if cluster.cluster_id not in known:
                await self._audit.append(
                    "news_clusters",
                    cluster,
                    created_at=self._clock.now(),
                    asset="QQQ",
                    event_time=cluster.first_published_at,
                    received_time=self._clock.now(),
                )
        now = self._clock.now()
        self.state.news = [
            {**cluster.model_dump(mode="json"), "decay": str(cluster.decay(now))}
            for cluster in clusters[:25]
        ]

    async def _refresh_breadth(self) -> None:
        if self._market is None:
            raise FetchError("key_missing", "alpaca")
        if not self.state.weights:
            raise FetchError("universe_unavailable")
        quotes = await self._market.fetch_component_quotes([*self.state.weights, "QQQ"])
        qqq = next((quote for quote in quotes if quote.symbol == "QQQ"), None)
        _, holdings = await self.universe.members()
        prices = {quote.symbol: quote.last for quote in quotes}
        weights = estimated_weights(holdings, prices) or self.state.weights
        breadth = compute_breadth(
            [quote for quote in quotes if quote.symbol != "QQQ"],
            weights,
            as_of=self._clock.now(),
            qqq_change=qqq.change if qqq else None,
        )
        if breadth is None:
            raise FetchError("breadth_unavailable")
        self.state.breadth = breadth
        await self._audit.append(
            "breadth_snapshots", breadth, created_at=self._clock.now(), asset="QQQ"
        )

    async def _refresh_history(self) -> None:
        """Past QQQ sessions for analogs and event memory (read once a day)."""

        if self._market is None:
            raise FetchError("key_missing", "alpaca")
        now = self._clock.now()
        bars = await self._market.fetch_candle_range(
            "QQQ", start=now - timedelta(days=HISTORY_SESSIONS_DAYS), end=now, interval="1m"
        )
        regular = regular_session_only(bars)
        self.state.history = group_sessions(regular)
        events = await self.macro.events(
            start=now - timedelta(days=HISTORY_SESSIONS_DAYS), end=now, as_of=now
        )
        self.state.event_memory = [
            reaction.model_dump(mode="json")
            for reaction in event_reactions(events, regular, as_of=now)
        ]

    def _update_analogs(self, candles: tuple[Candle, ...]) -> None:
        if not self.state.history or not candles:
            return
        today = trading_day(self._clock.now())
        session = [c for c in regular_session_only(candles) if trading_day(c.event_time) == today]
        if len(session) < 15:
            return
        self.state.analogs = find_analogs(
            self.state.history, session, today_day=today, minute=len(session)
        )

    async def _refresh_options(self, candles: tuple[Candle, ...]) -> None:
        vix = self.state.rates.value("VIXCLS") if self.state.rates else None
        chain = []
        spot = candles[-1].close if candles else None
        if self._market is not None:
            today = trading_day(self._clock.now())
            chain = await self._market.fetch_option_chain(
                "QQQ",
                expiration_gte=(today + timedelta(days=1)).isoformat(),
                expiration_lte=(today + timedelta(days=21)).isoformat(),
            )
            if spot is not None:
                # Only strikes near the money matter for ATM IV, skew and move.
                chain = [q for q in chain if abs(q.strike / spot - 1) <= Decimal("0.08")]
        context = volatility_context(
            chain, spot=spot, today=trading_day(self._clock.now()), candles=candles, vix_close=vix
        )
        self.state.volatility = context
        await self._audit.append(
            "options_snapshots", context, created_at=self._clock.now(), asset="QQQ"
        )

    async def context(self) -> dict[str, Any]:
        """The read-only intelligence envelope agents, UI and replay consume."""

        now = self._clock.now()
        gate = await self.macro.gate(self._settings.public.risk, now=now)
        upcoming = await self.macro.events(
            start=now - timedelta(hours=1), end=now + timedelta(days=14)
        )
        refreshed = await self.macro.refreshed_at()
        return {
            "as_of": now.isoformat(),
            "breadth": self.state.breadth.model_dump(mode="json") if self.state.breadth else None,
            "macro": {
                "gate": gate.reason,
                "gate_event": gate.event.model_dump(mode="json") if gate.event else None,
                "calendar_refreshed_at": refreshed.isoformat() if refreshed else None,
                "upcoming": [event.as_public(now) for event in upcoming[:40]],
            },
            "rates": self.state.rates.model_dump(mode="json") if self.state.rates else None,
            "volatility": (
                self.state.volatility.model_dump(mode="json") if self.state.volatility else None
            ),
            "news": self.state.news,
            "filings": self.state.filings,
            "earnings": self.state.earnings,
            "analogs": self.state.analogs.model_dump(mode="json") if self.state.analogs else None,
            "event_memory": self.state.event_memory,
            "universe": {
                "report_date": self.state.holdings_report,
                "components": len(self.state.weights),
            },
            "errors": dict(self.state.errors),
        }


def intelligence_event(intelligence: dict[str, Any] | None) -> bool:
    """Is there something the agents should look at now? (§36 event activation)."""

    if not intelligence:
        return False
    breadth = intelligence.get("breadth") or {}
    fresh_news = [
        item
        for item in intelligence.get("news") or []
        if item.get("materiality") == "HIGH" and float(item.get("decay") or 0) > 0.5
    ]
    macro = intelligence.get("macro") or {}
    return bool(breadth.get("divergences") or fresh_news or macro.get("gate_event"))
