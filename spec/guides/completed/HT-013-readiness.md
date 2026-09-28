# HT-013 — Deterministic readiness audit

## Purpose

The readiness report is an operator-facing gate review for the local terminal.
It answers whether PAPER is internally coherent without pretending that a
successful local check authorizes private exchange mutations or LIVE trading.

## How to run it

1. Start the native app in its normal local configuration.
2. Open **Más → Auditoría**.
3. Select **Ejecutar auditoría de preparación**.
4. Read every row. `OK`, `INFO`, `PENDIENTE` and `FALLO` remain visible; no row
   is hidden because a provider or exchange account is unavailable.

The same bounded JSON report is available to a token-protected local client:

```bash
curl -fsS http://127.0.0.1:8787/api/v1/readiness
```

## Checks and safety boundary

The report checks PAPER/LIVE configuration, spot/no-leverage defaults, control
database health, the single-primary `ModelRouter` chain, selected official
subscription states and deterministic protective-stop recovery. It also
exposes the two deliberately gated checks: authenticated exchange mutations
and LIVE authorization.

`overall=PAPER_READY` means the local simulation contract is coherent.
`live_authorized` is always `false` in this implementation. Provider failure,
stale data, database inconsistency or missing protection must remain fail-closed
and cannot be converted into a trade by the UI.

## Evidence

- `src/trading_bot/monitoring/readiness.py` — deterministic projection.
- `src/trading_bot/control_api.py` — token-protected transport boundary.
- `src/trading_bot/desktop/app.py` — native Audit panel.
- `tests/unit/test_control_api.py` and `tests/unit/test_desktop.py` — API and
  UI coverage.
