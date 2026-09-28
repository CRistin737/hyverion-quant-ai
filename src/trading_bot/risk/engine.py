from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from trading_bot.config.models import RiskConfig
from trading_bot.core.clock import Clock
from trading_bot.core.instruments import SYMBOL_NOT_EXECUTION_WHITELISTED, is_executable
from trading_bot.risk.profit_ladder import ProfitProtectionLadder
from trading_bot.schemas.assessments import CriticAssessment
from trading_bot.schemas.common import TradingMode
from trading_bot.schemas.trading import PositionDecision, RiskContext, RiskDecision, TradeProposal

ONE_HUNDRED = Decimal("100")
# Mirrors market.clock.REGULAR_STATES without importing the calendar into RiskEngine.
REGULAR_SESSION_STATES = frozenset({"OPENING", "REGULAR", "MIDDAY", "POWER_HOUR", "CLOSING"})
SESSION_DENY_REASONS = {
    "CLOSED": "market_closed",
    "PRE_MARKET": "premarket_trading_disabled",
    "AFTER_HOURS": "after_hours_trading_disabled",
}
COMPARISON_TOLERANCE = Decimal("0.00000001")
# In paper_autonomous mode these are context for the AI, not blocks. A stale or
# missing calendar (``macro_calendar_unavailable``) always blocks.
MACRO_WINDOW_REASONS = frozenset({"macro_event_pre_block", "macro_event_cooldown"})


def _capped(value: Decimal, cap: Decimal | None) -> Decimal:
    return value if cap is None else min(value, cap)


