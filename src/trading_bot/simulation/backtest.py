from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from pydantic import Field

from trading_bot.schemas.common import StrictSchema
from trading_bot.simulation.costs import DEFAULT_FEE_BPS, DEFAULT_SLIPPAGE_BPS, bps_cost


class BacktestResult(StrictSchema):
    trades: int = Field(ge=0)
    net_pnl_usd: Decimal
    gross_pnl_usd: Decimal
    fees_usd: Decimal = Field(ge=0)
    slippage_usd: Decimal = Field(ge=0)
    max_drawdown_usd: Decimal = Field(ge=0)
    assumptions: tuple[str, ...]


class WalkForwardWindow(StrictSchema):
    window_id: str
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int
    test_result: BacktestResult


class WalkForwardResult(StrictSchema):
    experiment_id: str
    windows: tuple[WalkForwardWindow, ...]
    aggregate: BacktestResult
    assumptions: tuple[str, ...]


class BacktestEngine:
    """Small deterministic replay baseline with explicit execution costs."""

    def run_buy_and_hold_baseline(
        self,
        prices: tuple[Decimal, ...],
        *,
        capital: Decimal,
        fee_bps: Decimal = DEFAULT_FEE_BPS,
        slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS,
    ) -> BacktestResult:
        if len(prices) < 2 or capital <= 0 or any(price <= 0 for price in prices):
            raise ValueError("valid prices and positive capital are required")
        entry = prices[0] * (Decimal("1") + bps_cost(Decimal("1"), slippage_bps))
        exit_price = prices[-1] * (Decimal("1") - bps_cost(Decimal("1"), slippage_bps))
        quantity = capital / entry
        entry_fee = bps_cost(capital, fee_bps)
        exit_notional = quantity * exit_price
        exit_fee = bps_cost(exit_notional, fee_bps)
        gross = exit_notional - capital
        fees = entry_fee + exit_fee
        slippage = quantity * bps_cost(prices[0], slippage_bps) + quantity * bps_cost(
            prices[-1], slippage_bps
        )
        equity_curve = [quantity * price - capital for price in prices]
        peak = equity_curve[0]
        drawdown = Decimal("0")
        for value in equity_curve:
            peak = max(peak, value)
            drawdown = max(drawdown, peak - value)
        return BacktestResult(
            trades=1,
            net_pnl_usd=gross - fees,
            gross_pnl_usd=gross,
            fees_usd=fees,
            slippage_usd=slippage,
            max_drawdown_usd=drawdown,
            assumptions=(
                "Deterministic baseline, not evidence of profitability.",
                "No look-ahead; entry is first observation and exit is last observation.",
                "Fees and symmetric adverse slippage are included.",
            ),
        )

    def run_walk_forward(
        self,
        prices: tuple[Decimal, ...],
        *,
        capital: Decimal,
        train_size: int,
        validation_size: int,
        test_size: int,
        step: int | None = None,
        fee_bps: Decimal = DEFAULT_FEE_BPS,
        slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS,
    ) -> WalkForwardResult:
        """Replay disjoint test windows after train/validation periods.

        This baseline does not tune parameters.  It deliberately keeps the
        train/validation slices visible in the result so a future strategy
        optimizer cannot accidentally report an in-sample score as OOS evidence.
        """

        if any(size < 2 for size in (train_size, validation_size, test_size)):
            raise ValueError("walk-forward windows must each contain at least two prices")
        if step is None:
            step = test_size
        if step < 1:
            raise ValueError("walk-forward step must be positive")
        minimum = train_size + validation_size + test_size
        if len(prices) < minimum:
            raise ValueError("prices do not contain one complete walk-forward window")

        windows: list[WalkForwardWindow] = []
        start = 0
        while start + minimum <= len(prices):
            train_end = start + train_size
            validation_end = train_end + validation_size
            test_end = validation_end + test_size
            test_prices = prices[validation_end:test_end]
            windows.append(
                WalkForwardWindow(
                    window_id=str(uuid4()),
                    train_start=start,
                    train_end=train_end,
                    validation_start=train_end,
                    validation_end=validation_end,
                    test_start=validation_end,
                    test_end=test_end,
                    test_result=self.run_buy_and_hold_baseline(
                        test_prices,
                        capital=capital,
                        fee_bps=fee_bps,
                        slippage_bps=slippage_bps,
                    ),
                )
            )
            start += step

        aggregate_results = [window.test_result for window in windows]
        aggregate = BacktestResult(
            trades=sum(result.trades for result in aggregate_results),
            net_pnl_usd=sum((result.net_pnl_usd for result in aggregate_results), Decimal("0")),
            gross_pnl_usd=sum((result.gross_pnl_usd for result in aggregate_results), Decimal("0")),
            fees_usd=sum((result.fees_usd for result in aggregate_results), Decimal("0")),
            slippage_usd=sum(
                (result.slippage_usd for result in aggregate_results), Decimal("0")
            ),
            max_drawdown_usd=max(
                (result.max_drawdown_usd for result in aggregate_results), default=Decimal("0")
            ),
            assumptions=("Aggregate of disjoint OOS test windows.",),
        )
        return WalkForwardResult(
            experiment_id=str(uuid4()),
            windows=tuple(windows),
            aggregate=aggregate,
            assumptions=(
                "Only out-of-sample (OOS) test windows contribute to the aggregate.",
                "Train, validation and test windows are non-overlapping within each replay.",
                "No future test observation is used to construct an earlier test result.",
                "Fees, spread proxy and adverse slippage are included in every test window.",
            ),
        )
