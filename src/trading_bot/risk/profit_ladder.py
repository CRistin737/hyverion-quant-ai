from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_bot.config.models import LadderLevelConfig, RiskConfig
from trading_bot.schemas.trading import RiskContext

ONE_HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class ProfitProtectionState:
    level: int
    risk_multiplier: Decimal
    minimum_score: int
    minimum_confirmations: int
    protected_profit_floor_usd: Decimal
    stop_live_for_day: bool
    reason: str | None = None


class ProfitProtectionLadder:
    def __init__(self, config: RiskConfig) -> None:
        self._config = config

    def evaluate(self, context: RiskContext) -> ProfitProtectionState:
        realized = context.realized_net_pnl_today
        cap = self._config.daily_profit_hard_cap_usd
        if cap is not None and realized >= cap:
            return ProfitProtectionState(
                level=5,
                risk_multiplier=Decimal("0"),
                minimum_score=100,
                minimum_confirmations=999,
                protected_profit_floor_usd=cap,
                stop_live_for_day=True,
                reason="daily_profit_hard_cap_reached",
            )

        giveback_reason = self._giveback_reason(context)
        level = self._find_level(realized, context.equity)
        protected_base = realized
        if level.level >= 3:
            protected_base = max(
                realized,
                context.intraday_peak_realized_pnl,
                context.intraday_peak_total_pnl,
            )
        floor = max(Decimal("0"), protected_base * level.protected_fraction)
        return ProfitProtectionState(
            level=level.level,
            risk_multiplier=level.risk_multiplier,
            minimum_score=level.minimum_score,
            minimum_confirmations=level.minimum_confirmations,
            protected_profit_floor_usd=floor,
            stop_live_for_day=giveback_reason is not None,
            reason=giveback_reason,
        )

    def _find_level(self, realized: Decimal, equity: Decimal) -> LadderLevelConfig:
        percent = realized / equity * ONE_HUNDRED
        if percent < self._config.ladder[0].maximum_pnl_percent:
            return self._config.ladder[0]
        for level in self._config.ladder[1:]:
            if level.minimum_pnl_percent <= percent < level.maximum_pnl_percent:
                return level
        return self._config.ladder[-1]

    def _giveback_reason(self, context: RiskContext) -> str | None:
        """Stop new entries after giving back too much of the day's peak gain.

        Only once the peak is meaningful (the first ladder band, as a percent
        of equity), so a few cents of noise never stop the day.
        """

        threshold = self._config.max_profit_giveback_percent / ONE_HUNDRED
        minimum_peak = context.equity * self._config.ladder[0].maximum_pnl_percent / ONE_HUNDRED
        checks = (
            ("realized", context.intraday_peak_realized_pnl, context.realized_net_pnl_today),
            ("total", context.intraday_peak_total_pnl, context.current_total_pnl),
        )
        for label, peak, current in checks:
            if peak < minimum_peak or peak <= 0:
                continue
            giveback = (peak - current) / peak
            if giveback >= threshold:
                return f"max_profit_giveback_{label}_reached"
        return None
