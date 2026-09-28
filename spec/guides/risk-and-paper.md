# Risk, PAPER and SHADOW

## Starting values

With the initial `$100` account:

```text
base risk = min(equity * 0.25%, $10)
daily loss = min(equity * 0.75%, $25)
weekly loss = min(equity * 2.5%, $75)
```

The profit ladder raises the required score and lowers the multiplier as realized
profit rises. At `$50` realized net PnL, new LIVE entries are forbidden and SHADOW
continues. A 35% giveback after the relevant peak, three consecutive losses or a 5%
account drawdown blocks new LIVE risk.

## PAPER

PAPER uses public data and the deterministic simulator. Fills include fee and
slippage assumptions, support idempotency and preserve protective-stop state.
Target/stop checks run deterministically before new entry analysis; their
reduce-only fills update realized PnL, fees and losing streak. A paper result is
not evidence of guaranteed profitability.

## SHADOW

Shadow records proposals that were not executed and simulates entry, stop, target,
fees and slippage. Compare the alternative with the real trade after the outcome is
known; keep the reason the real trade was rejected.

RiskEngine remains the final authority for both modes. No agent can increase size to
recover a loss or remove a protective stop.

## Safety net for every position (2026-09-26)
- **Time stop:** `risk.max_position_hold_minutes` (default 240). A position older than this is closed with
  `TIME_EXIT` even if neither stop nor target was touched. Stop and target still win over the time stop.
- **Exact authorization:** an `ALLOW` decision states the approved asset, side, quantity and notional.
  `ExecutionEngine` refuses an intent that differs or exceeds it, a decision older than
  `risk.max_decision_age_seconds`, and a second order from the same decision.
- **Price sanity:** entries priced more than `risk.max_entry_deviation_bps` from the observed market are denied.
- **Orphans:** open positions whose symbol left `allowed_symbols` keep getting deterministic exits.
- **Emergency flatten:** `POST /api/v1/engine/flatten` leaves `flatten.request`; the running engine closes every
  position through the normal exit path and blocks new entries until flat. The API never submits orders.
- **Continuous trigger check:** between entry cycles (`run --interval-seconds`, default 60) the engine
  re-checks every open position each `risk.position_check_seconds` (default 10) with a protective-only
  cycle: stops, targets and time limits are evaluated without AI and without new entries.