class RiskEngine:
    """Pure deterministic entry gate. It has no network or provider dependencies."""

    def __init__(self, config: RiskConfig, clock: Clock, *, autonomous: bool = False) -> None:
        """``autonomous`` is paper_autonomous mode (PAPER only; ignored for LIVE)."""

        self._config = config
        self._clock = clock
        self._autonomous = autonomous
        self._ladder = ProfitProtectionLadder(config)

    def _base_risk(self, context: RiskContext) -> Decimal:
        return _capped(
            context.equity * self._config.base_risk_percent / ONE_HUNDRED,
            self._config.max_base_risk_usd,
        )

    def notional_capacity(self, context: RiskContext) -> Decimal:
        """Largest new notional the exposure limits allow (sizing uses the same number)."""

        total = context.equity * self._config.max_total_exposure_percent / ONE_HUNDRED
        asset = context.equity * self._config.max_asset_exposure_percent / ONE_HUNDRED
        return max(
            Decimal("0"),
            min(total - context.current_exposure_usd, asset - context.asset_exposure_usd),
        )

    def risk_budget(self, context: RiskContext) -> Decimal:
        """Base risk per trade after the profit ladder (what sizing may use)."""

        ladder = self._ladder.evaluate(context)
        return self._base_risk(context) * ladder.risk_multiplier

    def evaluate_entry(
        self,
        proposal: TradeProposal,
        critic: CriticAssessment,
        context: RiskContext,
    ) -> RiskDecision:
        ladder = self._ladder.evaluate(context)
        autonomous = self._autonomous and context.mode != TradingMode.LIVE
        base_risk = self._base_risk(context)
        multiplied_risk = base_risk * ladder.risk_multiplier
        daily_limit = _capped(
            context.equity * self._config.daily_loss_percent / ONE_HUNDRED,
            self._config.daily_loss_hard_cap_usd,
        )
        weekly_limit = _capped(
            context.equity * self._config.weekly_loss_percent / ONE_HUNDRED,
            self._config.weekly_loss_hard_cap_usd,
        )
        # Open positions count against the loss limits: a floating loss is already
        # lost for this purpose, and every open stop could still be hit.
        open_exposure = min(Decimal("0"), context.unrealized_pnl) - context.open_remaining_risk_usd
        daily_remaining = max(
            Decimal("0"), daily_limit + context.realized_net_pnl_today + open_exposure
        )
        weekly_remaining = max(
            Decimal("0"), weekly_limit + context.realized_net_pnl_week + open_exposure
        )
        profit_capacity = max(
            Decimal("0"),
            context.current_total_pnl
            - context.open_remaining_risk_usd
            - ladder.protected_profit_floor_usd,
        )
        exposure_capacity = max(
            Decimal("0"),
            context.equity * self._config.max_total_exposure_percent / ONE_HUNDRED
            - context.current_exposure_usd,
        )
        asset_capacity = max(
            Decimal("0"),
            context.equity * self._config.max_asset_exposure_percent / ONE_HUNDRED
            - context.asset_exposure_usd,
        )
        correlated_capacity = max(
            Decimal("0"),
            context.equity * self._config.max_correlated_risk_percent / ONE_HUNDRED
            - context.correlated_open_risk_usd,
        )
        capacities = [
            multiplied_risk,
            daily_remaining,
            weekly_remaining,
            exposure_capacity,
            asset_capacity,
            correlated_capacity,
        ]
        if ladder.protected_profit_floor_usd > 0:
            capacities.append(profit_capacity)
        allowed_risk = min(capacities)

        reasons: list[str] = []
        if not is_executable(proposal.asset):
            reasons.append(SYMBOL_NOT_EXECUTION_WHITELISTED)
        reasons.extend(self._session_reasons(context))
        if context.mode == TradingMode.LIVE and context.live_stopped:
            reasons.append(context.session_stop_reason or "live_session_stopped")
        if ladder.stop_live_for_day:
            # Giveback / profit cap stop new entries for the day in every mode.
            reasons.append(ladder.reason or "stopped_by_profit_protection")
        if not context.reconciliation_ok:
            # Any external venue (Alpaca Paper included) must match local state.
            reasons.append("exchange_reconciliation_failed")
        notes: list[str] = []
        if context.macro_block_reason:
            # MacroRiskGate: scheduled high-impact event or its cooldown (§22).
            if autonomous and context.macro_block_reason in MACRO_WINDOW_REASONS:
                notes.append(context.macro_block_reason)
            else:
                reasons.append(context.macro_block_reason)
        if not context.broker_equity_fresh:
            # Sizing needs the broker's real equity; never size on a guess.
            reasons.append("broker_equity_unavailable")
        if context.unresolved_operations > 0:
            reasons.append("unresolved_operation_requires_recovery")
        if not context.data_fresh:
            reasons.append("stale_market_data")
        if context.open_positions >= self._config.max_positions:
            reasons.append("maximum_positions_reached")
        if context.losing_streak >= self._config.max_losing_streak:
            reasons.append("maximum_losing_streak_reached")
        if context.cooldown_active:
            reasons.append("loss_cooldown_active")
        # Drawdown on marked equity: an open loss counts before it is realized.
        marked_equity = context.equity + min(Decimal("0"), context.unrealized_pnl)
        drawdown_percent = (
            (context.account_high_water_mark - marked_equity)
            / context.account_high_water_mark
            * ONE_HUNDRED
        )
        if drawdown_percent >= self._config.max_account_drawdown_percent:
            reasons.append("account_high_water_mark_drawdown_reached")
        if proposal.signal_score < ladder.minimum_score:
            reasons.append("signal_score_below_ladder_minimum")
        if len(proposal.confirmation_categories) < ladder.minimum_confirmations:
            reasons.append("insufficient_independent_confirmations")
        if critic.verdict == "REJECT" or (critic.verdict == "REVISE" and not autonomous):
            reasons.append("critic_did_not_approve")
        elif critic.verdict == "REVISE":
            notes.append("critic_revise_advisory")
        if critic.critical_conflicts:
            reasons.append("unresolved_critical_conflicts")
        if ladder.level >= 3 and not proposal.is_a_plus:
            reasons.append("a_plus_setup_required")
        if ladder.level >= 4 and proposal.expected_net_value_usd <= 0:
            reasons.append("expected_value_not_positive_after_costs")
        if context.market_price is not None:
            deviation_bps = (
                abs(proposal.entry_price - context.market_price)
                / context.market_price
                * Decimal("10000")
            )
            if deviation_bps > self._config.max_entry_deviation_bps:
                reasons.append("entry_price_deviates_from_market")
        if proposal.estimated_slippage_bps > self._config.max_slippage_bps:
            reasons.append("estimated_slippage_too_high")
        if proposal.observed_spread_bps > self._config.max_spread_bps:
            reasons.append("observed_spread_too_high")
        if proposal.observed_liquidity_usd < self._config.min_liquidity_usd:
            reasons.append("observed_liquidity_below_minimum")
        if proposal.worst_case_loss_usd - allowed_risk > COMPARISON_TOLERANCE:
            reasons.append("candidate_worst_case_loss_exceeds_allowed_risk")
        if proposal.notional_usd > exposure_capacity:
            reasons.append("candidate_notional_exceeds_exposure_capacity")
        if proposal.notional_usd > asset_capacity:
            reasons.append("candidate_notional_exceeds_asset_capacity")
        if daily_remaining <= 0:
            reasons.append("daily_loss_limit_reached")
        if weekly_remaining <= 0:
            reasons.append("weekly_loss_limit_reached")

        verdict = "DENY" if reasons else "ALLOW"
        live_allowed = verdict == "ALLOW" and context.mode == TradingMode.LIVE
        return RiskDecision(
            decision_id=str(uuid4()),
            proposal_id=proposal.proposal_id,
            verdict=verdict,
            reasons=tuple(dict.fromkeys(reasons)),
            notes=tuple(dict.fromkeys(notes)),
            level=ladder.level,
            risk_multiplier=ladder.risk_multiplier,
            base_risk_usd=base_risk,
            allowed_risk_usd=allowed_risk,
            candidate_worst_case_loss_usd=proposal.worst_case_loss_usd,
            protected_profit_floor_usd=ladder.protected_profit_floor_usd,
            live_trading_allowed=live_allowed,
            shadow_trading=True,
            decided_at=self._clock.now(),
            approved_asset=proposal.asset if verdict == "ALLOW" else None,
            approved_side=proposal.side if verdict == "ALLOW" else None,
            approved_quantity=proposal.quantity if verdict == "ALLOW" else None,
            approved_notional_usd=proposal.notional_usd if verdict == "ALLOW" else None,
            approved_stop_price=proposal.stop_price if verdict == "ALLOW" else None,
        )

    def _session_reasons(self, context: RiskContext) -> list[str]:
        """US equity session gates: regular hours only, no opening rush, flat by the close."""

        session = context.market_session
        if session not in REGULAR_SESSION_STATES:
            return [SESSION_DENY_REASONS.get(session, "market_session_unknown")]
        reasons: list[str] = []
        if (
            context.minutes_since_open is None
            or context.minutes_since_open < self._config.opening_no_trade_minutes
        ):
            reasons.append("opening_protection_window")
        if (
            context.minutes_to_close is None
            or context.minutes_to_close <= self._config.eod_flatten_minutes_before_close
        ):
            reasons.append("end_of_day_cutoff")
        if context.entries_today >= self._config.max_trades_per_day:
            reasons.append("max_trades_per_day_reached")
        return reasons

    def evaluate_exit(
        self,
        position: PositionDecision,
        context: RiskContext,
    ) -> RiskDecision:
        """Authorize deterministic protective exits without opening new risk."""

        ladder = self._ladder.evaluate(context)
        return RiskDecision(
            decision_id=str(uuid4()),
            proposal_id=position.position_id,
            verdict="EXIT_ONLY",
            reasons=position.reasons,
            level=ladder.level,
            risk_multiplier=Decimal("0"),
            base_risk_usd=Decimal("0"),
            allowed_risk_usd=Decimal("0"),
            candidate_worst_case_loss_usd=Decimal("0"),
            protected_profit_floor_usd=ladder.protected_profit_floor_usd,
            live_trading_allowed=context.mode == TradingMode.LIVE,
            shadow_trading=True,
            decided_at=self._clock.now(),
        )
