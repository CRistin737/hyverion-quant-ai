# HT-010 — Learning and strategy plugins

## Complete

Learning calculates expectancy, win rate, profit factor, drawdown, MFE/MAE,
fees, slippage, cost drag and incremental agent value. Optimizer output is a
bounded, persisted `ChangeProposal` through `LearningSupervisor`;
champion/challenger evidence is required and
`AUTO_PROMOTE=false`. Trend/momentum is enabled; breakout and mean-reversion
remain candidates until replay evidence exists.

## Verify

```bash
uv run pytest -q tests/unit/test_learning.py tests/unit/test_strategy_plugins.py
```

In **Aprendizaje**, inspect invalid-row counts, proposals, agent memory and
challenger metrics. Select a proposal to record a review-only transition;
deployment states are intentionally unavailable. No agent can write the active
checkout, modify risk or approve itself.

## Evidence and boundary

See `learning/`, `strategies/`, `schemas/learning.py` and
`spec/guides/learning.md`. Full evidence persistence and isolated worktree
deployment review remain required before any promotion.
