# HT-008 — PAPER, SHADOW and research

## Complete

PAPER fills include fees, spread/slippage assumptions, idempotency and partial
fill state. Rejected proposals create bounded SHADOW alternatives. Backtests
support train/validation/OOS and walk-forward metadata without creating an
execution intent.

## Verify

```bash
uv run python -m trading_bot paper --fixture --capital 100 --symbol BTC/USDT
uv run python -m trading_bot shadow --fixture --symbol BTC/USDT
uv run python -m trading_bot backtest --walk-forward
```

Use **Paper / Shadow** and **Backtest** to inspect costs, rejection reasons and
experiment metadata. A backtest is never a profitability guarantee.

## Evidence and boundary

See `exchange/paper.py`, `simulation/shadow.py` and `simulation/backtest.py`. Real-fill
shadow comparison and exchange-backed restart recovery remain gated.
