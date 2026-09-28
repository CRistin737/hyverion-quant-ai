from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from trading_bot.schemas.common import Evidence, Score, Side, StrictSchema, TradingMode


class MarketSnapshot(StrictSchema):
    symbol: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    last: Decimal = Field(gt=0)
    # Regular-session shares traded so far and their dollar value (not a rolling 24h).
    session_volume: Decimal = Field(ge=0)
    session_dollar_volume: Decimal = Field(ge=0)
    event_time: datetime
    received_time: datetime
    processed_time: datetime
    # Lineage: who produced the quote and whether it is delayed or a partial feed.
    provider: str = "unknown"
    feed: str | None = None
    is_delayed: bool = False

    @field_validator("event_time", "received_time", "processed_time")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @property
    def spread_bps(self) -> Decimal:
        midpoint = (self.bid + self.ask) / Decimal("2")
        return ((self.ask - self.bid) / midpoint) * Decimal("10000")

    def age_seconds(self, now: datetime) -> Decimal:
        return Decimal(str((now - self.event_time).total_seconds()))

    @model_validator(mode="after")
    def validate_market(self) -> MarketSnapshot:
        if self.ask < self.bid:
            raise ValueError("ask cannot be lower than bid")
        if not (self.event_time <= self.received_time <= self.processed_time):
            raise ValueError("market timestamps must be ordered")
        return self


class Candle(StrictSchema):
    symbol: str
    interval: str
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    # Volume-weighted average price of the bar when the provider reports it.
    vwap: Decimal | None = Field(default=None, gt=0)
    trades: int = Field(ge=0)
    event_time: datetime
    received_time: datetime
    processed_time: datetime

    @field_validator("event_time", "received_time", "processed_time")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candle timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_prices(self) -> Candle:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC values are inconsistent")
        if self.low > self.high:
            raise ValueError("candle low cannot exceed high")
        return self


class SignalComponents(StrictSchema):
    technical: Score
    regime: Score
    liquidity: Score
    risk_reward: Score
    news: Score
    sentiment: Score
    historical_expectancy: Score
    data_freshness: Score
    agent_agreement: Score
    weights_version: str


class TradeProposal(StrictSchema):
    proposal_id: str
    asset: str
    side: Side
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    expected_r: Decimal = Field(gt=0)
    time_horizon_seconds: int = Field(gt=0)
    signal_score: Score
    signal_components: SignalComponents
    confirmation_categories: frozenset[str]
    evidence: tuple[Evidence, ...]
    contradictory_evidence: tuple[Evidence, ...] = ()
    invalidations: tuple[str, ...]
    expected_fees_usd: Decimal = Field(ge=0)
    estimated_slippage_usd: Decimal = Field(ge=0)
    estimated_slippage_bps: Decimal = Field(ge=0)
    observed_spread_bps: Decimal = Field(ge=0)
    observed_liquidity_usd: Decimal = Field(ge=0)
    expected_net_value_usd: Decimal
    why_now: str
    why_not_trade: tuple[str, ...] = ()
    is_a_plus: bool = False
    created_at: datetime

    @model_validator(mode="after")
    def validate_protection(self) -> TradeProposal:
        if self.side == Side.BUY and not (self.stop_price < self.entry_price < self.target_price):
            raise ValueError("buy proposal requires stop < entry < target")
        if self.side == Side.SELL and not (self.target_price < self.entry_price < self.stop_price):
            raise ValueError("sell proposal requires target < entry < stop")
        return self

    @property
    def notional_usd(self) -> Decimal:
        return self.entry_price * self.quantity

    @property
    def stop_loss_usd(self) -> Decimal:
        return abs(self.entry_price - self.stop_price) * self.quantity

    @property
    def worst_case_loss_usd(self) -> Decimal:
        return self.stop_loss_usd + self.expected_fees_usd + self.estimated_slippage_usd


class StrategyOutput(StrictSchema):
    """Typed strategy-agent envelope that makes NO_TRADE explicit."""

    decision: Literal["TRADE", "NO_TRADE"]
    proposal: TradeProposal | None = None
    why_not_trade: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_decision(self) -> StrategyOutput:
        if self.decision == "TRADE" and self.proposal is None:
            raise ValueError("TRADE output requires a proposal")
        if self.decision == "NO_TRADE" and self.proposal is not None:
            raise ValueError("NO_TRADE output cannot include a proposal")
        if self.decision == "NO_TRADE" and not self.why_not_trade:
            raise ValueError("NO_TRADE output requires a reason")
        return self


