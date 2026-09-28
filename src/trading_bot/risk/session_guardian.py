from __future__ import annotations

from trading_bot.config.models import RiskConfig
from trading_bot.core.clock import Clock
from trading_bot.risk.profit_ladder import ProfitProtectionLadder
from trading_bot.schemas.trading import RiskContext, SessionDecision


class SessionGuardian:
    def __init__(self, config: RiskConfig, clock: Clock) -> None:
        self._config = config
        self._clock = clock
        self._ladder = ProfitProtectionLadder(config)

    def evaluate(self, context: RiskContext) -> SessionDecision:
        state = self._ladder.evaluate(context)
        reasons: list[str] = []
        action = "CONTINUE"
        if context.live_stopped:
            action = "STOP_LIVE_FOR_DAY"
            reasons.append(context.session_stop_reason or "live_session_stopped")
        elif state.stop_live_for_day:
            action = "STOP_LIVE_FOR_DAY"
            reasons.append(state.reason or "profit_protection_stop")
        elif context.losing_streak >= self._config.max_losing_streak:
            action = "STOP_LIVE_FOR_DAY"
            reasons.append("maximum_losing_streak_reached")
        elif state.level >= 3:
            action = "REDUCE_RISK"
            reasons.append("protect_profit_default")
        elif context.cooldown_active:
            action = "PAUSE"
            reasons.append("loss_cooldown_active")
        else:
            reasons.append("session_within_limits")
        return SessionDecision.model_validate(
            {
                "action": action,
                "reasons": reasons,
                "live_trading_allowed": action in {"CONTINUE", "REDUCE_RISK"},
                "shadow_trading": True,
                "created_at": self._clock.now(),
            }
        )
