# Database and Analytical Storage

SQLite is the local control plane. Connections enable WAL, foreign keys, and a five-second busy
timeout. Initialization has a bounded retry for concurrent startup. `PRAGMA integrity_check` feeds
the readiness status; failure never causes automatic replacement.

The schema includes accounts, exchange connections, market snapshots, candles, news/social items,
features, agent runs/outputs, signals, proposals, critic/risk decisions, orders, fills, positions,
daily PnL, shadow trades, evaluations, usage, versions, change proposals, experiments, deployments,
events, and alerts.

`operations` holds the current lifecycle state of every trade operation and `operation_events` is
its append-only transition log (ordered by a per-operation `sequence`). Both were added by the
additive revision `0003`; see [`OPERATION_LIFECYCLE.md`](OPERATION_LIFECYCLE.md).

Memory tables arrive in `0004`–`0006` (pgvector embeddings in `0005`, PostgreSQL only).
`decision_attributions` (revision `0007`) is an audit table with one row per specialist-pipeline
proposal recording each agent's stance, scored later by meta-memory; see
[`MEMORIA_CICLO.md`](MEMORIA_CICLO.md). `strategic_memory_snapshots` (revision
`0008`) stores the mutable fields of a knowledge item after every change so research replays can
rebuild its state at any past moment; see [`MEMORIA.md`](MEMORIA.md).

Revision `0001` builds the schema from the live SQLAlchemy metadata, so later revisions must be
idempotent on a fresh database (`checkfirst` creation, column-existence checks).

`alembic upgrade head` applies the baseline migration. Future schema changes must add an immutable
revision; never edit a deployed revision. Production migration to PostgreSQL keeps repository
interfaces and replaces the SQLAlchemy URL. TimescaleDB is optional for later time-series needs.

Raw tick/order-book history is written in bounded Parquet batches partitioned by date. DuckDB
accepts read-only SELECT queries against those files. Retention and compaction must be configured
before continuous high-volume collection.
