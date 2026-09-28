from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from conftest import make_context, make_critic, make_proposal

from trading_bot.config.models import RiskConfig
from trading_bot.core.clock import FixedClock
from trading_bot.risk.engine import RiskEngine
from trading_bot.schemas.common import TradingMode


def evaluate(
    risk_config: RiskConfig,
    clock: FixedClock,
    now: datetime,
    *,
    context_updates: dict[str, object] | None = None,
    proposal_updates: dict[str, object] | None = None,
    critic_updates: dict[str, object] | None = None,
):
    return RiskEngine(risk_config, clock).evaluate_entry(
        make_proposal(now, **(proposal_updates or {})),
        make_critic(now, **(critic_updates or {})),
        make_context(**(context_updates or {})),
    )


def test_allows_conservative_paper_trade(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = evaluate(risk_config, clock, now)

    assert decision.verdict == "ALLOW"
    # Medio profile: 0.5 % of 10 000 equity, no dollar cap in PAPER.
    assert decision.allowed_risk_usd == Decimal("50")
    assert decision.candidate_worst_case_loss_usd == Decimal("7")
    assert decision.live_trading_allowed is False


def test_rounding_noise_does_not_reject_budget_boundary(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    risk_config = risk_config.model_copy(update={"max_base_risk_usd": Decimal("0.25")})
    decision = evaluate(
        risk_config,
        clock,
        now,
        context_updates={
            "equity": Decimal("1000"),
            "account_high_water_mark": Decimal("1000"),
        },
        proposal_updates={
            "quantity": Decimal("0.250000005"),
            "expected_fees_usd": Decimal("0"),
            "estimated_slippage_usd": Decimal("0"),
        },
    )

    assert "candidate_worst_case_loss_exceeds_allowed_risk" not in decision.reasons


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"realized_net_pnl_today": Decimal("-200")}, "daily_loss_limit_reached"),
        ({"realized_net_pnl_week": Decimal("-500")}, "weekly_loss_limit_reached"),
        ({"losing_streak": 3}, "maximum_losing_streak_reached"),
        ({"cooldown_active": True}, "loss_cooldown_active"),
        ({"data_fresh": False}, "stale_market_data"),
        (
            {"equity": Decimal("9000"), "account_high_water_mark": Decimal("10000")},
            "account_high_water_mark_drawdown_reached",
        ),
        (
            {"mode": TradingMode.LIVE, "reconciliation_ok": False},
            "exchange_reconciliation_failed",
        ),
        # Unresolved operations block entries in every mode, PAPER included.
        ({"unresolved_operations": 1}, "unresolved_operation_requires_recovery"),
    ],
)
def test_fail_closed_context_limits(
    risk_config: RiskConfig,
    clock: FixedClock,
    now: datetime,
    updates: dict[str, object],
    reason: str,
) -> None:
    decision = evaluate(risk_config, clock, now, context_updates=updates)

    assert decision.verdict == "DENY"
    assert reason in decision.reasons


def test_rejects_provider_or_critic_failure(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = evaluate(
        risk_config,
        clock,
        now,
        critic_updates={"verdict": "REJECT", "critical_conflicts": ("stale_news",)},
    )

    assert decision.verdict == "DENY"
    assert "critic_did_not_approve" in decision.reasons
    assert "unresolved_critical_conflicts" in decision.reasons


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"estimated_slippage_bps": Decimal("15.01")}, "estimated_slippage_too_high"),
        ({"observed_spread_bps": Decimal("20.01")}, "observed_spread_too_high"),
        (
            {"observed_liquidity_usd": Decimal("99999.99")},
            "observed_liquidity_below_minimum",
        ),
    ],
)
def test_market_quality_limits_are_deterministic(
    risk_config: RiskConfig,
    clock: FixedClock,
    now: datetime,
    updates: dict[str, object],
    reason: str,
) -> None:
    decision = evaluate(risk_config, clock, now, proposal_updates=updates)

    assert decision.verdict == "DENY"
    assert reason in decision.reasons


