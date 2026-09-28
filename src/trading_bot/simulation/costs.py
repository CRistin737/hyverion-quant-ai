"""One transaction-cost model for strategies, shadow trades and replays (§77, §101).

US equities at a zero-commission broker still pay: half the spread on each side,
slippage, and small regulatory fees on sales. These are *estimates* to be
checked against real fills (the paper-vs-reality report, phase 13). Three
scenarios bound them; a strategy that only makes money in the optimistic one is
rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

BPS_DENOMINATOR = Decimal("10000")


class CostScenario(StrEnum):
    OPTIMISTIC = "optimistic"
    BASE = "base"
    STRESS = "stress"


@dataclass(frozen=True, slots=True)
class CostModel:
    scenario: CostScenario
    # Price concession on each fill, beyond the quoted spread.
    slippage_bps: Decimal
    # Half of the spread is paid when crossing it; never assume less than this.
    min_half_spread_bps: Decimal
    # Commission as a rate of notional (0 at Alpaca) plus sell-side regulatory fees.
    commission_bps: Decimal
    sell_fee_bps: Decimal

    def fill_price(
        self, price: Decimal, *, buy: bool, spread_bps: Decimal = Decimal("0")
    ) -> Decimal:
        """Price actually paid (buy) or received (sell) after spread and slippage."""

        concession = max(spread_bps / 2, self.min_half_spread_bps) + self.slippage_bps
        rate = concession / BPS_DENOMINATOR
        return price * (1 + rate) if buy else price * (1 - rate)

    def fees(self, *, buy_notional: Decimal, sell_notional: Decimal) -> Decimal:
        return (
            (buy_notional + sell_notional) * self.commission_bps
            + sell_notional * self.sell_fee_bps
        ) / BPS_DENOMINATOR

    def round_trip_bps(self, spread_bps: Decimal = Decimal("0")) -> Decimal:
        """Total cost of entering and leaving, in bps of notional."""

        concession = max(spread_bps / 2, self.min_half_spread_bps) + self.slippage_bps
        return 2 * concession + 2 * self.commission_bps + self.sell_fee_bps


SCENARIOS: dict[CostScenario, CostModel] = {
    CostScenario.OPTIMISTIC: CostModel(
        CostScenario.OPTIMISTIC,
        slippage_bps=Decimal("0.5"),
        min_half_spread_bps=Decimal("0.2"),
        commission_bps=Decimal("0"),
        sell_fee_bps=Decimal("0.1"),
    ),
    CostScenario.BASE: CostModel(
        CostScenario.BASE,
        slippage_bps=Decimal("2"),
        min_half_spread_bps=Decimal("0.5"),
        commission_bps=Decimal("0"),
        sell_fee_bps=Decimal("0.3"),
    ),
    CostScenario.STRESS: CostModel(
        CostScenario.STRESS,
        slippage_bps=Decimal("5"),
        min_half_spread_bps=Decimal("1"),
        commission_bps=Decimal("0"),
        sell_fee_bps=Decimal("1"),
    ),
}
BASE_COSTS = SCENARIOS[CostScenario.BASE]
# Legacy buy-and-hold baseline and shadow defaults follow the base scenario.
DEFAULT_FEE_BPS = BASE_COSTS.sell_fee_bps
DEFAULT_SLIPPAGE_BPS = BASE_COSTS.slippage_bps + BASE_COSTS.min_half_spread_bps


def bps_cost(amount: Decimal, bps: Decimal) -> Decimal:
    """Return ``amount * bps / 10000`` evaluated left to right in Decimal.

    Pass ``Decimal("1")`` as ``amount`` to obtain the bare rate used for price
    multipliers such as ``1 + rate``.
    """

    return amount * bps / BPS_DENOMINATOR


# Notional equity for research replays and the internal simulator only. Live
# PAPER sizing always uses the broker account's synced equity; USD risk caps
# bind long before this number matters.
SIMULATION_CAPITAL_USD = Decimal("10000")
