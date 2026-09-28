"""Named risk profiles, the way a professional trader sizes: percent of equity.

A profile fixes four numbers: risk per trade, daily loss, weekly loss and the
maximum drawdown from the account peak. Everything else in ``RiskConfig`` stays
as configured. Choosing a profile rewrites those four values; editing one of
them by hand makes the profile ``personalizado``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ProfileName = Literal["conservador", "medio", "alto"]


@dataclass(frozen=True, slots=True)
class RiskProfile:
    base_risk_percent: Decimal
    daily_loss_percent: Decimal
    weekly_loss_percent: Decimal
    max_account_drawdown_percent: Decimal

    def as_values(self) -> dict[str, Decimal]:
        return {
            "base_risk_percent": self.base_risk_percent,
            "daily_loss_percent": self.daily_loss_percent,
            "weekly_loss_percent": self.weekly_loss_percent,
            "max_account_drawdown_percent": self.max_account_drawdown_percent,
        }


RISK_PROFILES: dict[ProfileName, RiskProfile] = {
    "conservador": RiskProfile(Decimal("0.25"), Decimal("1"), Decimal("2.5"), Decimal("5")),
    "medio": RiskProfile(Decimal("0.5"), Decimal("2"), Decimal("5"), Decimal("10")),
    "alto": RiskProfile(Decimal("1"), Decimal("3"), Decimal("8"), Decimal("15")),
}

PROFILE_FIELDS = frozenset(RISK_PROFILES["medio"].as_values())
