from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_context

from trading_bot.config.models import RiskConfig
from trading_bot.risk.profit_ladder import ProfitProtectionLadder


@pytest.mark.parametrize(
    ("pnl", "expected_level", "expected_multiplier"),
    [
        # Bands are percent of equity (10 000 in make_context): 1 % = 100 USD.
        ("99.99", 0, "1.00"),
        ("100", 1, "0.75"),
        ("199.99", 1, "0.75"),
        ("200", 2, "0.50"),
        ("299.99", 2, "0.50"),
        ("300", 3, "0.35"),
        ("399.99", 3, "0.35"),
        ("400", 4, "0.25"),
        ("5000", 4, "0.25"),
    ],
)
def test_exact_profit_ladder_boundaries(
    risk_config: RiskConfig, pnl: str, expected_level: int, expected_multiplier: str
) -> None:
    state = ProfitProtectionLadder(risk_config).evaluate(
        make_context(realized_net_pnl_today=Decimal(pnl))
    )

    assert state.level == expected_level
    assert state.risk_multiplier == Decimal(expected_multiplier)


def test_optional_profit_cap_stops_the_day_only_when_configured(risk_config: RiskConfig) -> None:
    uncapped = ProfitProtectionLadder(risk_config).evaluate(
        make_context(realized_net_pnl_today=Decimal("5000"))
    )
    assert uncapped.stop_live_for_day is False

    capped = risk_config.model_copy(update={"daily_profit_hard_cap_usd": Decimal("50")})
    state = ProfitProtectionLadder(capped).evaluate(
        make_context(realized_net_pnl_today=Decimal("50"))
    )
    assert state.level == 5
    assert state.stop_live_for_day is True
    assert state.reason == "daily_profit_hard_cap_reached"


def test_realized_giveback_at_threshold_stops_live(risk_config: RiskConfig) -> None:
    state = ProfitProtectionLadder(risk_config).evaluate(
        make_context(
            realized_net_pnl_today=Decimal("260"),
            intraday_peak_realized_pnl=Decimal("400"),
        )
    )

    assert state.stop_live_for_day is True
    assert state.reason == "max_profit_giveback_realized_reached"


def test_giveback_below_threshold_does_not_stop(risk_config: RiskConfig) -> None:
    state = ProfitProtectionLadder(risk_config).evaluate(
        make_context(
            realized_net_pnl_today=Decimal("260.01"),
            intraday_peak_realized_pnl=Decimal("400"),
        )
    )

    assert state.stop_live_for_day is False
