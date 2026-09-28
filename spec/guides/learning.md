# Learning and safe self-improvement

The supervisor evaluates expectancy, drawdown, calibration, false positives and
negatives, MFE/MAE, fees, slippage, latency and incremental agent value. PnL alone is
not sufficient.

The native **Aprendizaje** workspace calculates descriptive metrics from the
bounded persisted evaluation history: sample integrity, win rate, expectancy,
profit factor, maximum drawdown, average MFE/MAE, fee/slippage drag and the
accumulated incremental value attributed to each agent. Invalid rows are
counted separately instead of being treated as zero. A metric never edits
weights, prompts or source code.

## Change path

```text
observation -> ChangeProposal -> TESTING -> READY_FOR_REVIEW
-> human approval -> DEPLOYED -> rollback if needed
```

Every proposal records agent, current/candidate version, evidence, affected rules,
expected improvement, risk and rollback information. The challenger must pass
historical replay, backtest, walk-forward, recent PAPER and SHADOW evidence. The
active prompt, risk YAML and checkout are immutable to agents.

`LearningSupervisor` validates and persists proposals as immutable audit rows. It
rejects empty evidence, same-version candidates, sensitive candidate fields and
oversized specs. Transitioning a proposal records the previous and next state;
it never writes the active checkout or deploys a candidate.

The native review controls use the local control API:

```text
GET  /api/v1/learning/proposals
POST /api/v1/learning/proposals
POST /api/v1/learning/proposals/{id}/transition
```

The terminal exposes only `TESTING`, `READY_FOR_REVIEW`, `APPROVED` and
`REJECTED` transitions. `DEPLOYED` and `ROLLED_BACK` remain outside this UI
until an isolated deployment/recovery workflow has its own evidence.

`AUTO_PROMOTE_AGENT_CHANGES=false` is mandatory. An agent may suggest code changes,
but it cannot write files, run a deployment, loosen a risk rule or approve itself.

Strategy configuration is also fail-closed: only plugins with a deterministic
implementation and tests may appear in `strategies.enabled`. The current local
release enables `trend_momentum`; adding breakout or mean-reversion requires a
new plugin contract, replay evidence and an explicit review task first.
