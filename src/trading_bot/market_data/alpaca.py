"""Alpaca Market Data v2 (stocks) adapter.

Verified against the official docs (docs.alpaca.markets, 2026-09-27):
``GET /v2/stocks/{symbol}/snapshot``, ``GET /v2/stocks/bars`` (``next_page_token``
pagination, ``limit`` ≤ 10 000, bar ``t`` is the bar *start*), headers
``APCA-API-KEY-ID``/``APCA-API-SECRET-KEY`` and the WebSocket
``wss://stream.data.alpaca.markets/v2/{feed}`` with an ``auth`` then
``subscribe`` message.

The free ``iex`` feed is real time but covers only the IEX venue (a small share
of consolidated volume), so every snapshot is labelled with its feed. Failures
raise :class:`MarketDataUnavailable` with a stable code; prices are never
guessed.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import websockets

from trading_bot.core.clock import Clock
from trading_bot.market.clock import trading_day
from trading_bot.market_data.base import (
    TIMEFRAMES,
    ComponentQuote,
    MarketDataUnavailable,
    NewsArticle,
    OptionQuote,
)
from trading_bot.schemas.trading import Candle, MarketSnapshot
from trading_bot.security.credentials import AlpacaPaperCredentials

DATA_URL = "https://data.alpaca.markets"
STREAM_URL = "wss://stream.data.alpaca.markets/v2"
_TIMEFRAME = {
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "4h": "4Hour",
    "1d": "1Day",
}
_STEP = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}
MAX_PAGE = 10_000
MAX_PAGES = 50


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _time(value: str) -> datetime:
    # RFC-3339 with up to nanoseconds; Python keeps microseconds.
    text = value.replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        digits = tail[: len(tail) - len(tail.lstrip("0123456789"))]
        zone = tail[len(digits) :]
        text = f"{head}.{digits[:6]}{zone}"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp without timezone")
    return parsed.astimezone(UTC)


def _parse_occ(contract: str) -> tuple[str, str, Decimal] | None:
    """OCC symbol ``QQQ261016C00480000`` → (``2026-10-16``, ``call``, ``480``)."""

    match = re.fullmatch(r"([A-Z.]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})", contract)
    if not match:
        return None
    _, yy, mm, dd, kind, strike = match.groups()
    return f"20{yy}-{mm}-{dd}", "call" if kind == "C" else "put", Decimal(strike) / 1000


class AlpacaMarketData:
    provider = "alpaca"

    def __init__(
        self,
        credentials: AlpacaPaperCredentials,
        clock: Clock,
        *,
        feed: str = "iex",
        base_url: str = DATA_URL,
        stream_url: str = STREAM_URL,
        timeout_seconds: float = 10.0,
    ) -> None:
        if feed not in {"iex", "sip"}:
            raise ValueError("unsupported Alpaca feed")
        self._credentials = credentials
        self._clock = clock
        self.feed: str | None = feed
        self._base_url = base_url.rstrip("/")
        self._stream_url = f"{stream_url.rstrip('/')}/{feed}"
        self._timeout = timeout_seconds

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._credentials.headers(),
                timeout=self._timeout,
            ) as client:
                response = await client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise MarketDataUnavailable("market_data_unavailable", type(exc).__name__) from exc
        if response.status_code in {401, 403}:
            raise MarketDataUnavailable("market_data_auth_failed", str(response.status_code))
        if response.status_code == 429:
            raise MarketDataUnavailable("market_data_rate_limited")
        if response.status_code >= 400:
            raise MarketDataUnavailable("market_data_unavailable", str(response.status_code))
        try:
            body = response.json()
        except ValueError as exc:
            raise MarketDataUnavailable("market_data_invalid_response") from exc
        if not isinstance(body, dict):
            raise MarketDataUnavailable("market_data_invalid_response")
        return body

    async def fetch_component_quotes(self, symbols: Sequence[str]) -> list[ComponentQuote]:
        """Batch snapshots (``/v2/stocks/snapshots``) for breadth and contribution."""

        quotes: list[ComponentQuote] = []
        now = self._clock.now().astimezone(UTC)
        unique = sorted({symbol.upper() for symbol in symbols if symbol})
        for start in range(0, len(unique), 100):
            batch = unique[start : start + 100]
            body = await self._get(
                "/v2/stocks/snapshots", {"symbols": ",".join(batch), "feed": self.feed}
            )
            for symbol in batch:
                row = body.get(symbol)
                if not isinstance(row, dict):
                    continue
                try:
                    daily = row.get("dailyBar") or {}
                    previous = row.get("prevDailyBar") or {}
                    trade = row.get("latestTrade") or {}
                    last = _dec(trade.get("p") or daily.get("c"))
                    previous_close = _dec(previous["c"])
                    today = bool(daily) and trading_day(_time(daily["t"])) == trading_day(now)
                    quotes.append(
                        ComponentQuote(
                            symbol=symbol,
                            last=last,
                            previous_close=previous_close,
                            session_open=_dec(daily["o"]) if today else None,
                            session_vwap=_dec(daily["vw"]) if today and daily.get("vw") else None,
                            session_volume=_dec(daily.get("v", 0)) if today else Decimal("0"),
                            as_of=min(_time(trade["t"]), now) if trade.get("t") else now,
                        )
                    )
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue  # a symbol without a clean quote is skipped, not guessed
        return quotes

    async def fetch_news(
        self, symbols: Sequence[str], *, start: datetime, limit: int = 50
    ) -> list[NewsArticle]:
        """Alpaca News (``/v1beta1/news``), newest first. Headlines and summaries only."""

        body = await self._get(
            "/v1beta1/news",
            {
                "symbols": ",".join(sorted(set(symbols))),
                "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "limit": max(1, min(limit, 50)),
                "sort": "desc",
                "include_content": "false",
            },
        )
        articles: list[NewsArticle] = []
        for row in body.get("news") or []:
            try:
                articles.append(
                    NewsArticle(
                        article_id=f"alpaca:{row['id']}",
                        headline=str(row["headline"])[:500],
                        summary=str(row.get("summary") or "")[:2000],
                        source=str(row.get("source") or "alpaca"),
                        url=row.get("url") or None,
                        symbols=tuple(str(item) for item in row.get("symbols") or ()),
                        created_at=_time(row["created_at"]),
                        updated_at=_time(row["updated_at"]) if row.get("updated_at") else None,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return articles

    async def fetch_option_chain(
        self, underlying: str, *, expiration_gte: str, expiration_lte: str
    ) -> list[OptionQuote]:
        """Option snapshots (``/v1beta1/options/snapshots``), free ``indicative`` feed."""

        params: dict[str, Any] = {
            "feed": "indicative",
            "expiration_date_gte": expiration_gte,
            "expiration_date_lte": expiration_lte,
            "limit": 1000,
        }
        quotes: list[OptionQuote] = []
        for _ in range(10):
            body = await self._get(f"/v1beta1/options/snapshots/{underlying}", params)
            for contract, row in (body.get("snapshots") or {}).items():
                parsed = _parse_occ(str(contract))
                if parsed is None or not isinstance(row, dict):
                    continue
                expiration, option_type, strike = parsed
                quote = row.get("latestQuote") or {}
                greeks = row.get("greeks") or {}
                try:
                    quotes.append(
                        OptionQuote(
                            contract=str(contract),
                            underlying=underlying,
                            expiration=expiration,
                            option_type=option_type,
                            strike=strike,
                            bid=_dec(quote["bp"]) if quote.get("bp") is not None else None,
                            ask=_dec(quote["ap"]) if quote.get("ap") is not None else None,
                            implied_volatility=(
                                _dec(row["impliedVolatility"])
                                if row.get("impliedVolatility") is not None
                                else None
                            ),
                            delta=(
                                _dec(greeks["delta"]) if greeks.get("delta") is not None else None
                            ),
                            as_of=_time(quote["t"]) if quote.get("t") else None,
                        )
                    )
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue
            token = body.get("next_page_token")
            if not token:
                break
            params = {**params, "page_token": token}
        return quotes

    async def fetch_snapshot(self, symbol: str) -> MarketSnapshot:
        body = await self._get(f"/v2/stocks/{symbol}/snapshot", {"feed": self.feed})
        return self.snapshot_from_payload(symbol, body)

    def snapshot_from_payload(self, symbol: str, body: dict[str, Any]) -> MarketSnapshot:
        now = self._clock.now().astimezone(UTC)
        try:
            quote = body["latestQuote"]
            trade = body["latestTrade"]
            bid, ask, last = _dec(quote["bp"]), _dec(quote["ap"]), _dec(trade["p"])
            event_time = max(_time(quote["t"]), _time(trade["t"]))
            daily = body.get("dailyBar") or {}
            volume = Decimal("0")
            dollar_volume = Decimal("0")
            # dailyBar is the latest session; it only counts if it is today's.
            if daily and trading_day(_time(daily["t"])) == trading_day(now):
                volume = _dec(daily.get("v", 0))
                dollar_volume = volume * _dec(daily.get("vw") or daily.get("c") or 0)
            return MarketSnapshot(
                symbol=symbol,
                bid=bid,
                ask=ask,
                last=last,
                session_volume=volume,
                session_dollar_volume=dollar_volume,
                event_time=min(event_time, now),
                received_time=now,
                processed_time=now,
                provider=self.provider,
                feed=self.feed,
                is_delayed=False,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            # A zero bid (no IEX quote) or a malformed payload is not a price.
            raise MarketDataUnavailable("market_data_invalid_quote", type(exc).__name__) from exc

    def _candles(self, symbol: str, rows: list[dict[str, Any]], interval: str) -> list[Candle]:
        now = self._clock.now().astimezone(UTC)
        candles: list[Candle] = []
        for row in rows:
            try:
                close_time = _time(row["t"]) + _STEP[interval]
                candles.append(
                    Candle(
                        symbol=symbol,
                        interval=interval,
                        open=_dec(row["o"]),
                        high=_dec(row["h"]),
                        low=_dec(row["l"]),
                        close=_dec(row["c"]),
                        volume=_dec(row.get("v", 0)),
                        vwap=_dec(row["vw"]) if row.get("vw") else None,
                        trades=int(row.get("n", 0)),
                        # Candle.event_time is the bar close (a bar is known once it ends).
                        event_time=close_time,
                        received_time=max(close_time, now),
                        processed_time=max(close_time, now),
                    )
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
        return candles

    async def fetch_candles(
        self, symbol: str, *, interval: str = "1m", limit: int = 20
    ) -> tuple[Candle, ...]:
        if interval not in TIMEFRAMES:
            raise MarketDataUnavailable("unsupported_interval", interval)
        if not 1 <= limit <= MAX_PAGE:
            raise MarketDataUnavailable("limit_out_of_range")
        now = self._clock.now().astimezone(UTC)
        # Look back far enough to cover nights, weekends and holidays.
        lookback = max(timedelta(days=10), _STEP[interval] * limit * 4)
        body = await self._get(
            "/v2/stocks/bars",
            {
                "symbols": symbol,
                "timeframe": _TIMEFRAME[interval],
                "start": (now - lookback).isoformat().replace("+00:00", "Z"),
                "end": now.isoformat().replace("+00:00", "Z"),
                "limit": limit,
                "feed": self.feed,
                "adjustment": "split",
                "sort": "desc",
            },
        )
        rows = list(((body.get("bars") or {}).get(symbol)) or [])
        candles = self._candles(symbol, rows, interval)
        # Only closed bars: the still-forming bar is not a fact yet.
        closed = [candle for candle in candles if candle.event_time <= now]
        return tuple(sorted(closed, key=lambda candle: candle.event_time))[-limit:]

    async def fetch_candle_range(
        self, symbol: str, *, start: datetime, end: datetime, interval: str = "1m"
    ) -> tuple[Candle, ...]:
        if interval not in TIMEFRAMES:
            raise MarketDataUnavailable("unsupported_interval", interval)
        params: dict[str, Any] = {
            "symbols": symbol,
            "timeframe": _TIMEFRAME[interval],
            "start": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "limit": MAX_PAGE,
            "feed": self.feed,
            "adjustment": "split",
            "sort": "asc",
        }
        rows: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            body = await self._get("/v2/stocks/bars", params)
            rows.extend(((body.get("bars") or {}).get(symbol)) or [])
            token = body.get("next_page_token")
            if not token:
                break
            params = {**params, "page_token": token}
        else:
            raise MarketDataUnavailable("market_data_history_too_large")
        unique = {candle.event_time: candle for candle in self._candles(symbol, rows, interval)}
        return tuple(unique[key] for key in sorted(unique))

    async def stream_snapshots(
        self, symbol: str, *, max_reconnect_delay_seconds: int, stop_event: asyncio.Event
    ) -> AsyncIterator[MarketSnapshot]:
        """Quotes and trades from the WebSocket, folded into snapshots (≤ 1 per second)."""

        delay = 1
        while not stop_event.is_set():
            try:
                seed = await self.fetch_snapshot(symbol)
                async with websockets.connect(self._stream_url) as socket:
                    await socket.send(
                        json.dumps(
                            {
                                "action": "auth",
                                "key": self._credentials.key_id,
                                "secret": self._credentials.secret_key,
                            }
                        )
                    )
                    await socket.send(
                        json.dumps({"action": "subscribe", "quotes": [symbol], "trades": [symbol]})
                    )
                    delay = 1
                    state = {"bid": seed.bid, "ask": seed.ask, "last": seed.last}
                    last_emit: datetime | None = None
                    async for raw in socket:
                        if stop_event.is_set():
                            return
                        for message in json.loads(raw):
                            if message.get("T") == "error":
                                raise MarketDataUnavailable(
                                    "market_data_stream_error", str(message.get("code"))
                                )
                            snapshot = self._fold(symbol, message, state, seed)
                            if snapshot is None:
                                continue
                            if last_emit and snapshot.event_time - last_emit < timedelta(
                                seconds=1
                            ):
                                continue
                            last_emit = snapshot.event_time
                            yield snapshot
            except (OSError, websockets.WebSocketException, MarketDataUnavailable):
                await asyncio.sleep(delay)
                delay = min(max_reconnect_delay_seconds, delay * 2)

    def _fold(
        self,
        symbol: str,
        message: dict[str, Any],
        state: dict[str, Decimal],
        seed: MarketSnapshot,
    ) -> MarketSnapshot | None:
        kind = message.get("T")
        if message.get("S") != symbol or kind not in {"q", "t"}:
            return None
        try:
            if kind == "q":
                if Decimal(str(message["bp"])) <= 0 or Decimal(str(message["ap"])) <= 0:
                    return None
                state["bid"], state["ask"] = _dec(message["bp"]), _dec(message["ap"])
            else:
                state["last"] = _dec(message["p"])
            now = self._clock.now().astimezone(UTC)
            return MarketSnapshot(
                symbol=symbol,
                bid=state["bid"],
                ask=max(state["ask"], state["bid"]),
                last=state["last"],
                session_volume=seed.session_volume,
                session_dollar_volume=seed.session_dollar_volume,
                event_time=min(_time(message["t"]), now),
                received_time=now,
                processed_time=now,
                provider=self.provider,
                feed=self.feed,
                is_delayed=False,
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return None
