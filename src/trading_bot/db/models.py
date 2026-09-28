from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()


accounts = Table(
    "accounts",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String(120), nullable=False),
    Column("currency", String(16), nullable=False, default="USD"),
    Column("equity", Numeric(28, 12), nullable=False),
    Column("high_water_mark", Numeric(28, 12), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

exchange_connections = Table(
    "exchange_connections",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("account_id", ForeignKey("accounts.id"), nullable=True),
    Column("exchange_id", String(80), nullable=False),
    Column("sandbox", Boolean, nullable=False),
    Column("region", String(8), nullable=True),
    Column("credential_reference", String(255), nullable=True),
    Column("withdrawals_disabled_verified", Boolean, nullable=False, default=False),
    Column("last_reconciled_at", DateTime(timezone=True), nullable=True),
    Column("status", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


def _audit_table(name: str) -> Table:
    return Table(
        name,
        metadata,
        Column("id", String(36), primary_key=True),
        Column("asset", String(40), nullable=True),
        Column("event_time", DateTime(timezone=True), nullable=True),
        Column("received_time", DateTime(timezone=True), nullable=True),
        Column("processed_time", DateTime(timezone=True), nullable=True),
        Column("payload", JSON, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Index(f"ix_{name}_asset_created", "asset", "created_at"),
    )


market_snapshots = _audit_table("market_snapshots")
candles = _audit_table("candles")
news_items = _audit_table("news_items")
social_items = _audit_table("social_items")
features = _audit_table("features")
agent_outputs = _audit_table("agent_outputs")
signals = _audit_table("signals")
trade_proposals = _audit_table("trade_proposals")
critic_reviews = _audit_table("critic_reviews")
risk_decisions = _audit_table("risk_decisions")
orders = _audit_table("orders")
fills = _audit_table("fills")
positions = _audit_table("positions")
shadow_trades = _audit_table("shadow_trades")
trade_evaluations = _audit_table("trade_evaluations")
# Each specialist's stance per proposal, scored later by meta-memory.
decision_attributions = _audit_table("decision_attributions")
change_proposals = _audit_table("change_proposals")
experiments = _audit_table("experiments")
deployments = _audit_table("deployments")
system_events = _audit_table("system_events")
alerts = _audit_table("alerts")
source_runs = _audit_table("source_runs")

agent_runs = Table(
    "agent_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("agent_id", String(80), nullable=False),
    Column("agent_version", String(40), nullable=False),
    Column("provider", String(80), nullable=True),
    Column("model", String(120), nullable=True),
    Column("status", String(40), nullable=False),
    Column("latency_ms", Integer, nullable=True),
    Column("error_code", String(120), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

model_usage = Table(
    "model_usage",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("agent_run_id", ForeignKey("agent_runs.id"), nullable=True),
    Column("provider", String(80), nullable=False),
    Column("model", String(120), nullable=False),
    Column("billing_mode", String(40), nullable=False),
    Column("input_tokens", Integer, nullable=False, default=0),
    Column("output_tokens", Integer, nullable=False, default=0),
    Column("cost_usd", Numeric(20, 10), nullable=True),
    Column("attempted_providers", JSON, nullable=True),
    Column("fallback_reason", String(255), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

daily_pnl = Table(
    "daily_pnl",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("account_id", ForeignKey("accounts.id"), nullable=True),
    Column("session_date", Date, nullable=False),
    Column("realized_net_pnl", Numeric(28, 12), nullable=False, default=0),
    Column("unrealized_pnl", Numeric(28, 12), nullable=False, default=0),
    Column("peak_realized_pnl", Numeric(28, 12), nullable=False, default=0),
    Column("peak_total_pnl", Numeric(28, 12), nullable=False, default=0),
    Column("fees", Numeric(28, 12), nullable=False, default=0),
    Column("losing_streak", Integer, nullable=False, default=0),
    Column("live_stopped", Boolean, nullable=False, default=False),
    Column("stop_reason", String(255), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("account_id", "session_date", name="uq_daily_pnl_account_date"),
)

agent_versions = Table(
    "agent_versions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("agent_id", String(80), nullable=False),
    Column("version", String(40), nullable=False),
    Column("spec_hash", String(64), nullable=False),
    Column("spec_path", String(255), nullable=False),
    Column("active", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("agent_id", "version", name="uq_agent_version"),
)

# One row per version of a versionable component (an agent spec or a strategy's
# parameters). Rows are only appended or closed (active_to); the active version
# is the row without active_to. Only the human-approved deployer writes here.
component_versions = Table(
    "component_versions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("component_id", String(80), nullable=False, index=True),
    Column("kind", String(16), nullable=False),
    Column("version", String(40), nullable=False),
    Column("spec_hash", String(64), nullable=True),
    Column("params", JSON, nullable=False),
    Column("proposal_id", String(64), nullable=True),
    # The version this row replaced; "Deshacer" returns to it, never forward.
    Column("replaces_id", String(36), nullable=True),
    Column("reason", String(255), nullable=True),
    Column("active_from", DateTime(timezone=True), nullable=False),
    Column("active_to", DateTime(timezone=True), nullable=True),
)

operations = Table(
    "operations",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("proposal_id", String(64), nullable=False),
    Column("asset", String(40), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("state", String(32), nullable=False),
    Column("client_order_id", String(64), nullable=True),
    Column("position_id", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_operations_state", "state"),
    UniqueConstraint("client_order_id", name="uq_operations_client_order_id"),
)

# Append-only transition log. Rows are never updated or deleted.
operation_events = Table(
    "operation_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("operation_id", ForeignKey("operations.id"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("from_state", String(32), nullable=True),
    Column("to_state", String(32), nullable=False),
    Column("requested_state", String(32), nullable=False),
    Column("legal", Boolean, nullable=False),
    Column("reason", String(255), nullable=False),
    Column("details", JSON, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("operation_id", "sequence", name="uq_operation_events_sequence"),
)

# --- Memory (docs/MEMORIA.md). Portable types; no row is ever deleted. ---

_SCORE = Numeric(6, 5)

memory_candidates = Table(
    "memory_candidates",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("memory_type", String(32), nullable=False),
    Column("title", String(160), nullable=False),
    Column("summary", String(500), nullable=False),
    Column("content", String(4000), nullable=False),
    Column("source_type", String(64), nullable=False),
    Column("source_ids", JSON, nullable=False),
    Column("agent_id", String(64), nullable=True),
    Column("trade_id", String(64), nullable=True),
    Column("symbol", String(40), nullable=True),
    Column("market_regime", String(40), nullable=True),
    Column("confidence", _SCORE, nullable=False),
    Column("importance", _SCORE, nullable=False),
    Column("novelty", _SCORE, nullable=False),
    Column("evidence_strength", _SCORE, nullable=False),
    # Every deterministic confidence component, persisted for audit (revision 0006).
    Column("confidence_components", JSON, nullable=True),
    Column("status", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_memory_candidates_status", "status", "created_at"),
)

memory_evidence = Table(
    "memory_evidence",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("memory_candidate_id", ForeignKey("memory_candidates.id"), nullable=False),
    Column("source_type", String(64), nullable=False),
    Column("source_id", String(64), nullable=False),
    Column("relationship", String(16), nullable=False),
    Column("weight", _SCORE, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_memory_evidence_candidate", "memory_candidate_id"),
)

strategic_memories = Table(
    "strategic_memories",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("knowledge_id", String(64), nullable=False, unique=True),
    Column("title", String(160), nullable=False),
    Column("summary", String(500), nullable=False),
    Column("category", String(32), nullable=False),
    Column("symbol", String(40), nullable=True),
    Column("strategy", String(80), nullable=True),
    Column("market_regime", String(40), nullable=True),
    Column("confidence", _SCORE, nullable=False),
    Column("reliability", _SCORE, nullable=False),
    Column("importance", _SCORE, nullable=False),
    Column("status", String(24), nullable=False),
    Column("valid_from", DateTime(timezone=True), nullable=False),
    Column("valid_until", DateTime(timezone=True), nullable=True),
    Column("last_validated_at", DateTime(timezone=True), nullable=True),
    Column("validation_count", Integer, nullable=False, default=0),
    Column("successful_uses", Integer, nullable=False, default=0),
    Column("failed_uses", Integer, nullable=False, default=0),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_strategic_memories_lookup", "status", "symbol", "market_regime"),
)

strategic_memory_versions = Table(
    "strategic_memory_versions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("strategic_memory_id", ForeignKey("strategic_memories.id"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("content", String(20000), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("previous_version", Integer, nullable=True),
    Column("change_reason", String(255), nullable=False),
    Column("created_by", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("strategic_memory_id", "version", name="uq_strategic_memory_version"),
)

memory_usage = Table(
    "memory_usage",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("memory_id", ForeignKey("strategic_memories.id"), nullable=False),
    Column("agent_run_id", String(64), nullable=True),
    Column("decision_id", String(64), nullable=True),
    Column("retrieval_score", _SCORE, nullable=False),
    Column("used_in_prompt", Boolean, nullable=False),
    Column("influenced_decision", Boolean, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_memory_usage_memory", "memory_id", "created_at"),
)

memory_outcomes = Table(
    "memory_outcomes",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("memory_id", ForeignKey("strategic_memories.id"), nullable=False),
    Column("trade_id", String(64), nullable=False),
    Column("prediction_context", JSON, nullable=False),
    Column("actual_outcome", String(255), nullable=False),
    Column("helpful", Boolean, nullable=True),
    Column("estimated_contribution", Numeric(28, 12), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_memory_outcomes_memory", "memory_id", "created_at"),
)

memory_conflicts = Table(
    "memory_conflicts",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("memory_a_id", ForeignKey("strategic_memories.id"), nullable=False),
    Column("memory_b_id", ForeignKey("strategic_memories.id"), nullable=False),
    Column("conflict_type", String(64), nullable=False),
    Column("resolution", String(255), nullable=True),
    Column("resolved_by", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Point-in-time history of the mutable knowledge fields, so research replays can
# reconstruct status/reliability as they were at a past moment (no look-ahead).
strategic_memory_snapshots = Table(
    "strategic_memory_snapshots",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("memory_id", ForeignKey("strategic_memories.id"), nullable=False),
    Column("status", String(24), nullable=False),
    Column("category", String(32), nullable=False),
    Column("reliability", _SCORE, nullable=False),
    Column("successful_uses", Integer, nullable=False),
    Column("failed_uses", Integer, nullable=False),
    Column("validation_count", Integer, nullable=False),
    Column("last_validated_at", DateTime(timezone=True), nullable=True),
    Column("valid_until", DateTime(timezone=True), nullable=True),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Index("ix_strategic_memory_snapshots_memory", "memory_id", "recorded_at"),
)

MEMORY_TABLES: tuple[Table, ...] = (
    memory_candidates,
    memory_evidence,
    strategic_memories,
    strategic_memory_versions,
    memory_usage,
    memory_outcomes,
    memory_conflicts,
    strategic_memory_snapshots,
)

# ---- QQQ intelligence (phases 6-11). Point-in-time: every row keeps when it was
# published (event_time) and when Hyverion first saw it (received_time).
breadth_snapshots = _audit_table("breadth_snapshots")
component_snapshots = _audit_table("component_snapshots")
sec_filings = _audit_table("sec_filings")
earnings_events = _audit_table("earnings_events")
news_clusters = _audit_table("news_clusters")
options_snapshots = _audit_table("options_snapshots")
rates_snapshots = _audit_table("rates_snapshots")
# One row per broker account sync: the equity history behind period reports.
# Never purged by retention.
equity_snapshots = _audit_table("equity_snapshots")

macro_events = Table(
    "macro_events",
    metadata,
    Column("event_id", String(120), primary_key=True),
    Column("event_type", String(40), nullable=False),
    Column("importance", String(8), nullable=False),
    Column("scheduled_at", DateTime(timezone=True), nullable=False),
    Column("time_known", Boolean, nullable=False),
    Column("source", String(40), nullable=False),
    Column("title", String(255), nullable=False),
    Column("payload", JSON, nullable=False),
    # First time this event was in any calendar we read (§70: nothing before it).
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_macro_events_scheduled", "scheduled_at"),
)

# Official quarterly QQQ holdings (SEC N-PORT). One row per security per report.
index_holdings = Table(
    "index_holdings",
    metadata,
    Column("index_name", String(40), primary_key=True),
    Column("report_date", Date, primary_key=True),
    Column("cusip", String(16), primary_key=True),
    Column("symbol", String(16), nullable=True),
    Column("name", String(255), nullable=False),
    Column("isin", String(16), nullable=True),
    Column("shares", Numeric(28, 8), nullable=False),
    Column("weight", Numeric(18, 12), nullable=False),
    Column("source", String(80), nullable=False),
    Column("filed_at", Date, nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Index("ix_index_holdings_symbol", "index_name", "symbol"),
)


TABLES: dict[str, Table] = {table.name: table for table in metadata.tables.values()}
