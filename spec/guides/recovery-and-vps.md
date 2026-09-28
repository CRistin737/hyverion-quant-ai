# Recovery and VPS handoff

## Local recovery

1. Check `uv run python -m trading_bot doctor` and `/health/ready`.
2. Read structured logs and the native bottom status bar.
3. Export bounded runtime counters before changing the process:
   `curl -fsS http://127.0.0.1:8787/api/v1/observability/metrics` (or add the
   configured bearer token). The Prometheus view is available with
   `?format=prometheus`.
4. If data is stale, pause new proposals; do not widen freshness limits.
5. If provider status is unavailable, keep deterministic exits active and use PAPER/SHADOW.
6. On every restart, inspect the snapshot's `protection_recovery` projection. Any
   open position without `protective_stop_active` is SAFE MODE; do not guess or
   recreate a native stop without an authenticated exchange adapter.
7. If the database integrity check fails, enter SAFE MODE and restore from a verified backup.

For a local SQLite copy, use `uv run python -m trading_bot backup`. The command
uses SQLite Online Backup and verifies the result; the restore procedure is
documented in [backup-and-restore.md](backup-and-restore.md).

## VPS boundary

The same Python modules run under Docker with persistent volumes. Use PostgreSQL when
the control plane needs concurrent remote writers, and keep Parquet/DuckDB for bounded
historical analysis. Inject secrets with Docker Secrets or a managed secret store;
never bake them into the image or compose file.

The local container build is reproducible without the desktop extra and includes the
same handoff matrix and guides used by the native terminal:

```bash
docker build -t hyverion-quant-ai:local .
docker run --rm hyverion-quant-ai:local python -m trading_bot plan-audit
docker run --rm hyverion-quant-ai:local python -m trading_bot doctor
```

The first command must finish successfully, and the two runtime checks must report
`overall=PASS` for the 43-item matrix and `database=ok` respectively. The image runs
as the unprivileged `hyverion` user, has no secrets copied into it, and receives its
SQLite path through `DATABASE_URL` (the Compose default resolves to the persistent
`/app/data` volume from the container working directory). This proves the
local-to-container handoff only; it does not prove a
PostgreSQL restore, remote retention, provider login, or LIVE authorization.

Bind the control API to a private network only, require `CONTROL_API_TOKEN` for remote
state routes, configure restart policies and healthchecks, and verify reconciliation
after every restart. A mismatch means SAFE MODE: no new entries until a human reviews it.

LIVE remains blocked: this release only has paper brokers. With Alpaca Paper, restart
reconciliation (positions, open orders, fills) runs at start and every 10 minutes and blocks
entries on any mismatch; see [broker-paper.md](broker-paper.md).
## Local database compatibility

The native app runs additive SQLite compatibility checks during startup. This
allows a control-plane file created by an older bundle to gain new audit
columns without deleting or replacing data. It does not perform destructive
migrations. For planned schema changes, run the versioned Alembic command and
keep a copy of the database before the operation:

```bash
uv run alembic upgrade head
```
