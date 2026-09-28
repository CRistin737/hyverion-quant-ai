# HT-012 — Verification and handoff

## Complete

The project has a durable specification, task ledger, completed-task guides,
provider-specific handoffs and requirement matrix. Every continuation must
update evidence rather than relying on conversation history.

## Full local gate

```bash
uv run ruff check .
uv run mypy src
uv run pytest --cov=trading_bot --cov-report=term-missing
uv run alembic upgrade head
uv run python -m trading_bot doctor
uv run pyinstaller packaging/hyverion_quant_ai.spec --clean --noconfirm
```

The gate proves the offline contract only. It does not authorize a real order.

## Handoff rule

Update the plan coverage matrix, the completion audit, the relevant task and
this guide after each bounded change. A `FOUNDATION` or `GATED` status must stay
visible until its exact integration evidence exists.

> **Nota (2026-09-25):** la interfaz PySide6 descrita aquí fue sustituida por la app Tauri 2 + React de `app/`; `src/trading_bot/desktop/`, `scripts/render_native_ui.py`, `scripts/build_brand_assets.py` y `packaging/hyverion_quant_ai.spec` ya no existen. Ver `docs/DESKTOP_TERMINAL.md`.
