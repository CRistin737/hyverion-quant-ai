from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from trading_bot.schemas.common import Side, StrictSchema
from trading_bot.schemas.trading import TradeProposal
from trading_bot.simulation.costs import (
    DEFAULT_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    CostModel,
    bps_cost,
)


class ShadowComparison(StrictSchema):
    proposal_id: str
    real_trade_pnl_usd: Decimal | None
    shadow_trade_pnl_usd: Decimal
    incremental_value_usd: Decimal | None
    reason_real_not_taken: str | None

    @classmethod
    def compare(
        cls,
        proposal_id: str,
        shadow_trade_pnl_usd: Decimal,
        real_trade_pnl_usd: Decimal | None,
        reason_real_not_taken: str | None = None,
    ) -> ShadowComparison:
        incremental = None
        if real_trade_pnl_usd is not None:
            incremental = real_trade_pnl_usd - shadow_trade_pnl_usd
        return cls(
            proposal_id=proposal_id,
            real_trade_pnl_usd=real_trade_pnl_usd,
            shadow_trade_pnl_usd=shadow_trade_pnl_usd,
            incremental_value_usd=incremental,
            reason_real_not_taken=reason_real_not_taken,
        )


class ShadowTradeResult(StrictSchema):
    proposal_id: str
    outcome: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    gross_pnl_usd: Decimal
    fees_usd: Decimal
    slippage_usd: Decimal
    net_pnl_usd: Decimal
    # Best and worst gross excursion observed along the replayed path, in USD.
    mfe_usd: Decimal = Field(ge=0)
    mae_usd: Decimal = Field(le=0)
    observations: int
    exit_reason: str


class ShadowSimulator:
    """Deterministically replay a proposal against later prices."""

    def simulate(
        self,
        proposal: TradeProposal,
        prices: tuple[Decimal, ...],
        *,
        fee_bps: Decimal = DEFAULT_FEE_BPS,
        slippage_bps: Decimal = DEFAULT_SLIPPAGE_BPS,
        costs: CostModel | None = None,
    ) -> ShadowTradeResult:
        """Replay against later prices; ``costs`` (a scenario) overrides the bps defaults."""

        if costs is not None:
            slippage_bps = costs.slippage_bps + costs.min_half_spread_bps
        if not prices or any(price <= 0 for price in prices):
            raise ValueError("shadow replay requires positive prices")
        if fee_bps < 0 or slippage_bps < 0:
            raise ValueError("cost assumptions cannot be negative")

        multiplier = bps_cost(Decimal("1"), slippage_bps)
        if proposal.side == Side.BUY:
            entry = proposal.entry_price * (Decimal("1") + multiplier)
            stop = proposal.stop_price * (Decimal("1") - multiplier)
            target = proposal.target_price * (Decimal("1") - multiplier)
        else:
            entry = proposal.entry_price * (Decimal("1") - multiplier)
            stop = proposal.stop_price * (Decimal("1") + multiplier)
            target = proposal.target_price * (Decimal("1") + multiplier)

        exit_price = prices[-1]
        reason = "horizon_expired"
        observations = len(prices)
        for index, price in enumerate(prices, start=1):
            if proposal.side == Side.BUY and price <= proposal.stop_price:
                exit_price = stop
                reason = "stop"
                observations = index
                break
            if proposal.side == Side.BUY and price >= proposal.target_price:
                exit_price = target
                reason = "target"
                observations = index
                break
            if proposal.side == Side.SELL and price >= proposal.stop_price:
                exit_price = stop
                reason = "stop"
                observations = index
                break
            if proposal.side == Side.SELL and price <= proposal.target_price:
                exit_price = target
                reason = "target"
                observations = index
                break

        direction = Decimal("1") if proposal.side == Side.BUY else Decimal("-1")
        gross = (exit_price - entry) * proposal.quantity * direction
        # Marks held before the exit tick, then the exit fill itself: the tick that
        # triggered a stop/target is replaced by the protective fill price.
        held = prices[: observations - 1] if reason in {"stop", "target"} else prices
        excursions = [(price - entry) * proposal.quantity * direction for price in held]
        excursions.append(gross)
        entry_notional = entry * proposal.quantity
        exit_notional = exit_price * proposal.quantity
        if costs is None:
            fees = bps_cost(entry_notional + exit_notional, fee_bps)
        elif proposal.side == Side.BUY:
            fees = costs.fees(buy_notional=entry_notional, sell_notional=exit_notional)
        else:
            fees = costs.fees(buy_notional=exit_notional, sell_notional=entry_notional)
        slippage = abs(entry - proposal.entry_price) * proposal.quantity + abs(
            exit_price - (proposal.stop_price if reason == "stop" else proposal.target_price)
            if reason in {"stop", "target"}
            else exit_price - prices[-1]
        ) * proposal.quantity
        return ShadowTradeResult(
            proposal_id=proposal.proposal_id,
            outcome="CLOSED",
            entry_price=entry,
            exit_price=exit_price,
            quantity=proposal.quantity,
            gross_pnl_usd=gross,
            fees_usd=fees,
            slippage_usd=slippage,
            net_pnl_usd=gross - fees,
            mfe_usd=max(max(excursions), Decimal("0")),
            mae_usd=min(min(excursions), Decimal("0")),
            observations=observations,
            exit_reason=reason,
        )