def test_profit_floor_counts_existing_and_candidate_risk(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = evaluate(
        risk_config,
        clock,
        now,
        context_updates={
            "realized_net_pnl_today": Decimal("100"),
            "intraday_peak_realized_pnl": Decimal("100"),
            "open_remaining_risk_usd": Decimal("55"),
        },
    )

    # Level 1 protects 40 % of the 100 made today; 100 - 55 open - 40 = 5 left.
    assert decision.protected_profit_floor_usd == Decimal("40.00")
    assert decision.allowed_risk_usd == Decimal("5.00")
    assert decision.verdict == "DENY"


def test_level_three_requires_a_plus(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = evaluate(
        risk_config,
        clock,
        now,
        context_updates={"realized_net_pnl_today": Decimal("300")},
        proposal_updates={"is_a_plus": False, "quantity": Decimal("0.1")},
    )

    assert decision.verdict == "DENY"
    assert "a_plus_setup_required" in decision.reasons


def test_level_four_requires_positive_net_expectancy(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = evaluate(
        risk_config,
        clock,
        now,
        context_updates={"realized_net_pnl_today": Decimal("400")},
        proposal_updates={"expected_net_value_usd": Decimal("0"), "quantity": Decimal("0.1")},
    )

    assert decision.verdict == "DENY"
    assert "expected_value_not_positive_after_costs" in decision.reasons


def test_dollar_caps_still_bind_when_configured(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    capped = risk_config.model_copy(update={"max_base_risk_usd": Decimal("10")})
    decision = evaluate(capped, clock, now)

    assert decision.allowed_risk_usd == Decimal("10")


def test_macro_window_blocks_supervised_and_is_context_when_autonomous(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    context = make_context(macro_block_reason="macro_event_pre_block")
    proposal, critic = make_proposal(now), make_critic(now)

    supervised = RiskEngine(risk_config, clock).evaluate_entry(proposal, critic, context)
    assert supervised.verdict == "DENY"
    assert "macro_event_pre_block" in supervised.reasons

    autonomous = RiskEngine(risk_config, clock, autonomous=True).evaluate_entry(
        proposal, critic, context
    )
    assert autonomous.verdict == "ALLOW"
    assert autonomous.notes == ("macro_event_pre_block",)


def test_missing_macro_calendar_blocks_even_when_autonomous(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = RiskEngine(risk_config, clock, autonomous=True).evaluate_entry(
        make_proposal(now),
        make_critic(now),
        make_context(macro_block_reason="macro_calendar_unavailable"),
    )

    assert decision.verdict == "DENY"
    assert "macro_calendar_unavailable" in decision.reasons


def test_critic_revise_is_advisory_only_when_autonomous(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    proposal, context = make_proposal(now), make_context()
    critic = make_critic(now, verdict="REVISE")

    assert RiskEngine(risk_config, clock).evaluate_entry(proposal, critic, context).verdict == (
        "DENY"
    )
    autonomous = RiskEngine(risk_config, clock, autonomous=True).evaluate_entry(
        proposal, critic, context
    )
    assert autonomous.verdict == "ALLOW"
    assert "critic_revise_advisory" in autonomous.notes
    rejected = RiskEngine(risk_config, clock, autonomous=True).evaluate_entry(
        proposal, make_critic(now, verdict="REJECT"), context
    )
    assert "critic_did_not_approve" in rejected.reasons


def test_autonomy_never_relaxes_live(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = RiskEngine(risk_config, clock, autonomous=True).evaluate_entry(
        make_proposal(now),
        make_critic(now, verdict="REVISE"),
        make_context(mode=TradingMode.LIVE, macro_block_reason="macro_event_cooldown"),
    )

    assert decision.verdict == "DENY"
    assert "macro_event_cooldown" in decision.reasons
    assert "critic_did_not_approve" in decision.reasons


def test_profit_giveback_stops_new_entries_in_paper(
    risk_config: RiskConfig, clock: FixedClock, now: datetime
) -> None:
    decision = evaluate(
        risk_config,
        clock,
        now,
        context_updates={
            "realized_net_pnl_today": Decimal("250"),
            "intraday_peak_realized_pnl": Decimal("400"),
        },
    )

    assert decision.verdict == "DENY"
    assert "max_profit_giveback_realized_reached" in decision.reasons


def test_one_trade_on_a_100k_account_risks_half_a_percent(
    risk_config: RiskConfig, clock: FixedClock
) -> None:
    context = make_context(equity=Decimal("100000"), account_high_water_mark=Decimal("100000"))
    assert RiskEngine(risk_config, clock).risk_budget(context) == Decimal("500")
