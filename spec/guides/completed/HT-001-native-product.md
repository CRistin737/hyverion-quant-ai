# HT-001 — Native product and identity

## Complete

The deliverable is a PySide6 desktop terminal named **Hyverion Quant AI**. The
orbital mark belongs to the macOS application/Dock icon; the workspace remains
clean and text-led. The native client reads the local control API and never
receives private exchange credentials.

## Verify

```bash
uv run python -m trading_bot doctor
uv run python -m trading_bot desktop
```

Confirm the title, icon, compact top navigation, `PAPER` state and the UTC
validation footer. The supported window minimum is 1180×760.

## Evidence and boundary

Evidence is in `src/trading_bot/desktop/app.py`, the PyInstaller spec and
`assets/hyverion-quant-ai-icon.*`. Signing and distribution are release gates;
they are not required for local PAPER.

> **Nota (2026-09-25):** la interfaz PySide6 descrita aquí fue sustituida por la app Tauri 2 + React de `app/`; `src/trading_bot/desktop/`, `scripts/render_native_ui.py`, `scripts/build_brand_assets.py` y `packaging/hyverion_quant_ai.spec` ya no existen. Ver `docs/DESKTOP_TERMINAL.md`.
