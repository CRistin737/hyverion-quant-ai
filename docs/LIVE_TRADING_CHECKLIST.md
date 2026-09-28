# LIVE Trading Checklist

LIVE is not implemented or certified in this release. `python -m trading_bot live --confirm-live`
always exits blocked.

Before a future LIVE release, all items must have machine-verifiable evidence:

- Operating region and broker (Alpaca/IBKR) account and product availability verified against current official terms.
- Spot only; leverage and derivatives feature flags disabled.
- Trading key stored in keyring/secret manager, withdrawals disabled, IP allowlist enabled if offered.
- Authenticated connection test passes without logging credential material.
- Balance, open-order, position, and recent-fill reconciliation passes after restart.
- Idempotency, ambiguous timeout, partial fill, cancel/replace, and protective-stop tests pass against the broker's paper environment.
- Full risk/failure suite hash and configuration version are recorded.
- `LIVE_TRADING=true`, explicit `--confirm-live`, and a typed interactive phrase are all required.
- Dashboard displays persistent red LIVE state; alerts and rollback procedure are operational.

No checklist item may be waived to pursue the USD 10–50 target.
