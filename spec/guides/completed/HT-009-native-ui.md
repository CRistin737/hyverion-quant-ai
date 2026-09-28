# HT-009 — Native UI and observability

## Complete

The terminal exposes Overview, Markets, Agents, Risk, Positions, Operations, Providers,
Sources, Paper/Shadow, Backtest, Learning, Audit, Settings and Guides. Operaciones
is the read-only history for orders, fills and evaluated net outcomes; Posiciones
remains the current open-exposure view. Overview
shows real persisted values only; status, empty, stale and error states are
explicit. The UTC validation timestamp stays at the bottom.

## Verify

```bash
QT_QPA_PLATFORM=offscreen uv run pytest -q tests/unit/test_desktop.py
uv run python -m trading_bot desktop
```

Use **Más** for secondary workspaces. The header stays minimal: app name,
navigation and capital. Provider attempts, source runs, deterministic
operational alerts, retention candidates, risk decisions and execution IDs are
available in **Auditoría** without prompts or secrets.

## Evidence and boundary

See `desktop/app.py`, `monitoring/metrics.py`, `monitoring/alerts.py` and
`spec/guides/ui-operations.md`.
`scripts/render_native_ui.py` now produces the supported-size matrix from a
secret-free PAPER fixture. Human review of those PNGs remains required after
layout changes.

> **Nota (2026-09-25):** la interfaz PySide6 descrita aquí fue sustituida por la app Tauri 2 + React de `app/`; `src/trading_bot/desktop/`, `scripts/render_native_ui.py`, `scripts/build_brand_assets.py` y `packaging/hyverion_quant_ai.spec` ya no existen. Ver `docs/DESKTOP_TERMINAL.md`.
