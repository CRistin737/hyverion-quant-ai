"""Regression tests for the financial, agent and security review of 2026-09-28."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import make_context, make_critic, make_proposal
from test_execution import make_decision, make_intent

from trading_bot.broker.execution import ExecutionEngine
from trading_bot.broker.simulator import SimulatedBroker
from trading_bot.config import ConfigManager, ConfigPatch, load_settings
from trading_bot.config.models import AutonomyConfig, RiskConfig
from trading_bot.core.agent_pipeline import _bind_output
from trading_bot.core.clock import FixedClock
from trading_bot.core.context import AssessmentBundle
from trading_bot.core.orchestrator import _resize
from trading_bot.core.routines import ROUTINE_EVENT, run_due_routines
from trading_bot.db.database import Database
from trading_bot.db.repositories import AuditRepository
from trading_bot.providers.base import ProviderError
from trading_bot.providers.cli_path import ensure_cli_path
from trading_bot.reports.period import build_period_report, closed_trades, trades_csv
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.assessments import SensorAssessment

NOW = datetime(2026, 9, 28, 17, 0, tzinfo=UTC)


# --- Risk: open positions count against the loss limits -----------------------


def test_open_risk_and_floating_losses_reduce_the_daily_room(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    engine = RiskEngine(risk_config, clock)
    # 2 % of 10 000 = 200 a day; 150 already at risk in open stops + 40 floating.
    context = make_context(open_remaining_risk_usd=Decimal("150"), unrealized_pnl=Decimal("-40"))
    decision = engine.evaluate_entry(make_proposal(now), make_critic(now), context)
    assert decision.allowed_risk_usd <= Decimal("10")
    full = make_context(open_remaining_risk_usd=Decimal("170"), unrealized_pnl=Decimal("-40"))
    denied = engine.evaluate_entry(make_proposal(now), make_critic(now), full)
    assert "daily_loss_limit_reached" in denied.reasons


def test_drawdown_counts_an_open_loss(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    # Realized equity is at the peak, but the open position is down 10 %.
    context = make_context(unrealized_pnl=Decimal("-1000"))
    decision = RiskEngine(risk_config, clock).evaluate_entry(
        make_proposal(now), make_critic(now), context
    )
    assert "account_high_water_mark_drawdown_reached" in decision.reasons


def test_a_tight_stop_is_resized_to_fit_the_exposure_limit(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    engine = RiskEngine(risk_config, clock)
    context = make_context()
    capacity = engine.notional_capacity(context)
    assert capacity == Decimal("10000")  # 100 % of equity, nothing open
    big = make_proposal(now).model_copy(update={"quantity": Decimal("250")})
    fitted = _resize(big, capacity / big.entry_price)
    assert fitted.notional_usd <= capacity
    assert fitted.expected_fees_usd < big.expected_fees_usd
    assert fitted.stop_price == big.stop_price


def test_execution_refuses_a_stop_wider_than_approved(now: datetime) -> None:
    engine = ExecutionEngine(SimulatedBroker(FixedClock(now)), clock=FixedClock(now))
    decision = make_decision(now).model_copy(update={"approved_stop_price": Decimal("99")})
    wider = make_intent(now).model_copy(update={"stop_price": Decimal("98")})
    with pytest.raises(PermissionError, match="stop is wider"):
        engine._authorize(wider, decision)
    closer = make_intent(now).model_copy(update={"stop_price": Decimal("99.5")})
    engine._authorize(closer, decision)


# --- Configuration: never write or run an invalid one -------------------------


def test_risk_limits_are_ordered_and_bounded() -> None:
    risk = load_settings().public.risk
    with pytest.raises(ValueError):
        RiskConfig.model_validate({**risk.model_dump(), "base_risk_percent": "3"})
    with pytest.raises(ValueError):
        RiskConfig.model_validate({**risk.model_dump(), "max_total_exposure_percent": "400"})


def test_an_envelope_that_omits_a_key_keeps_its_default_bound() -> None:
    autonomy = AutonomyConfig.model_validate(
        {"envelope": {"base_risk_percent": {"minimum": "0.1", "maximum": "0.8"}}}
    )
    assert autonomy.envelope["base_risk_percent"].maximum == Decimal("0.8")
    assert autonomy.envelope["weekly_loss_percent"].maximum == Decimal("10")


def test_two_patches_cannot_leave_an_invalid_file(tmp_path: Path) -> None:
    settings = load_settings()
    manager = ConfigManager(tmp_path)
    with pytest.raises(ValueError):
        manager.apply(ConfigPatch(base_risk_percent="1", daily_loss_percent="0.5"), settings)
    assert not manager.local_path.exists()


def test_the_engine_keeps_the_last_good_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    import trading_bot.main as main

    good = load_settings()
    monkeypatch.setattr(main, "_LAST_GOOD_SETTINGS", None)
    monkeypatch.setattr(main, "load_settings", lambda *a, **k: good)
    assert main._runtime_settings() is good
    assert main._SETTINGS_INVALID is None

    def broken(*_: object, **__: object) -> None:
        raise ValueError("risk outside the autonomy envelope")

    monkeypatch.setattr(main, "load_settings", broken)
    assert main._runtime_settings() is good
    assert main._SETTINGS_INVALID == "ValueError"


# --- Busy tables never hide the last record ------------------------------------


@pytest.mark.asyncio
async def test_status_lookups_and_routines_survive_a_busy_event_table(tmp_path: Path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'busy.db'}")
    await database.initialize()
    audit = AuditRepository(database)
    morning = datetime(2026, 9, 28, 12, 40, tzinfo=UTC)  # 08:40 New York
    await audit.append(
        "system_events",
        {"status": ROUTINE_EVENT, "routine": "premarket", "session_date": "2026-09-28"},
        created_at=morning,
    )
    for index in range(700):
        await audit.append(
            "system_events", {"status": "NO_TRADE"}, created_at=morning + timedelta(seconds=index)
        )
    assert len(await audit.with_status("system_events", [ROUTINE_EVENT])) == 1
    calls: list[str] = []

    async def premarket() -> dict[str, str]:
        calls.append("premarket")
        return {}

    ran = await run_due_routines(
        audit, morning + timedelta(hours=1), is_trading_day=True, actions={"premarket": premarket}
    )
    assert ran == () and calls == []
    rows = await audit.between(
        "system_events", start=None, end=morning + timedelta(hours=1), page_size=100
    )
    assert len(rows) == 701  # paginated, never truncated
    await database.close()


# --- Reports -------------------------------------------------------------------


def _position(status: str, entry_fee: str, **extra: str) -> dict[str, object]:
    return {
        "payload": {
            "position_id": "p1",
            "asset": "QQQ",
            "status": status,
            "entry_fee_usd": entry_fee,
            "opened_at": "2026-07-01T14:00:00+00:00",
            "state_at": extra.pop("state_at"),
            **extra,
        }
    }


def test_partial_exits_add_up_in_the_report() -> None:
    rows = [
        _position("OPEN", "2", state_at="2026-07-01T14:00:00+00:00"),
        _position(
            "PARTIALLY_CLOSED",
            "1",
            state_at="2026-07-01T15:00:00+00:00",
            realized_net_pnl="30",
            exit_fee_usd="1",
            cumulative_realized_net_pnl="30",
            cumulative_exit_fees_usd="1",
        ),
        _position(
            "CLOSED",
            "0",
            state_at="2026-07-01T16:00:00+00:00",
            closed_at="2026-07-01T16:00:00+00:00",
            realized_net_pnl="20",
            exit_fee_usd="1",
            cumulative_realized_net_pnl="50",
            cumulative_exit_fees_usd="2",
        ),
    ]
    trades = closed_trades(rows, start=None, end=NOW)
    assert trades[0].net_pnl_usd == Decimal("50")
    assert trades[0].fees_usd == Decimal("4")


def test_a_losing_first_trade_is_drawdown_and_csv_is_formula_safe() -> None:
    trades = closed_trades(
        [
            _position(
                "CLOSED",
                "1",
                state_at="2026-07-01T16:00:00+00:00",
                closed_at="2026-07-01T16:00:00+00:00",
                cumulative_realized_net_pnl="-25",
                cumulative_exit_fees_usd="1",
                exit_reason='=HYPERLINK("x")',
            )
        ],
        start=None,
        end=NOW,
    )
    report = build_period_report(
        trades=trades, equity_rows=[], equity_before=None, start=None, end=NOW
    )
    assert report.max_drawdown_usd == Decimal("25")
    csv_text = trades_csv(report)
    assert "'=HYPERLINK" in csv_text
    assert csv_text.rstrip().endswith(",-25")  # numbers stay numbers


# --- Agents ----------------------------------------------------------------------


def _sensor(**updates: object) -> SensorAssessment:
    values: dict[str, object] = {
        "agent_id": "breadth",
        "agent_version": "1.1.0",
        "asset": "QQQ",
        "assessed_at": NOW,
        "confidence": Decimal("70"),
        "stance": "supports_long",
        "strength": Decimal("60"),
    }
    values.update(updates)
    return SensorAssessment.model_validate(values)


def test_model_answers_are_bound_to_the_call() -> None:
    impostor = _sensor(agent_id="macro")
    bound = _bind_output(impostor, "breadth", "QQQ", AssessmentBundle())
    assert bound.agent_id == "breadth"
    with pytest.raises(ProviderError):
        _bind_output(_sensor(asset="SPY"), "breadth", "QQQ", AssessmentBundle())


@pytest.mark.asyncio
async def test_an_old_quote_denies_the_entry(risk_config: RiskConfig, now: datetime) -> None:
    from test_pipeline_components import make_snapshot

    from trading_bot.core.orchestrator import MasterOrchestrator

    later = FixedClock(now + timedelta(minutes=5))
    orchestrator = MasterOrchestrator.__new__(MasterOrchestrator)
    orchestrator._quote_refresher = None
    orchestrator._max_quote_age = timedelta(seconds=10)
    orchestrator._clock = later
    context = await orchestrator._fresh_quote_context(make_snapshot(now), make_context())
    assert context.data_fresh is False
    decision = RiskEngine(risk_config, later).evaluate_entry(
        make_proposal(now), make_critic(now), context
    )
    assert "stale_market_data" in decision.reasons


def test_ai_clis_are_found_without_a_login_shell(tmp_path: Path) -> None:
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    environ = {"PATH": "/usr/bin:/bin"}
    from trading_bot.providers import cli_path

    original = cli_path.Path.home
    cli_path.Path.home = staticmethod(lambda: tmp_path)  # type: ignore[method-assign]
    try:
        path = ensure_cli_path(environ)
    finally:
        cli_path.Path.home = original  # type: ignore[method-assign]
    assert path.startswith("/usr/bin:/bin")
    assert str(tmp_path / ".local" / "bin") in path.split(":")
