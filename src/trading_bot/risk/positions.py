from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from pydantic import Field

from trading_bot.core.clock import Clock
from trading_bot.schemas.common import Side, StrictSchema
from trading_bot.schemas.trading import PositionDecision


class PositionState(StrictSchema):
    position_id: str
    asset: str
    side: Side
    entry_price: Decimal = Field(gt=0)
    current_price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    opened_at: datetime
    protective_stop_active: bool
    # Per-position horizon from the proposal; the tighter of this and the global cap applies.
    max_hold_seconds: int | None = Field(default=None, gt=0)


class DeterministicPositionManager:
    """Safety actions remain available even when every AI provider is offline."""

    def __init__(self, clock: Clock, *, max_hold: timedelta | None = None) -> None:
        self._clock = clock
        self._max_hold = max_hold

    def evaluate(self, position: PositionState, *, data_fresh: bool) -> PositionDecision:
        if not position.protective_stop_active:
            return self._decision(position, "EMERGENCY_EXIT", "protective_stop_missing")
        if not data_fresh:
            return self._decision(position, "HOLD", "stale_data_keep_exchange_stop")
        if position.side == Side.BUY:
            if position.current_price <= position.stop_price:
                return self._decision(position, "EMERGENCY_EXIT", "protective_stop_reached")
            if position.current_price >= position.target_price:
                return self._decision(position, "TAKE_PROFIT", "target_reached")
        else:
            if position.current_price >= position.stop_price:
                return self._decision(position, "EMERGENCY_EXIT", "protective_stop_reached")
            if position.current_price <= position.target_price:
                return self._decision(position, "TAKE_PROFIT", "target_reached")
        limits = [self._max_hold] if self._max_hold is not None else []
        if position.max_hold_seconds is not None:
            limits.append(timedelta(seconds=position.max_hold_seconds))
        if limits and self._clock.now() - position.opened_at >= min(limits):
            return self._decision(position, "TIME_EXIT", "max_hold_time_exceeded")
        return self._decision(position, "HOLD", "thesis_still_within_protective_bounds")

    def _decision(self, position: PositionState, action: str, reason: str) -> PositionDecision:
        return PositionDecision.model_validate(
            {
                "position_id": position.position_id,
                "action": action,
                "reasons": [reason],
                "created_at": self._clock.now(),
            }
        )
