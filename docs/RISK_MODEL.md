# Deterministic Risk Model

All percentages are expressed as human percentages in configuration and converted explicitly.
Money and quantities use `Decimal`.

```text
base risk = min(equity * 0.25%, USD 10)
daily loss = min(equity * 0.75%, USD 25)
weekly loss = min(equity * 2.5%, USD 75)
```

Candidate worst-case loss includes stop distance, estimated entry/exit fees, and adverse slippage.
Allowed risk is the minimum of the ladder-adjusted base risk and remaining daily, weekly,
correlated, exposure, asset, and protected-profit capacities.

| Level | Realized net PnL | Multiplier | Score | Confirmations | Protected floor |
|---|---:|---:|---:|---:|---:|
| 0 | `< 10` | 1.00 | 72 | 3 | none |
| 1 | `10..<25` | 0.75 | 78 | 4 | 50% realized |
| 2 | `25..<40` | 0.50 | 84 | 5 | 65% realized |
| 3 | `40..<45` | 0.25 | 90 | 5 + A+ | 80% peak |
| 4 | `45..<50` | 0.10 | 94 | 5 + A+ | 90% peak |
| 5 | `>= 50` | 0 | n/a | n/a | stop new LIVE entries |

At levels 3–4, the peak base is the maximum of realized PnL, peak realized PnL, and peak total
PnL. A candidate must satisfy:

```text
current total PnL - remaining open risk - candidate worst-case loss >= protected floor
```

Giveback is evaluated independently against realized and total peaks. Returning at least 35% after
the corresponding peak reached USD 10 stops new LIVE entries for the day. Three consecutive losses
also stop LIVE. Drawdown of at least 5% from account high-water mark requires manual review.

Any operation in `UNKNOWN`, `SAFE_MODE` or `RECOVERY_REQUIRED` denies every new entry in every
mode (`unresolved_operation_requires_recovery`) until it is resolved. Protective exits remain
authorized. See [`OPERATION_LIFECYCLE.md`](OPERATION_LIFECYCLE.md).

Profit targets never increase risk. Martingale, unlimited DCA, revenge trading, and recovery grids
are prohibited.
