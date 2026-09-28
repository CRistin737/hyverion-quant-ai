# HT-007 — Database and audit state

## Complete

SQLite runs in WAL mode as the local control plane. Typed repositories persist
account, PnL, fills, positions, source runs, agent runs, model usage, provider
checks, experiments and immutable audit metadata. Parquet/DuckDB remains the
analytical path for high-volume history.

## Verify

```bash
uv run alembic upgrade head
uv run pytest -q tests/integration/test_database.py tests/integration/test_state.py
```

Restarting the local API must preserve safe state and provider-check evidence.
Secret fields must never appear in YAML, SQLite, logs or UI output.

## Evidence and boundary

See `db/database.py`, `db/state.py`, `db/repositories.py` and migrations.
Local retention now has a verified JSONL export, SHA-256 manifest and explicit
dry-run/apply CLI. PostgreSQL/Timescale, remote retention and backup/restore
remain VPS rehearsal tasks.
