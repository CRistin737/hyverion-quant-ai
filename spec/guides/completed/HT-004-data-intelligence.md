# HT-004 — Market and external intelligence

## Complete

The collector boundary is `fetch → validate → sanitize → normalize → dedupe →
freshness → persist → trigger`. Public market data has UTC event, received and
processed timestamps. RSS and official X/Reddit connectors are read-only;
external text is untrusted data and never instructions.

## Verify

```bash
uv run pytest -q tests/unit/test_external_collector.py tests/unit/test_pipeline_components.py
uv run python -m trading_bot collect --once
```

Inspect **Mercados** and **Fuentes** for freshness, source-run state, duplicate
counts and parser errors. Unknown or stale data must not create a trade.

## Evidence and boundary

See `data/external_collector.py`, `data/sources.py` and
`spec/guides/sources.md`. HTML scraping is deliberately blocked until a source
has reviewed ToS/robots evidence and a bounded allowlist entry.