class RiskContext(StrictSchema):
    mode: TradingMode
    equity: Decimal = Field(gt=0)
    account_high_water_mark: Decimal = Field(gt=0)
    realized_net_pnl_today: Decimal
    unrealized_pnl: Decimal
    intraday_peak_realized_pnl: Decimal = Field(ge=0)
    intraday_peak_total_pnl: Decimal = Field(ge=0)
    fees_today: Decimal = Field(ge=0)
    realized_net_pnl_week: Decimal
    current_exposure_usd: Decimal = Field(ge=0)
    asset_exposure_usd: Decimal = Field(ge=0)
    correlated_open_risk_usd: Decimal = Field(ge=0)
    open_remaining_risk_usd: Decimal = Field(ge=0)
    open_positions: int = Field(ge=0)
    losing_streak: int = Field(ge=0)
    live_stopped: bool = False
    session_stop_reason: str | None = None
    cooldown_active: bool = False
    reconciliation_ok: bool = True
    # External broker: equity was synced from the broker account recently.
    broker_equity_fresh: bool = True
    # Operations in UNKNOWN, SAFE_MODE or RECOVERY_REQUIRED. Any value blocks entries.
    unresolved_operations: int = Field(default=0, ge=0)
    data_fresh: bool = True
    # Last observed market price. When present, entries priced away from it are denied.
    market_price: Decimal | None = Field(default=None, gt=0)
    # US equity session (market/clock.py). UNKNOWN fails closed for new entries.
    market_session: str = "UNKNOWN"
    minutes_since_open: int | None = None
    minutes_to_close: int | None = None
    # New entries already opened this New York trading day (exits never count).
    entries_today: int = Field(default=0, ge=0)
    # MacroRiskGate verdict for this moment (None = clear). Deterministic.
    macro_block_reason: str | None = None

    @property
    def current_total_pnl(self) -> Decimal:
        return self.realized_net_pnl_today + self.unrealized_pnl


class RiskDecision(StrictSchema):
    decision_id: str
    proposal_id: str
    verdict: Literal["ALLOW", "DENY", "EXIT_ONLY"]
    reasons: tuple[str, ...]
    # Context the decision recorded without blocking (paper_autonomous mode):
    # e.g. a macro window or a critic REVISE. Kept for learning and audit.
    notes: tuple[str, ...] = ()
    level: int = Field(ge=0, le=5)
    risk_multiplier: Decimal = Field(ge=0, le=1)
    base_risk_usd: Decimal = Field(ge=0)
    allowed_risk_usd: Decimal = Field(ge=0)
    candidate_worst_case_loss_usd: Decimal = Field(ge=0)
    protected_profit_floor_usd: Decimal = Field(ge=0)
    live_trading_allowed: bool
    shadow_trading: bool
    decided_at: datetime
    # What an ALLOW decision actually authorizes. ExecutionEngine refuses any
    # entry intent that exceeds or differs from it, so a bug or tampering between
    # RiskEngine and the venue cannot enlarge an approved trade.
    approved_asset: str | None = None
    approved_side: Side | None = None
    approved_quantity: Decimal | None = Field(default=None, gt=0)
    approved_notional_usd: Decimal | None = Field(default=None, gt=0)
    # The protective stop RiskEngine sized the risk with; execution may place
    # it closer to the entry, never further away.
    approved_stop_price: Decimal | None = Field(default=None, gt=0)


class ExecutionIntent(StrictSchema):
    intent_id: str
    decision_id: str
    proposal_id: str
    client_order_id: str
    mode: TradingMode
    asset: str
    side: Side
    quantity: Decimal = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    created_at: datetime
    reduce_only: bool = False
    position_id: str | None = None
    exit_reason: str | None = None
    # Entry only: the proposal's own time horizon, enforced as a per-position time stop.
    max_hold_seconds: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_exit_intent(self) -> ExecutionIntent:
        if self.reduce_only and not self.position_id:
            raise ValueError("reduce-only intents require a position_id")
        if not self.reduce_only and self.position_id is not None:
            raise ValueError("entry intents cannot reference a position_id")
        return self


class PositionDecision(StrictSchema):
    position_id: str
    action: Literal[
        "HOLD",
        "MOVE_STOP",
        "PARTIAL_CLOSE",
        "FULL_CLOSE",
        "TAKE_PROFIT",
        "TRAIL",
        "EMERGENCY_EXIT",
        "TIME_EXIT",
    ]
    proposed_stop: Decimal | None = None
    proposed_quantity: Decimal | None = None
    reasons: tuple[str, ...]
    created_at: datetime


class SessionDecision(StrictSchema):
    action: Literal["CONTINUE", "REDUCE_RISK", "PAUSE", "STOP_LIVE_FOR_DAY"]
    reasons: tuple[str, ...]
    live_trading_allowed: bool
    shadow_trading: bool
    created_at: datetime


class OperationResolutionRequest(StrictSchema):
    """Operator confirmation of what the venue shows for an unresolved operation."""

    target: Literal["REJECTED", "CANCELED", "OPEN", "CLOSED"]
    reason: str = Field(min_length=3, max_length=200)
