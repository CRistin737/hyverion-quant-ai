from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from trading_bot.schemas.common import Side, StrictSchema, TradingMode


class Fill(StrictSchema):
    fill_id: str
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    fee_usd: Decimal = Field(ge=0)
    filled_at: datetime


class OrderResult(StrictSchema):
    order_id: str
    client_order_id: str
    mode: TradingMode
    asset: str
    side: Side
    requested_quantity: Decimal = Field(gt=0)
    filled_quantity: Decimal = Field(ge=0)
    status: Literal["OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "UNKNOWN"]
    fills: tuple[Fill, ...] = ()
    protective_stop_active: bool
    created_at: datetime
