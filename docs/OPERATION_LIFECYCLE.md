# Operation Lifecycle

Every trade proposal becomes an *operation* that is tracked until it is closed, reconciled and
evaluated. The lifecycle is deterministic and independent of any AI provider: it only records
where an operation is, so an open position can never be forgotten after a crash, a provider outage
or a network failure.

```text
PROPOSED -> CRITIC_REVIEWED -> RISK_APPROVED -> EXECUTION_PENDING
 -> SUBMITTED -> ACKNOWLEDGED -> PARTIALLY_FILLED -> OPEN
 -> EXIT_PENDING -> CLOSING -> CLOSED -> RECONCILED -> EVALUATED
```

Exception states: `REJECTED`, `CANCEL_PENDING`, `CANCELED`, `UNKNOWN`, `SAFE_MODE`,
`RECOVERY_REQUIRED`. Terminal states: `EVALUATED`, `REJECTED`, `CANCELED`.

## Components

| Component | Path | Responsibility |
|---|---|---|
| State machine | `src/trading_bot/exchange/lifecycle.py` | `OperationState`, allowed transitions, order-status projection |
| Repository | `src/trading_bot/db/lifecycle.py` | `operations` projection + append-only `operation_events` log |
| Orchestrator wiring | `src/trading_bot/core/orchestrator.py` | Advances entry and exit paths |
| Post-close | `src/trading_bot/core/post_close.py` | `CLOSED -> RECONCILED -> EVALUATED` |
| Restart recovery | `src/trading_bot/core/recovery.py` | Resolves every non-terminal operation from evidence |

## Invariants

- A transition outside the table is persisted as `RECOVERY_REQUIRED` (fail closed) and logged
  with `legal = false`. Terminal operations are never reopened.
- `SAFE_MODE` and `RECOVERY_REQUIRED` are reachable from every active state; repeated escalation
  is idempotent.
- A submission timeout (`SubmissionStateUnknown`) parks the operation in `UNKNOWN`. It is never
  treated as a failed order and never retried blindly; only a venue lookup resolves it.
- Any operation in `UNKNOWN`, `SAFE_MODE` or `RECOVERY_REQUIRED` sets
  `RiskContext.unresolved_operations`, and `RiskEngine` denies every new entry in every mode with
  `unresolved_operation_requires_recovery`. Deterministic protective exits are not blocked.
- PAPER/SHADOW reconcile a closed position against the local fill log (flat quantity, entry fill
  quantity equals exit fill quantity). A mismatch moves the operation to `SAFE_MODE` and writes a
  `RECONCILIATION_MISMATCH` system event. Real venues stay `CLOSED` until an authenticated
  exchange reconciliation proves them.
- Every exit attempt records its `client_order_id` on the `EXIT_PENDING` event. On a real venue,
  an exit that leaves a resting remainder keeps the operation in `EXIT_PENDING`/`CLOSING`; before
  any new exit the orchestrator looks up that order. Still working → no new order (never two
  overlapping reduce-only orders). Resolved at the venue but not projected locally →
  `RECOVERY_REQUIRED`. Simulated venues never leave a remainder.
- Restart recovery never submits, cancels or replaces an order. It uses the read-only
  `ExecutionEngine.lookup`. A venue fill without a local projection is escalated rather than
  reconstructed.

## Post-close evaluation

`TradingStateRepository.closed_trade_evaluation` writes a `trade_evaluations` row with:

- cumulative realized net PnL across partial exits;
- entry plus exit fees;
- entry and exit slippage against the intended limit price;
- MFE/MAE from deterministic marks (`mark_to_market`) and exit fills, in USD;
- exit efficiency (`gross captured / MFE`), `thesis_valid` and `signal_correct`.

Shadow replays compute MFE/MAE along the replayed price path; the tick that triggered a stop or
target is replaced by the protective fill price.

## Restart recovery rules

| State at restart | Resolution |
|---|---|
| `PROPOSED`, `CRITIC_REVIEWED`, `RISK_APPROVED` | `REJECTED` (nothing was submitted) |
| `EXECUTION_PENDING`, `SUBMITTED`, `ACKNOWLEDGED`, `UNKNOWN`, `CANCEL_PENDING` | venue lookup; simulated order absent → `REJECTED`/`CANCELED`; real venue absent → `RECOVERY_REQUIRED`; fill without local projection → `RECOVERY_REQUIRED` |
| `OPEN`, `PARTIALLY_FILLED` | kept if the position projection is open, otherwise `RECOVERY_REQUIRED` |
| `EXIT_PENDING`, `CLOSING` (real venue) | exit order still working → unchanged; otherwise `RECOVERY_REQUIRED` |
| `EXIT_PENDING`, `CLOSING` (PAPER/SHADOW) | position still open → `OPEN` (the position manager re-evaluates); closed → `CLOSED` and post-close |
| `CLOSED`, `RECONCILED` | post-close evaluation |
| `SAFE_MODE`, `RECOVERY_REQUIRED` | unchanged; operator decision required |

A `RESTART_RECOVERY_OK` or `RESTART_RECOVERY_REQUIRED` system event is written only when
recovery changed something or left unresolved operations.

## Operator resolution

`OperatorResolutionService` (`core/operator_resolution.py`) and
`POST /api/v1/operations/{id}/resolve` let an operator record what the venue shows for an
operation in `UNKNOWN`, `SAFE_MODE` or `RECOVERY_REQUIRED`. Allowed targets: `REJECTED`,
`CANCELED` (no local position may exist), `OPEN` (local position must be open), `CLOSED` (local
position must be closed; post-close evaluation follows). A 3–200 character reason is required and
recorded as `operator:<reason>` plus an `OPERATOR_RESOLUTION` system event. The native
**Operaciones** workspace shows the lifecycle table, the per-operation timeline
(`GET /api/v1/operations/{id}/events`) and the resolution action.

## Remaining gates

- Authenticated broker (Alpaca Paper) reconciliation to move real-venue operations to `RECONCILED`. It must
  also prove the entry order is fully resolved (filled or canceled): a partially filled GTC entry
  can keep a resting remainder that fills later.
- Deterministic exit client ids and cancel/replace of a working exit (today a working exit is only
  waited on or escalated).
