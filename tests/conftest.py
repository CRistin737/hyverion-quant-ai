from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trading_bot.config import load_settings
from trading_bot.config.models import RiskConfig
from trading_bot.core.clock import FixedClock
from trading_bot.schemas.assessments import CriticAssessment
from trading_bot.schemas.common import Side, TradingMode
from trading_bot.schemas.trading import RiskContext, SignalComponents, TradeProposal

# Module-level load_settings() calls run before fixtures: isolate them too.
os.environ["HYVERION_SHARE_APP_STATE"] = "0"
# The engine's network sensors (SEC, Fed, BLS, Alpaca) never run in tests.
os.environ["HYVERION_INTELLIGENCE"] = "0"


class _EmptyKeychain:
    """Tests must never read the developer's real macOS Keychain."""

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        return None

    def delete(self, name: str) -> None:
        return None

    def has(self, name: str) -> bool:
        return False


@pytest.fixture(autouse=True)
def _isolated_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_bot.security.credentials as credentials

    monkeypatch.setattr(credentials, "KeyringSecretStore", _EmptyKeychain)
    for name in ("ALPACA_PAPER_KEY_ID", "ALPACA_PAPER_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    # Never read the desktop app's real config, database or broker choice.
    monkeypatch.setenv("HYVERION_SHARE_APP_STATE", "0")
    # Tests run on the internal simulator unless they build an Alpaca broker.
    monkeypatch.setenv("BROKER_PROVIDER", "simulator")


@pytest.fixture
def now() -> datetime:
    # Monday 11:00 New York time: inside the regular US equity session.
    return datetime(2026, 9, 14, 15, 0, tzinfo=UTC)


@pytest.fixture
def clock(now: datetime) -> FixedClock:
    return FixedClock(now)


@pytest.fixture
def risk_config() -> RiskConfig:
    return load_settings().public.risk


def make_context(**updates: object) -> RiskContext:
    values: dict[str, object] = {
        "mode": TradingMode.PAPER,
        "equity": Decimal("10000"),
        "account_high_water_mark": Decimal("10000"),
        "realized_net_pnl_today": Decimal("0"),
        "unrealized_pnl": Decimal("0"),
        "intraday_peak_realized_pnl": Decimal("0"),
        "intraday_peak_total_pnl": Decimal("0"),
        "fees_today": Decimal("0"),
        "realized_net_pnl_week": Decimal("0"),
        "current_exposure_usd": Decimal("0"),
        "asset_exposure_usd": Decimal("0"),
        "correlated_open_risk_usd": Decimal("0"),
        "open_remaining_risk_usd": Decimal("0"),
        "open_positions": 0,
        "losing_streak": 0,
        # A regular-session moment unless a test says otherwise.
        "market_session": "REGULAR",
        "minutes_since_open": 60,
        "minutes_to_close": 240,
    }
    values.update(updates)
    return RiskContext.model_validate(values)


def make_proposal(now: datetime, **updates: object) -> TradeProposal:
    values: dict[str, object] = {
        "proposal_id": "proposal-1",
        "asset": "QQQ",
        "side": Side.BUY,
        "entry_price": Decimal("100"),
        "stop_price": Decimal("99"),
        "target_price": Decimal("102"),
        "quantity": Decimal("5"),
        "expected_r": Decimal("2"),
        "time_horizon_seconds": 3600,
        "signal_score": Decimal("95"),
        "signal_components": SignalComponents(
            technical=95,
            regime=95,
            liquidity=95,
            risk_reward=95,
            news=95,
            sentiment=95,
            historical_expectancy=95,
            data_freshness=100,
            agent_agreement=95,
            weights_version="v1",
        ),
        "confirmation_categories": frozenset(
            {"technical", "regime", "liquidity", "risk", "strategy"}
        ),
        "evidence": (),
        "contradictory_evidence": (),
        "invalidations": ("stop_reached",),
        "expected_fees_usd": Decimal("1"),
        "estimated_slippage_usd": Decimal("1"),
        "estimated_slippage_bps": Decimal("10"),
        "observed_spread_bps": Decimal("5"),
        "observed_liquidity_usd": Decimal("1000000"),
        "expected_net_value_usd": Decimal("3"),
        "why_now": "test",
        "why_not_trade": (),
        "is_a_plus": True,
        "created_at": now,
    }
    values.update(updates)
    return TradeProposal.model_validate(values)


def make_critic(now: datetime, **updates: object) -> CriticAssessment:
    values: dict[str, object] = {
        "agent_id": "critic",
        "agent_version": "1.0.0",
        "asset": "QQQ",
        "assessed_at": now,
        "confidence": Decimal("95"),
        "proposal_id": "proposal-1",
        "verdict": "APPROVE",
        "evidence": (),
        "limitations": (),
        "critical_conflicts": (),
        "weak_assumptions": (),
        "required_revisions": (),
    }
    values.update(updates)
    return CriticAssessment.model_validate(values)


def control_client(app: FastAPI) -> TestClient:
    """TestClient for the control API carrying its (possibly ephemeral) bearer token."""

    token = app.state.control_api_token
    return TestClient(app, headers={"Authorization": f"Bearer {token}"})
