"""QQQ market data: Alpaca adapter (mocked HTTP/WebSocket), credentials, features, audit."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import respx
from pydantic import SecretStr

import trading_bot.market_data.alpaca as alpaca_module
from trading_bot.config import load_settings
from trading_bot.core.clock import FixedClock
from trading_bot.data.features import FeatureEngine, average_true_range, relative_strength_index
from trading_bot.market.clock import regular_session_only
from trading_bot.market_data import FixtureMarketData, MarketDataUnavailable, build_market_data
from trading_bot.market_data.alpaca import DATA_URL, AlpacaMarketData
from trading_bot.schemas.trading import Candle
from trading_bot.security.credentials import (
    ALPACA_PAPER_KEY_ID,
    ALPACA_PAPER_SECRET_KEY,
    AlpacaPaperCredentials,
    alpaca_paper_credentials,
)
from trading_bot.simulation.data_audit import audit_minute_bars

NOW = datetime(2026, 9, 28, 15, 0, 30, tzinfo=UTC)  # Monday 11:00:30 New York
CREDS = AlpacaPaperCredentials(key_id="PKTESTKEY123", secret_key="s3cr3t")  # noqa: S106


def snapshot_payload(**quote: object) -> dict:
    return {
        "symbol": "QQQ",
        "latestTrade": {"p": 481.23, "s": 10, "t": "2026-09-28T15:00:29.123456789Z"},
        "latestQuote": {
            "bp": quote.get("bp", 481.2),
            "ap": quote.get("ap", 481.25),
            "bs": 1,
            "as": 2,
            "t": "2026-09-28T15:00:29.5Z",
        },
        "dailyBar": {"t": "2026-09-28T04:00:00Z", "o": 480, "h": 482, "l": 479, "c": 481,
                     "v": 1_000_000, "vw": 480.5, "n": 9000},
    }


def adapter(clock: FixedClock | None = None) -> AlpacaMarketData:
    return AlpacaMarketData(CREDS, clock or FixedClock(NOW), feed="iex")


@pytest.mark.asyncio
@respx.mock
async def test_snapshot_uses_official_endpoint_headers_and_labels_its_feed() -> None:
    route = respx.get(f"{DATA_URL}/v2/stocks/QQQ/snapshot").mock(
        return_value=httpx.Response(200, json=snapshot_payload())
    )
    snapshot = await adapter().fetch_snapshot("QQQ")
    request = route.calls.last.request
    assert request.headers["APCA-API-KEY-ID"] == "PKTESTKEY123"
    assert request.headers["APCA-API-SECRET-KEY"] == "s3cr3t"
    assert request.url.params["feed"] == "iex"
    assert (snapshot.bid, snapshot.ask, snapshot.last) == (
        Decimal("481.2"), Decimal("481.25"), Decimal("481.23")
    )
    assert snapshot.session_volume == Decimal("1000000")
    assert snapshot.session_dollar_volume == Decimal("480500000.0")
    assert (snapshot.provider, snapshot.feed, snapshot.is_delayed) == ("alpaca", "iex", False)
    assert snapshot.event_time == datetime(2026, 9, 28, 15, 0, 29, 500000, tzinfo=UTC)


@pytest.mark.asyncio
@respx.mock
async def test_yesterdays_daily_bar_is_not_todays_session_volume() -> None:
    payload = snapshot_payload()
    payload["dailyBar"]["t"] = "2026-09-25T04:00:00Z"
    respx.get(f"{DATA_URL}/v2/stocks/QQQ/snapshot").mock(
        return_value=httpx.Response(200, json=payload)
    )
    assert (await adapter().fetch_snapshot("QQQ")).session_volume == 0


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (401, {}, "market_data_auth_failed"),
        (403, {}, "market_data_auth_failed"),
        (429, {}, "market_data_rate_limited"),
        (500, {}, "market_data_unavailable"),
        (200, snapshot_payload(bp=0), "market_data_invalid_quote"),
    ],
)
async def test_failures_raise_stable_codes_and_never_guess_prices(status, body, code) -> None:
    respx.get(f"{DATA_URL}/v2/stocks/QQQ/snapshot").mock(
        return_value=httpx.Response(status, json=body)
    )
    with pytest.raises(MarketDataUnavailable) as caught:
        await adapter().fetch_snapshot("QQQ")
    assert caught.value.code == code


def bar(minute: int, close: float = 481.0, **extra: object) -> dict:
    start = datetime(2026, 9, 28, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    return {"t": start.isoformat().replace("+00:00", "Z"), "o": close, "h": close + 0.2,
            "l": close - 0.2, "c": close, "v": 1000, "vw": close, "n": 20, **extra}


@pytest.mark.asyncio
@respx.mock
async def test_recent_candles_are_closed_bars_in_time_order() -> None:
    # Newest first (sort=desc); the bar starting 15:00 is still forming at 15:00:30.
    rows = [bar(90), bar(89), bar(88)]
    route = respx.get(f"{DATA_URL}/v2/stocks/bars").mock(
        return_value=httpx.Response(200, json={"bars": {"QQQ": rows}, "next_page_token": None})
    )
    candles = await adapter().fetch_candles("QQQ", interval="1m", limit=3)
    params = route.calls.last.request.url.params
    assert (params["timeframe"], params["sort"], params["feed"]) == ("1Min", "desc", "iex")
    assert [c.event_time.minute for c in candles] == [59, 0]  # 14:59 and 15:00 closes
    assert all(c.vwap == Decimal("481.0") for c in candles)


@pytest.mark.asyncio
@respx.mock
async def test_history_follows_page_tokens_and_drops_duplicates() -> None:
    pages = iter(
        [
            {"bars": {"QQQ": [bar(0), bar(1)]}, "next_page_token": "abc"},
            {"bars": {"QQQ": [bar(1), bar(2)]}, "next_page_token": None},
        ]
    )
    route = respx.get(f"{DATA_URL}/v2/stocks/bars").mock(
        side_effect=lambda request: httpx.Response(200, json=next(pages))
    )
    candles = await adapter().fetch_candle_range(
        "QQQ", start=NOW - timedelta(days=1), end=NOW
    )
    assert len(candles) == 3
    assert route.calls[1].request.url.params["page_token"] == "abc"  # noqa: S105
    assert route.calls[0].request.url.params["limit"] == "10000"


def test_credentials_come_from_env_first_then_keychain_and_never_print() -> None:
    values = {ALPACA_PAPER_KEY_ID: "PKFROMKEYCHAIN", ALPACA_PAPER_SECRET_KEY: "kc-secret"}

    class Store:
        def get(self, name: str) -> str | None:
            return values.get(name)

    secrets = load_settings().secrets.model_copy(
        update={"alpaca_paper_key_id": None, "alpaca_paper_secret_key": None}
    )
    from_keychain = alpaca_paper_credentials(secrets, Store())  # type: ignore[arg-type]
    assert from_keychain is not None and from_keychain.key_id == "PKFROMKEYCHAIN"
    env = secrets.model_copy(
        update={
            "alpaca_paper_key_id": SecretStr("PKFROMENV"),
            "alpaca_paper_secret_key": SecretStr("env-secret"),
        }
    )
    from_env = alpaca_paper_credentials(env, Store())  # type: ignore[arg-type]
    assert from_env is not None and from_env.key_id == "PKFROMENV"
    assert "env-secret" not in repr(from_env) and "PKFROMENV" not in repr(from_env)

    class Empty:
        def get(self, name: str) -> str | None:
            return None

    assert alpaca_paper_credentials(secrets, Empty()) is None  # type: ignore[arg-type]


def test_factory_fails_closed_without_keys_and_serves_fixture_on_request() -> None:
    settings = load_settings()
    live = settings.model_copy(
        update={
            "secrets": settings.secrets.model_copy(
                update={"alpaca_paper_key_id": None, "alpaca_paper_secret_key": None}
            )
        }
    )
    import trading_bot.market_data.factory as factory

    original = factory.alpaca_paper_credentials
    factory.alpaca_paper_credentials = lambda _secrets: None  # type: ignore[assignment]
    try:
        with pytest.raises(MarketDataUnavailable) as caught:
            build_market_data(live, FixedClock(NOW))
        assert caught.value.code == "market_data_credentials_missing"
    finally:
        factory.alpaca_paper_credentials = original
    fixture = settings.model_copy(
        update={
            "public": settings.public.model_copy(
                update={
                    "market_data": settings.public.market_data.model_copy(
                        update={"provider": "fixture"}
                    )
                }
            )
        }
    )
    assert isinstance(build_market_data(fixture, FixedClock(NOW)), FixtureMarketData)
    assert isinstance(build_market_data(live, FixedClock(NOW), credentials=CREDS), AlpacaMarketData)


@pytest.mark.asyncio
async def test_stream_authenticates_subscribes_and_folds_quotes(monkeypatch) -> None:
    sent: list[dict] = []
    stop = asyncio.Event()
    frames = [
        [{"T": "success", "msg": "authenticated"}],
        [{"T": "q", "S": "QQQ", "bp": 481.3, "ap": 481.35, "t": "2026-09-28T15:00:29Z"}],
        [{"T": "q", "S": "SPY", "bp": 1, "ap": 2, "t": "2026-09-28T15:00:29.5Z"}],
        [{"T": "t", "S": "QQQ", "p": 481.34, "t": "2026-09-28T15:00:30Z"}],
    ]

    class Socket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

        def __aiter__(self):
            async def gen():
                for frame in frames:
                    yield json.dumps(frame)
                stop.set()

            return gen()

    source = adapter()

    async def fake_snapshot(symbol: str):
        return await FixtureMarketData(FixedClock(NOW)).fetch_snapshot(symbol)

    monkeypatch.setattr(source, "fetch_snapshot", fake_snapshot)
    monkeypatch.setattr(alpaca_module.websockets, "connect", lambda url: Socket())
    received = [
        item
        async for item in source.stream_snapshots(
            "QQQ", max_reconnect_delay_seconds=1, stop_event=stop
        )
    ]
    assert sent[0] == {"action": "auth", "key": "PKTESTKEY123", "secret": "s3cr3t"}
    assert sent[1] == {"action": "subscribe", "quotes": ["QQQ"], "trades": ["QQQ"]}
    assert received[0].bid == Decimal("481.3")
    assert all(item.symbol == "QQQ" and item.feed == "iex" for item in received)
    # Throttled to one snapshot per second: the trade 1 s later is emitted too.
    assert [item.last for item in received][-1] == Decimal("481.34")


def _candle(minute: int, close: str, volume: str = "100", day: int = 28) -> Candle:
    end = datetime(2026, 9, day, 13, 30, tzinfo=UTC) + timedelta(minutes=minute)
    price = Decimal(close)
    return Candle(symbol="QQQ", interval="1m", open=price, high=price + 1, low=price - 1,
                  close=price, volume=Decimal(volume), vwap=price, trades=1,
                  event_time=end, received_time=end, processed_time=end)


def test_session_vwap_resets_at_the_open_and_features_are_deterministic() -> None:
    # A pre-market bar (09:20 ET) must not enter the session VWAP.
    premarket = _candle(-10, "400", volume="100000")
    session = [_candle(i, str(480 + i), volume="100") for i in range(1, 30)]
    features = FeatureEngine().compute(
        "QQQ", tuple(c.close for c in session[-20:]), (premarket, *session)
    )
    expected_vwap = sum((c.close for c in session), Decimal("0")) / len(session)
    assert features.session_vwap == expected_vwap
    assert features.session_bars == len(session)
    assert features.atr is not None and features.atr > 0
    assert features.rsi == Decimal("100")  # only rising closes
    assert features.ema_fast is not None and features.ema_slow is not None
    assert features.vwap_distance_atr is not None and features.vwap_distance_atr > 0
    # Without candles, session features are absent rather than invented.
    bare = FeatureEngine().compute("QQQ", tuple(c.close for c in session[-5:]))
    assert bare.session_vwap is None and bare.atr is None and bare.rsi is None


def test_atr_and_rsi_need_enough_bars() -> None:
    bars = [_candle(i, "480") for i in range(1, 10)]
    assert average_true_range(bars) is None
    assert relative_strength_index([Decimal("1")] * 5) is None


def test_regular_session_filter_and_data_audit() -> None:
    session = [_candle(i, "480") for i in range(1, 391)]  # full Monday session
    extended = [_candle(-60, "479"), _candle(400, "481")]  # 08:30 and 16:10 ET
    weekend = [_candle(10, "480", day=27)]  # Sunday
    kept = regular_session_only([*extended, *session, *weekend])
    assert kept == tuple(session)

    clean = audit_minute_bars(session)
    assert clean.ok and clean.missing_bars == 0 and clean.expected_bars == 390

    gappy = audit_minute_bars(session[::2])
    assert not gappy.ok and "too_many_missing_bars" in gappy.issues

    doubled = audit_minute_bars([*session, session[5]])
    assert not doubled.ok and "duplicate_bars" in doubled.issues

    jump = [*session[:100], _candle(101, "600"), *session[101:]]
    assert "implausible_price_jumps" in audit_minute_bars(jump).issues

    mixed = audit_minute_bars([extended[0], *session, extended[1]])
    assert mixed.ok and "bars_outside_regular_session" in mixed.issues
