from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import make_proposal

from trading_bot.simulation.backtest import BacktestEngine
from trading_bot.simulation.shadow import ShadowSimulator


def test_walk_forward_keeps_test_windows_disjoint_and_costed() -> None:
    prices = tuple(Decimal(str(value)) for value in range(100, 116))
    result = BacktestEngine().run_walk_forward(
        prices,
        capital=Decimal("100"),
        train_size=4,
        validation_size=3,
        test_size=3,
        step=3,
    )

    assert len(result.windows) == 3
    assert result.windows[0].train_end == result.windows[0].validation_start
    assert result.windows[0].validation_end == result.windows[0].test_start
    assert result.windows[0].test_end == result.windows[1].test_start
    assert result.aggregate.fees_usd > 0
    assert "OOS" in " ".join(result.assumptions)


def test_walk_forward_does_not_use_future_test_observations() -> None:
    engine = BacktestEngine()
    prefix = tuple(Decimal(str(value)) for value in range(100, 116))
    changed_future = (*prefix[:-1], Decimal("1000"))
    first = engine.run_walk_forward(
        prefix,
        capital=Decimal("100"),
        train_size=4,
        validation_size=3,
        test_size=3,
        step=3,
    )
    second = engine.run_walk_forward(
        changed_future,
        capital=Decimal("100"),
        train_size=4,
        validation_size=3,
        test_size=3,
        step=3,
    )
    assert first.windows[0].test_result == second.windows[0].test_result
    assert all(
        window.train_end <= window.validation_start <= window.validation_end <= window.test_start
        for window in first.windows
    )


def test_backtest_rejects_invalid_prices_and_keeps_fees_nonzero() -> None:
    engine = BacktestEngine()
    with pytest.raises(ValueError):
        engine.run_buy_and_hold_baseline((Decimal("100"), Decimal("0")), capital=Decimal("100"))
    result = engine.run_buy_and_hold_baseline(
        (Decimal("100"), Decimal("100")),
        capital=Decimal("100"),
        fee_bps=Decimal("10"),
        slippage_bps=Decimal("5"),
    )
    assert result.net_pnl_usd < 0
    assert result.fees_usd > 0
    assert result.slippage_usd > 0


def test_shadow_simulator_closes_at_protective_target_with_costs(now) -> None:
    proposal = make_proposal(now)
    result = ShadowSimulator().simulate(
        proposal,
        (Decimal("100"), Decimal("101"), Decimal("102"), Decimal("101")),
    )

    assert result.exit_reason == "target"
    assert result.observations == 3
    assert result.fees_usd > 0
    assert result.net_pnl_usd < result.gross_pnl_usd


def test_shadow_simulator_closes_at_stop(now) -> None:
    result = ShadowSimulator().simulate(
        make_proposal(now),
        (Decimal("100"), Decimal("99"), Decimal("98")),
    )

    assert result.exit_reason == "stop"
    assert result.net_pnl_usd < 0


def test_shadow_excursions_follow_the_price_path_not_the_final_pnl(now) -> None:
    # Price rallies to 101.5 before collapsing through the stop: the trade was
    # favorable first, so MFE must be positive even though the result is a loss.
    result = ShadowSimulator().simulate(
        make_proposal(now),
        tuple(Decimal(value) for value in ("100.5", "101.5", "100", "98.5")),
        fee_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
    )

    assert result.exit_reason == "stop"
    assert result.net_pnl_usd < 0
    assert result.mfe_usd == Decimal("7.5")  # (101.5 - 100) * 5
    assert result.mae_usd == Decimal("-5")  # stop fill at 99: (99 - 100) * 5
    assert result.observations == 4
