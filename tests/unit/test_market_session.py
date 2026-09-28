"""US equity session: NYSE calendar, DST, trading day and the risk gates built on it."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_context, make_critic, make_proposal
from test_pipeline_components import make_snapshot
from test_trade_safety import PRICES, _Clock, _orchestrator

from trading_bot.core.clock import FixedClock
from trading_bot.db import AuditRepository, Database
from trading_bot.market.clock import MarketClock, SessionState, TimeBucket, trading_day
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import TradingMode


def at(iso: str) -> MarketClock:
    return MarketClock(FixedClock(datetime.fromisoformat(iso)))


@pytest.mark.parametrize(
    ("moment", "state", "bucket"),
    [
        ("2026-09-28T07:59:00-04:00", SessionState.PRE_MARKET, TimeBucket.OUTSIDE),
        ("2026-09-28T09:29:00-04:00", SessionState.PRE_MARKET, TimeBucket.OUTSIDE),
        ("2026-09-28T09:30:00-04:00", SessionState.OPENING, TimeBucket.OPENING_DISCOVERY),
        ("2026-09-28T10:15:00-04:00", SessionState.REGULAR, TimeBucket.MORNING),
        ("2026-09-28T12:00:00-04:00", SessionState.MIDDAY, TimeBucket.MIDDAY),
        ("2026-09-28T14:30:00-04:00", SessionState.REGULAR, TimeBucket.AFTERNOON),
        ("2026-09-28T15:10:00-04:00", SessionState.POWER_HOUR, TimeBucket.AFTERNOON),
        ("2026-09-28T15:50:00-04:00", SessionState.CLOSING, TimeBucket.CLOSING_FLOW),
        ("2026-09-28T16:00:00-04:00", SessionState.AFTER_HOURS, TimeBucket.OUTSIDE),
        ("2026-09-28T20:00:00-04:00", SessionState.CLOSED, TimeBucket.OUTSIDE),
        ("2026-09-28T03:59:00-04:00", SessionState.CLOSED, TimeBucket.OUTSIDE),
    ],
)
def test_normal_day_walks_every_session_state(moment: str, state, bucket) -> None:
    snapshot = at(moment).snapshot()
    assert snapshot.state == state
    assert snapshot.time_bucket == bucket
    assert snapshot.is_open == (state not in {
        SessionState.PRE_MARKET, SessionState.AFTER_HOURS, SessionState.CLOSED
    })


def test_weekend_is_closed_and_points_to_monday() -> None:
    snapshot = at("2026-09-26T11:00:00-04:00").snapshot()  # Saturday
    assert snapshot.state == SessionState.CLOSED
    assert not snapshot.is_session_day
    assert snapshot.next_open == datetime(2026, 9, 28, 13, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "holiday",
    ["2026-04-03", "2026-06-19", "2026-11-26", "2026-12-25", "2026-01-01"],
    ids=["good-friday", "juneteenth", "thanksgiving", "christmas", "new-year"],
)
def test_nyse_holidays_are_closed(holiday: str) -> None:
    snapshot = at(f"{holiday}T11:00:00-05:00").snapshot()
    assert snapshot.state == SessionState.CLOSED
    assert not snapshot.is_session_day


@pytest.mark.parametrize("day", ["2026-11-27", "2026-12-24"])
def test_early_close_ends_the_session_at_1pm(day: str) -> None:
    clock = at(f"{day}T12:50:00-05:00")
    snapshot = clock.snapshot()
    assert snapshot.early_close
    assert snapshot.close_at == datetime.fromisoformat(f"{day}T13:00:00-05:00")
    assert snapshot.state == SessionState.CLOSING
    assert snapshot.minutes_to_close == 10
    assert at(f"{day}T13:05:00-05:00").state() == SessionState.AFTER_HOURS


def test_open_follows_daylight_saving_in_utc() -> None:
    # 2026-03-08 US clocks spring forward: the 09:30 ET open moves from 14:30Z to 13:30Z.
    assert at("2026-03-06T15:00:00+00:00").snapshot().open_at == datetime(
        2026, 3, 6, 14, 30, tzinfo=UTC
    )
    assert at("2026-03-09T15:00:00+00:00").snapshot().open_at == datetime(
        2026, 3, 9, 13, 30, tzinfo=UTC
    )
    # 2026-11-01 clocks fall back: the open returns to 14:30Z.
    assert at("2026-10-30T15:00:00+00:00").snapshot().open_at == datetime(
        2026, 10, 30, 13, 30, tzinfo=UTC
    )
    assert at("2026-11-02T15:00:00+00:00").snapshot().open_at == datetime(
        2026, 11, 2, 14, 30, tzinfo=UTC
    )


def test_trading_day_is_the_new_york_date_not_the_utc_date() -> None:
    # 21:30 ET on Sep 28 is already Sep 29 in UTC; PnL still belongs to Sep 28.
    assert trading_day(datetime(2026, 9, 29, 1, 30, tzinfo=UTC)) == date(2026, 9, 28)
    with pytest.raises(ValueError):
        trading_day(datetime(2026, 9, 29, 1, 30))


def test_sleep_until_pre_market_is_bounded_by_the_next_session() -> None:
    friday_evening = at("2026-09-25T21:00:00-04:00")
    wait = friday_evening.seconds_until_pre_market()
    assert wait == pytest.approx((timedelta(days=2, hours=7)).total_seconds())


@pytest.mark.parametrize(
    ("session", "reason"),
    [
        ("CLOSED", "market_closed"),
        ("PRE_MARKET", "premarket_trading_disabled"),
        ("AFTER_HOURS", "after_hours_trading_disabled"),
        ("UNKNOWN", "market_session_unknown"),
    ],
)
def test_risk_engine_blocks_entries_outside_the_regular_session(
    now: datetime, risk_config, session: str, reason: str
) -> None:
    decision = RiskEngine(risk_config, FixedClock(now)).evaluate_entry(
        make_proposal(now), make_critic(now), make_context(market_session=session)
    )
    assert decision.verdict == "DENY"
    assert reason in decision.reasons


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"minutes_since_open": 2}, "opening_protection_window"),
        ({"minutes_to_close": 10}, "end_of_day_cutoff"),
        ({"minutes_to_close": None}, "end_of_day_cutoff"),
        ({"entries_today": 10}, "max_trades_per_day_reached"),
    ],
)
def test_risk_engine_session_limits(now: datetime, risk_config, updates, reason: str) -> None:
    decision = RiskEngine(risk_config, FixedClock(now)).evaluate_entry(
        make_proposal(now), make_critic(now), make_context(**updates)
    )
    assert decision.verdict == "DENY"
    assert reason in decision.reasons


def test_regular_session_entry_is_still_allowed(now: datetime, risk_config) -> None:
    decision = RiskEngine(risk_config, FixedClock(now)).evaluate_entry(
        make_proposal(now), make_critic(now), make_context(entries_today=2)
    )
    assert decision.verdict == "ALLOW", decision.reasons


@pytest.mark.asyncio
async def test_state_counts_entries_per_new_york_day_and_blocks_the_fourth(
    tmp_path, risk_config, now: datetime
) -> None:
    clock = _Clock(now)
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'trades.db'}")
    await database.initialize()
    orchestrator, state = _orchestrator(database, clock, risk_config)

    async def context():
        return await state.risk_context(
            mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=clock.now()
        )

    first = await context()
    assert first.market_session == "REGULAR"
    assert first.entries_today == 0
    result = await orchestrator.run_cycle(make_snapshot(clock.now()), PRICES, first)
    assert result.status == "EXECUTED"
    assert (await context()).entries_today == 1
    # Next New York day starts from zero again.
    clock.advance(timedelta(days=1))
    assert (await context()).entries_today == 0
    await database.close()


@pytest.mark.asyncio
async def test_open_position_is_flattened_before_the_close(tmp_path, risk_config) -> None:
    # Enter at 15:40 ET (20 minutes left), then reach the 10-minute end-of-day window.
    clock = _Clock(datetime(2026, 9, 14, 19, 40, tzinfo=UTC))
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'eod.db'}")
    await database.initialize()
    orchestrator, state = _orchestrator(database, clock, risk_config)

    async def context():
        return await state.risk_context(
            mode=TradingMode.PAPER, starting_equity=Decimal("10000"), asset="QQQ", now=clock.now()
        )

    opened = await orchestrator.run_cycle(make_snapshot(clock.now()), PRICES, await context())
    assert opened.status == "EXECUTED"
    # The per-position time stop never extends past the close.
    assert int((await state.open_positions())[0]["max_hold_seconds"]) <= 20 * 60

    clock.advance(timedelta(minutes=11))
    late = await context()
    assert late.market_session == "CLOSING"
    assert late.minutes_to_close == 9
    closed = await orchestrator.run_cycle(make_snapshot(clock.now()), PRICES, late)
    assert closed.status == "POSITION_EXITED"
    assert await state.open_positions() == []
    decisions = [
        row["payload"]
        for row in await AuditRepository(database).recent("system_events", limit=20)
        if row["payload"].get("status") == "POSITION_DECISION"
    ]
    assert decisions[0]["reasons"] == ["end_of_day_flatten"]
    await database.close()
