"""The broker account is the capital: no configured equity, USD caps still bind."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_context, make_critic, make_proposal

from trading_bot.broker.base import BrokerAccount, BrokerPosition
from trading_bot.config.loader import local_config_path, shares_app_state
from trading_bot.db.database import Database
from trading_bot.db.state import TradingStateRepository
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import TradingMode


def _account(equity: str = "100000", label: str = "PA…DEMO") -> BrokerAccount:
    return BrokerAccount(
        provider="alpaca",
        environment="paper",
        account_label=label,
        currency="USD",
        status="ACTIVE",
        equity=Decimal(equity),
        cash=Decimal(equity),
        buying_power=Decimal(equity) * 4,
    )


async def _state(tmp_path) -> tuple[Database, TradingStateRepository]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'equity.db'}")
    await database.initialize()
    return database, TradingStateRepository(database)


@pytest.mark.asyncio
async def test_risk_context_uses_the_synced_broker_equity(tmp_path, now: datetime) -> None:
    database, state = await _state(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="broker_equity_unavailable"):
            # External broker, never synced: no equity to size from.
            await state.risk_context(
                mode=TradingMode.PAPER, starting_equity=None, asset="QQQ", now=now,
                require_reconciliation=True,
            )
        position = BrokerPosition(
            symbol="QQQ", quantity=Decimal("2"), average_entry_price=Decimal("480"),
            market_value=Decimal("970"), unrealized_pnl=Decimal("10"),
        )
        await state.sync_broker_account(_account("100010"), [position], now=now)
        context = await state.risk_context(
            mode=TradingMode.PAPER, starting_equity=None, asset="QQQ", now=now,
            require_reconciliation=True,
        )
        # Realized equity: the broker's marked equity minus its unrealized PnL.
        assert context.equity == Decimal("100000")
        assert context.account_high_water_mark == Decimal("100000")
        assert context.broker_equity_fresh is True
        synced = await state.latest_broker_account()
        assert synced is not None and synced["account_label"] == "PA…DEMO"
        assert synced["buying_power"] == "400040"

        stale = await state.risk_context(
            mode=TradingMode.PAPER, starting_equity=None, asset="QQQ",
            now=now + timedelta(minutes=16), require_reconciliation=True,
        )
        assert stale.broker_equity_fresh is False
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_a_new_broker_account_starts_a_new_high_water_mark(
    tmp_path, now: datetime
) -> None:
    database, state = await _state(tmp_path)
    try:
        await state.sync_broker_account(_account("100000"), [], now=now)
        await state.sync_broker_account(_account("100500"), [], now=now)
        same = await state.account_projection()
        assert same["high_water_mark"] == Decimal("100500")
        # The owner reset the paper account to 25 000: not a 75 % drawdown.
        await state.sync_broker_account(_account("25000", label="PA…7Q1Z"), [], now=now)
        reset = await state.account_projection()
        assert reset["equity"] == Decimal("25000")
        assert reset["high_water_mark"] == Decimal("25000")
    finally:
        await database.close()


def test_risk_scales_with_the_broker_account(risk_config, now: datetime) -> None:
    engine = RiskEngine(risk_config, clock=_clock(now))
    context = make_context(equity=Decimal("100000"), account_high_water_mark=Decimal("100000"))
    # Medio profile: 0.5 % of the 100 000 Alpaca Paper equity.
    assert engine.risk_budget(context) == Decimal("500")
    # A dollar cap, when the owner sets one, still binds.
    capped = RiskEngine(
        risk_config.model_copy(update={"max_base_risk_usd": Decimal("10")}), clock=_clock(now)
    )
    decision = capped.evaluate_entry(make_proposal(now), make_critic(now), context)
    assert decision.allowed_risk_usd <= Decimal("10")


def test_stale_broker_equity_blocks_entries(risk_config, now: datetime) -> None:
    engine = RiskEngine(risk_config, clock=_clock(now))
    context = make_context(broker_equity_fresh=False)
    decision = engine.evaluate_entry(make_proposal(now), make_critic(now), context)
    assert decision.verdict == "DENY"
    assert "broker_equity_unavailable" in decision.reasons


def test_cli_shares_the_desktop_app_config_once_it_exists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HYVERION_APP_SUPPORT", str(tmp_path))
    monkeypatch.setenv("HYVERION_SHARE_APP_STATE", "1")
    assert shares_app_state() is False  # the app was never set up here
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "local.yaml").write_text("config_version: 2\n", encoding="utf-8")
    assert shares_app_state() is True
    assert local_config_path() == tmp_path / "config" / "local.yaml"
    monkeypatch.setenv("HYVERION_SHARE_APP_STATE", "0")
    assert shares_app_state() is False


def _clock(now: datetime):
    from trading_bot.core.clock import FixedClock

    return FixedClock(now)
