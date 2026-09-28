# HT-003 — Risk and profit protection

## Complete

`RiskEngine` is deterministic and authoritative. The profit ladder raises the
required score and reduces the multiplier at $10, $25, $40, $45 and $50. The
35% giveback stop, daily/weekly loss caps, three-loss breaker and 5% equity
drawdown guard fail closed. No martingale, revenge sizing or unlimited DCA is
allowed.

## Verify

```bash
uv run pytest -q tests/unit/test_risk_engine.py tests/unit/test_profit_ladder.py
```

Read **Riesgo** in the terminal: verdict, level, multiplier, protected floor,
remaining loss capacity and block reason must be explicit. `NO_TRADE` is a valid
outcome.

## Evidence and boundary

See `src/trading_bot/risk/engine.py`, `risk/profit_ladder.py`,
`db/state.py` and `spec/guides/risk-and-paper.md`. Marked local equity now
persists the account high-water mark without compounding unrealized PnL; live
high-water-mark and all-exchange-fill reconciliation remain sandbox gates.
