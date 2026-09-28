/**
 * Deterministic demo data for development, screenshots and visual tests.
 * Synthetic numbers only; the static catalog (agents, providers, sources) comes
 * from `catalog.json`, which mirrors the backend's built-in descriptors.
 */

import catalog from "./catalog.json";
import type { AiModels, AiUsage,
  MemoryCandidate,
  MemoryConflict,
  Agent,
  Candle,
  KnowledgeItem,
  Market,
  MemoryStatus,
  Operation,
  PnlDay,
  Provider,
  PublicConfig,
  Readiness,
  RiskLimits,
  SetupStatus,
  Snapshot,
  Source,
  VaultNote,
  VaultOverview,
} from "../types";

export const DEMO_NOW = new Date("2026-09-25T14:02:11Z");
const iso = (minutesAgo: number) => new Date(DEMO_NOW.getTime() - minutesAgo * 60_000).toISOString();

function prng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

function candles(symbol: string, start: number, vol: number, seed: number, count = 120): Candle[] {
  const rand = prng(seed);
  const out: Candle[] = [];
  let price = start;
  for (let i = count - 1; i >= 0; i -= 1) {
    const drift = (rand() - 0.47) * vol;
    const open = price;
    const close = Math.max(1, open + drift);
    const high = Math.max(open, close) + rand() * vol * 0.6;
    const low = Math.min(open, close) - rand() * vol * 0.6;
    price = close;
    out.push({
      symbol,
      interval: "1m",
      open: open.toFixed(2),
      high: high.toFixed(2),
      low: low.toFixed(2),
      close: close.toFixed(2),
      volume: (20000 + rand() * 80000).toFixed(0),
      event_time: iso(i),
    });
  }
  return out;
}

function market(symbol: string, series: Candle[], volume: string): Market {
  const last = series[series.length - 1]!;
  const history = series.slice(-60).map((c) => ({
    symbol,
    bid: (Number(c.close) - 0.01).toFixed(2),
    ask: c.close,
    last: c.close,
    session_volume: volume,
    session_dollar_volume: (Number(volume) * Number(c.close)).toFixed(0),
    provider: "alpaca",
    feed: "iex",
    is_delayed: false,
    event_time: c.event_time,
  }));
  return { ...history[history.length - 1]!, last: last.close, history, candles: series };
}

const qqq = candles("QQQ", 481.2, 0.35, 7);
const spy = candles("SPY", 668.4, 0.45, 11);

const risk: RiskLimits = {
  profile: "medio",
  base_risk_percent: "0.5",
  max_base_risk_usd: null,
  daily_loss_percent: "2",
  daily_loss_hard_cap_usd: null,
  weekly_loss_percent: "5",
  weekly_loss_hard_cap_usd: null,
  max_profit_giveback_percent: "35",
  max_positions: 3,
  max_total_exposure_percent: "100",
  max_asset_exposure_percent: "100",
  max_correlated_risk_percent: "2",
  max_spread_bps: "20",
  max_slippage_bps: "15",
  min_liquidity_usd: "100000",
  max_losing_streak: 3,
  cooldown_minutes_after_loss: 30,
  max_account_drawdown_percent: "10",
  daily_profit_hard_cap_usd: null,
  max_trades_per_day: 10,
  opening_no_trade_minutes: 5,
  eod_flatten_minutes_before_close: 10,
  max_position_hold_minutes: 240,
  max_entry_deviation_bps: "50",
  max_decision_age_seconds: 60,
  ladder: [
    { level: 0, minimum_pnl_percent: "0", maximum_pnl_percent: "1", risk_multiplier: "1.0", minimum_score: 65, minimum_confirmations: 2, protected_fraction: "0" },
    { level: 1, minimum_pnl_percent: "1", maximum_pnl_percent: "2", risk_multiplier: "0.75", minimum_score: 70, minimum_confirmations: 3, protected_fraction: "0.4" },
    { level: 2, minimum_pnl_percent: "2", maximum_pnl_percent: "3", risk_multiplier: "0.5", minimum_score: 75, minimum_confirmations: 3, protected_fraction: "0.5" },
    { level: 3, minimum_pnl_percent: "3", maximum_pnl_percent: "4", risk_multiplier: "0.35", minimum_score: 80, minimum_confirmations: 4, protected_fraction: "0.6" },
    { level: 4, minimum_pnl_percent: "4", maximum_pnl_percent: "100", risk_multiplier: "0.25", minimum_score: 85, minimum_confirmations: 4, protected_fraction: "0.7" },
  ],
};

const bound = (minimum: string, maximum: string) => ({ minimum, maximum });

const config: PublicConfig = {
  ...(catalog.config as unknown as PublicConfig),
  operating_region: "US",
  ai_provider: "anthropic",
  ai_fallback_providers: ["openai", "xai"],
  ai_models: { analysis: "claude-sonnet-5", decision: "claude-opus-5-5", improvement: "claude-opus-5-5" },
  risk,
  risk_profiles: {
    conservador: { base_risk_percent: "0.25", daily_loss_percent: "1", weekly_loss_percent: "2.5", max_account_drawdown_percent: "5" },
    medio: { base_risk_percent: "0.5", daily_loss_percent: "2", weekly_loss_percent: "5", max_account_drawdown_percent: "10" },
    alto: { base_risk_percent: "1", daily_loss_percent: "3", weekly_loss_percent: "8", max_account_drawdown_percent: "15" },
  },
  autonomy: {
    mode: "paper_autonomous",
    auto_promote_paper: true,
    shadow_sessions_before_promotion: 5,
    envelope: {
      base_risk_percent: bound("0.05", "1.0"),
      daily_loss_percent: bound("0.25", "3"),
      weekly_loss_percent: bound("0.5", "10"),
      max_account_drawdown_percent: bound("2", "20"),
      max_trades_per_day: bound("1", "20"),
      max_profit_giveback_percent: bound("20", "60"),
    },
  },
};

function pnlHistory(): PnlDay[] {
  const rand = prng(3);
  const days: PnlDay[] = [];
  for (let i = 29; i >= 0; i -= 1) {
    const date = new Date(DEMO_NOW.getTime() - i * 86_400_000).toISOString().slice(0, 10);
    const value = (rand() - 0.42) * 38;
    days.push({
      session_date: date,
      realized_net_pnl: value.toFixed(2),
      unrealized_pnl: "0",
      fees: (1.2 + rand() * 2).toFixed(2),
      losing_streak: value < 0 ? 1 : 0,
      live_stopped: false,
      stop_reason: "session_within_limits",
    });
  }
  days[days.length - 1]!.realized_net_pnl = "82.17";
  return days;
}

const operations: Operation[] = [
  { id: "op-7f3a91c2", proposal_id: "p-51c0", asset: "QQQ", mode: "paper", state: "OPEN", client_order_id: "bot300-7f3a91c2", position_id: "pos-qqq-01", created_at: iso(46), updated_at: iso(44) },
  { id: "op-b82e0d17", proposal_id: "p-51b9", asset: "QQQ", mode: "paper", state: "RECOVERY_REQUIRED", client_order_id: "bot300-b82e0d17", position_id: null, created_at: iso(95), updated_at: iso(12) },
  { id: "op-2c44e8aa", proposal_id: "p-51a2", asset: "QQQ", mode: "paper", state: "EVALUATED", client_order_id: "bot300-2c44e8aa", position_id: "pos-qqq-00", created_at: iso(260), updated_at: iso(180) },
  { id: "op-91d0f3b5", proposal_id: "p-5188", asset: "QQQ", mode: "paper", state: "REJECTED", client_order_id: null, position_id: null, created_at: iso(310), updated_at: iso(310) },
  { id: "op-4e6a02cd", proposal_id: "p-5171", asset: "QQQ", mode: "paper", state: "CLOSED", client_order_id: "bot300-4e6a02cd", position_id: "pos-qqq-99", created_at: iso(420), updated_at: iso(355) },
  { id: "op-0a7bb219", proposal_id: "p-5160", asset: "QQQ", mode: "paper", state: "CANCELED", client_order_id: "bot300-0a7bb219", position_id: null, created_at: iso(510), updated_at: iso(506) },
];

const agents: Agent[] = (catalog.agents as Agent[]).map((agent, index) => {
  const deterministic = ["master_orchestrator", "optimizer", "position_manager", "session_guardian"].includes(agent.agent_id);
  if (deterministic) return agent;
  return {
    ...agent,
    status: agent.agent_id === "social" ? "ERROR" : index % 3 === 0 ? "ACTIVE" : "IDLE",
    last_run_at: iso(2 + index),
    last_error: agent.agent_id === "social" ? "provider_timeout" : null,
    provider: "anthropic",
    model: "claude-sonnet-5",
    latency_ms: 900 + index * 140,
  };
});

const providers: Provider[] = (catalog.providers as Provider[]).map((provider) =>
  provider.provider_id === "anthropic"
    ? { ...provider, auth_state: "CONNECTED", usage_state: "AVAILABLE", detail: "Suscripción activa vía CLI oficial." }
    : provider.provider_id === "openai"
      ? {
          ...provider,
          auth_state: "CONNECTED",
          billing_mode: "subscription",
          usage_state: "AVAILABLE",
          detail: "Suscripción activa vía CLI oficial.",
          circuit: { state: "open" as const, failures: 1, last_code: "provider_quota_exhausted", retry_at: new Date(DEMO_NOW.getTime() + 25 * 60_000).toISOString() },
        }
      : provider,
);

const sources: Source[] = (catalog.sources as Source[]).map((source) =>
  source.source_id === "market-data"
    ? { ...source, last_success_at: iso(0.2), freshness_seconds: 12, records_today: 18_442 }
    : source,
);

export const demoSnapshot: Snapshot = {
  generated_at: DEMO_NOW.toISOString(),
  engine: { state: "running", pid: 4242, started_at: DEMO_NOW.toISOString(), stopped_at: null, exit_code: null, interval_seconds: 60, mode: "PAPER" },
  config,
  markets: [market("QQQ", qqq, "31844210"), market("SPY", spy, "48211903")],
  risk: {
    decision_id: "d-88f1",
    proposal_id: "p-51c0",
    verdict: "ALLOW",
    reasons: ["within_limits", "score_above_level_minimum", "liquidity_ok"],
    level: 1,
    risk_multiplier: "0.75",
    base_risk_usd: "10",
    allowed_risk_usd: "7.50",
    candidate_worst_case_loss_usd: "7.12",
    protected_profit_floor_usd: "41.08",
    live_trading_allowed: false,
    asset: "QQQ",
    decided_at: iso(46),
  },
  pnl: {
    equity: "100082.17",
    broker_account: {
      provider: "alpaca",
      environment: "paper",
      account_label: "PA…DEMO",
      currency: "USD",
      equity: "100123.37",
      cash: "98170.40",
      buying_power: "396400.00",
      unrealized_pnl: "41.20",
      synced_at: iso(9),
    },
    realized_net_pnl: "82.17",
    unrealized_pnl: "41.20",
    peak_realized_pnl: "96.40",
    peak_total_pnl: "131.02",
    fees: "4.88",
    losing_streak: 0,
    cash_equity: "100082.17",
    account_high_water_mark: "100120.00",
    marked_equity: "100123.37",
    session_date: DEMO_NOW.toISOString().slice(0, 10),
    live_stopped: false,
    stop_reason: "session_within_limits",
  },
  session: { action: "CONTINUE", reasons: ["session_within_limits"] },
  reconciliation: { status: "RECONCILIATION_OK", safe_mode: false, mismatches: [], checked_at: iso(9) },
  protection_recovery: { ok: true, safe_mode: false, open_positions: 1, protected_positions: 1, unprotected_position_ids: [], reason: null },
  pnl_history: pnlHistory(),
  proposals: [
    {
      proposal_id: "p-51c0", asset: "QQQ", side: "buy", entry_price: "481.18", stop_price: "478.30", target_price: "486.94",
      quantity: "2.35", expected_r: "2", signal_score: "84.6", confirmation_categories: ["technical", "regime", "liquidity", "news"],
      invalidations: ["fast_sma_below_slow_sma", "protective_stop_reached"], expected_net_value_usd: "6.42",
      why_now: "Tendencia y momentum alineados con datos frescos; régimen alcista confirmado.", why_not_trade: [], is_a_plus: true, created_at: iso(46),
    },
    {
      proposal_id: "p-51b9", asset: "QQQ", side: "buy", entry_price: "480.12", stop_price: "477.60", target_price: "485.16",
      quantity: "2.10", expected_r: "2", signal_score: "76.1", confirmation_categories: ["technical", "regime", "liquidity"],
      invalidations: ["regime_shift"], expected_net_value_usd: "2.10",
      why_now: "Ruptura de rango con volumen por encima de la media.", why_not_trade: [], is_a_plus: false, created_at: iso(95),
    },
    {
      proposal_id: "p-5188", asset: "QQQ", side: "buy", entry_price: "481.40", stop_price: "479.60", target_price: "484.10",
      quantity: "1.90", expected_r: "1.5", signal_score: "68.2", confirmation_categories: ["technical"],
      invalidations: ["spread_too_wide"], expected_net_value_usd: "0.41",
      why_now: "Rebote en media de 20 periodos.", why_not_trade: ["score_below_level_minimum"], is_a_plus: false, created_at: iso(310),
    },
  ],
  orders: [
    { order_id: "o-7f3a", mode: "paper", asset: "QQQ", side: "buy", requested_quantity: "2.35", filled_quantity: "2.35", status: "FILLED", protective_stop_active: true, created_at: iso(45) },
    { order_id: "o-b82e", mode: "paper", asset: "QQQ", side: "buy", requested_quantity: "2.10", filled_quantity: "0", status: "UNKNOWN", protective_stop_active: false, created_at: iso(94) },
    { order_id: "o-2c44", mode: "paper", asset: "QQQ", side: "sell", requested_quantity: "2.00", filled_quantity: "2.00", status: "FILLED", protective_stop_active: false, created_at: iso(181) },
  ],
  fills: [
    { fill_id: "f-1", asset: "QQQ", side: "buy", price: "481.18", quantity: "2.35", fee: "1.12", created_at: iso(45) },
    { fill_id: "f-2", asset: "QQQ", side: "sell", price: "483.72", quantity: "2.00", fee: "0.95", created_at: iso(181) },
  ],
  positions: [
    { position_id: "pos-qqq-01", asset: "QQQ", side: "long", quantity: "2.35", entry_price: "481.18", mark_price: qqq[qqq.length - 1]!.close, stop_price: "478.30", unrealized_pnl: "41.20", status: "OPEN", opened_at: iso(45) },
  ],
  position_history: [],
  alerts: [
    { alert_id: "a-1", severity: "WARNING", source: "social-official-api", message: "External intelligence collection degraded", created_at: iso(14), acknowledged: false },
    { alert_id: "a-2", severity: "CRITICAL", source: "operations", message: "Operation requires recovery before new entries", created_at: iso(12), acknowledged: false },
  ],
  shadow_trades: [
    { proposal_id: "p-5188", asset: "QQQ", outcome: "CLOSED", entry_price: "481.40", exit_price: "483.44", net_pnl_usd: "3.92", exit_reason: "target", created_at: iso(300) },
    { proposal_id: "p-5160", asset: "QQQ", outcome: "CLOSED", entry_price: "479.02", exit_price: "477.55", net_pnl_usd: "-4.11", exit_reason: "stop", created_at: iso(500) },
  ],
  change_proposals: [],
  experiments: [
    { experiment_id: "e-walk-3", status: "COMPLETED", period: "walk-forward OOS", net_pnl: "146.20", max_drawdown: "38.40", created_at: iso(60 * 20) },
    { experiment_id: "e-base-2", status: "COMPLETED", period: "baseline replay", net_pnl: "92.75", max_drawdown: "51.10", created_at: iso(60 * 44) },
  ],
  trade_evaluations: [],
  operations,
  unresolved_operations: 1,
  learning_metrics: {
    sample_size: 42, wins: 24, losses: 18, win_rate_percent: "57.14", expectancy_usd: "3.48", profit_factor: "1.62",
    max_drawdown_usd: "58.20", fees_usd: "41.30", slippage_usd: "12.84", cost_drag_percent: "18.6",
  },
  audit: [],
  decision_trace: [
    { id: "t-5", type: "agent_runs", asset: null, status: "FAILED", created_at: iso(14), decision_id: null, proposal_id: null, agent_id: "social", provider: "anthropic", model: "claude-sonnet-5", error_code: "provider_timeout", detail: "provider_timeout" },
    { id: "t-1", type: "risk_decisions", asset: "QQQ", status: "ALLOW", created_at: iso(46), decision_id: "d-88f1", proposal_id: "p-51c0", agent_id: null, provider: null, model: null, error_code: null, detail: "within_limits" },
    { id: "t-2", type: "critic_reviews", asset: "QQQ", status: "APPROVE", created_at: iso(46.2), decision_id: null, proposal_id: "p-51c0", agent_id: "critic", provider: null, model: null, error_code: null, detail: "No contradicting evidence above threshold" },
    { id: "t-3", type: "trade_proposals", asset: "QQQ", status: "PROPOSED", created_at: iso(46.5), decision_id: null, proposal_id: "p-51c0", agent_id: "strategy", provider: "anthropic", model: "claude-sonnet-5", error_code: null, detail: "Tendencia y momentum alineados" },
    { id: "t-4", type: "risk_decisions", asset: "QQQ", status: "DENY", created_at: iso(310), decision_id: "d-87c0", proposal_id: "p-5188", agent_id: null, provider: null, model: null, error_code: null, detail: "score_below_level_minimum" },
  ],
  agents,
  providers,
  usage: { calls_today: 46, input_tokens: 182_400, output_tokens: 21_930, records: [] },
  observability: { agent_runs_total: 96, agent_failures: 2, provider_fallbacks_today: 1, source_runs_total: 288, alerts_open: 2, database: "ok", runtime_metrics: [] },
  sources,
  source_runs: [],
  agent_memory: [],
};

export const demoSetup: SetupStatus = { configured: true, missing: [], local_config_exists: true, mode: "paper", live_trading: false };
export const firstRunSetup: SetupStatus = { configured: false, missing: ["broker_credentials"], local_config_exists: false, mode: "paper", live_trading: false };

export const demoReadiness: Readiness = {
  generated_at: DEMO_NOW.toISOString(),
  overall: "PAPER_READY",
  live_authorized: false,
  checks: [
    { check_id: "paper_mode", label: "PAPER mode", status: "PASS", detail: "PAPER is active and LIVE is disabled.", evidence: [] },
    { check_id: "long_only_session", label: "QQQ long-only, regular session", status: "PASS", detail: "QQQ only, long-only, regular session, flat overnight, no options.", evidence: [] },
    { check_id: "database", label: "Control-plane database", status: "PASS", detail: "ok", evidence: [] },
    { check_id: "provider_gateway", label: "Single provider gateway", status: "PASS", detail: "One primary and ordered unique fallbacks use ModelRouter.", evidence: [] },
    { check_id: "provider_accounts", label: "Selected subscription accounts", status: "PASS", detail: "Primary subscription authenticated.", evidence: [] },
    { check_id: "protective_recovery", label: "Protective-stop recovery", status: "PASS", detail: "Open positions have deterministic protection.", evidence: [] },
    { check_id: "broker_mutations", label: "Live broker orders", status: "GATED", detail: "Only paper brokers exist; no live broker adapter or live credential profile is present.", evidence: [] },
    { check_id: "live_authorization", label: "LIVE authorization", status: "GATED", detail: "Independent readiness review and explicit confirm-live approval are still required.", evidence: [] },
  ],
};

export const demoMemory: MemoryStatus = {
  health: {
    overall: "DEGRADED",
    components: [
      { component: "working_memory", status: "DEGRADED", detail: "in-process: single process, not shared" },
      { component: "historical_db", status: "HEALTHY", detail: "sqlite" },
      { component: "pgvector", status: "DEGRADED", detail: "SQLite: semantic search off" },
      { component: "embeddings", status: "HEALTHY", detail: "local model present; hashes checked on load" },
      { component: "vault", status: "HEALTHY", detail: "data/knowledge" },
    ],
  },
  overview: {
    knowledge: { ACTIVE: 18, NEEDS_REVALIDATION: 3 },
    candidates: { PENDING: 4 },
    conflicts: 1,
    created_last_7d: 6,
    created_last_30d: 21,
    retrieval_latency_ms: 0.9,
    last_cycle_at: iso(60 * 6),
    recent_lessons: [],
    agents: [],
  },
};

export const demoKnowledge: KnowledgeItem[] = [
  {
    id: "k-1", knowledge_id: "k-1", title: "Rupturas con spread > 15 bps fallan más", summary: "En QQQ, las rupturas con spread observado superior a 15 bps tuvieron expectativa negativa en 30 días.",
    category: "LESSON", symbol: "QQQ", strategy: "breakout", market_regime: "range", confidence: 0.72, reliability: 0.68, importance: 0.8, status: "ACTIVE",
    valid_from: iso(60 * 24 * 20), valid_until: null, last_validated_at: iso(60 * 30), validation_count: 4, successful_uses: 9, failed_uses: 2, created_at: iso(60 * 24 * 20), updated_at: iso(60 * 30),
  },
  {
    id: "k-2", knowledge_id: "k-2", title: "Momentum en régimen alcista: stop 0.6%", summary: "El stop protector al 0.6% redujo el MAE promedio sin bajar la tasa de acierto.",
    category: "PATTERN", symbol: null, strategy: "trend_pullback", market_regime: "bull", confidence: 0.61, reliability: 0.55, importance: 0.6, status: "NEEDS_REVALIDATION",
    valid_from: iso(60 * 24 * 40), valid_until: null, last_validated_at: iso(60 * 24 * 12), validation_count: 2, successful_uses: 5, failed_uses: 3, created_at: iso(60 * 24 * 40), updated_at: iso(60 * 24 * 12),
  },
];

export const demoVault: VaultOverview = {
  root_exists: true,
  notes: [
    { path: "00-System/Knowledge Map.md", name: "Knowledge Map", title: "Knowledge Map", folder: "00-System", kind: "map", status: null, type: null, has_owner_notes: false },
    { path: "03-Markets/QQQ.md", name: "QQQ", title: "Mercado: QQQ", folder: "03-Markets", kind: "index", status: null, type: null, has_owner_notes: false },
    { path: "04-Regimes/range.md", name: "range", title: "Régimen: range", folder: "04-Regimes", kind: "index", status: null, type: null, has_owner_notes: false },
    { path: "04-Regimes/bull.md", name: "bull", title: "Régimen: bull", folder: "04-Regimes", kind: "index", status: null, type: null, has_owner_notes: false },
    { path: "06-Daily-Lessons/k-1.md", name: "k-1", title: "Rupturas con spread > 15 bps fallan más", folder: "06-Daily-Lessons", kind: "knowledge", status: "active", type: "lesson", has_owner_notes: true },
    { path: "07-Patterns/k-2.md", name: "k-2", title: "Momentum en régimen alcista: stop 0.6%", folder: "07-Patterns", kind: "knowledge", status: "needs_revalidation", type: "pattern", has_owner_notes: false },
  ],
  edges: [
    { source: "00-System/Knowledge Map.md", target: "03-Markets/QQQ.md" },
    { source: "00-System/Knowledge Map.md", target: "04-Regimes/range.md" },
    { source: "00-System/Knowledge Map.md", target: "04-Regimes/bull.md" },
    { source: "06-Daily-Lessons/k-1.md", target: "03-Markets/QQQ.md" },
    { source: "06-Daily-Lessons/k-1.md", target: "04-Regimes/range.md" },
    { source: "06-Daily-Lessons/k-1.md", target: "07-Patterns/k-2.md" },
    { source: "07-Patterns/k-2.md", target: "04-Regimes/bull.md" },
    { source: "03-Markets/QQQ.md", target: "06-Daily-Lessons/k-1.md" },
  ],
};

export const demoVaultNotes: Record<string, VaultNote> = {
  "06-Daily-Lessons/k-1.md": {
    path: "06-Daily-Lessons/k-1.md",
    name: "k-1",
    title: "Rupturas con spread > 15 bps fallan más",
    frontmatter: { type: "lesson", status: "active", symbol: "QQQ", reliability: 0.68, confidence: 0.72, version: 4 },
    body:
      "# Rupturas con spread > 15 bps fallan más\n\n> En QQQ, las rupturas con spread observado superior a 15 bps tuvieron expectativa negativa en 30 días.\n\n" +
      "## Evidencia\n\n- 11 operaciones evaluadas, 9 con **MAE** superior al stop previsto.\n- El spread medio en la entrada fue de `18.4 bps`.\n\n" +
      "## Condiciones\n\n- Régimen lateral\n- Liquidez por debajo de 2M USD\n\nContradice a [[k-2]] en régimen alcista.",
    owner_notes: "Confirmado revisando el historial de órdenes. Revisar de nuevo si cambia la estructura de comisiones.",
    editable: true,
    links: [
      { name: "QQQ", path: "03-Markets/QQQ.md" },
      { name: "range", path: "04-Regimes/range.md" },
      { name: "k-2", path: "07-Patterns/k-2.md" },
      { name: "Knowledge Map", path: "00-System/Knowledge Map.md" },
    ],
    backlinks: [{ path: "03-Markets/QQQ.md", title: "Mercado: QQQ" }],
  },
  "07-Patterns/k-2.md": {
    path: "07-Patterns/k-2.md",
    name: "k-2",
    title: "Momentum en régimen alcista: stop 0.6%",
    frontmatter: { type: "pattern", status: "needs_revalidation", reliability: 0.55, version: 2 },
    body: "# Momentum en régimen alcista: stop 0.6%\n\n> El stop protector al 0.6% redujo el MAE promedio sin bajar la tasa de acierto.",
    owner_notes: "",
    editable: true,
    links: [
      { name: "bull", path: "04-Regimes/bull.md" },
      { name: "Knowledge Map", path: "00-System/Knowledge Map.md" },
    ],
    backlinks: [{ path: "06-Daily-Lessons/k-1.md", title: "Rupturas con spread > 15 bps fallan más" }],
  },
  "03-Markets/QQQ.md": {
    path: "03-Markets/QQQ.md",
    name: "QQQ",
    title: "Mercado: QQQ",
    frontmatter: { tags: ["hyverion/index"] },
    body: "# Mercado: QQQ\n\n_Nota generada por Hyverion desde la base de datos; se regenera en cada ciclo._\n\n## ACTIVE\n\n- [[k-1|Rupturas con spread > 15 bps fallan más]] · LESSON · fiabilidad 0.68\n\n[[Knowledge Map]]",
    owner_notes: "",
    editable: false,
    links: [
      { name: "k-1", path: "06-Daily-Lessons/k-1.md" },
      { name: "Knowledge Map", path: "00-System/Knowledge Map.md" },
    ],
    backlinks: [{ path: "06-Daily-Lessons/k-1.md", title: "Rupturas con spread > 15 bps fallan más" }],
  },
};

export const demoCandidates: MemoryCandidate[] = [
  { id: "cand-7f3a91c2", memory_type: "HYPOTHESIS", title: "Rupturas de QQQ en la primera media hora fallan con volumen bajo", agent_id: "strategy", symbol: "QQQ", market_regime: "range", confidence: "0.58", status: "PENDING", created_at: iso(5) },
  { id: "cand-1b44e0d9", memory_type: "LESSON", title: "El crítico acierta al vetar entradas con spread > 12 bps", agent_id: "critic", symbol: null, market_regime: null, confidence: "0.71", status: "VALIDATING", created_at: iso(26) },
];

export const demoConflicts: MemoryConflict[] = [
  { id: "conf-2c9d", memory_a_id: "mem-8812ab34", memory_b_id: "mem-44cd90ef", conflict_type: "opposing_claims", resolution: null, resolved_by: null, created_at: iso(30) },
];

export const demoAiModels: AiModels = {
  primary_provider: "anthropic",
  roles: [
    { role: "analysis", label: "Análisis" },
    { role: "decision", label: "Decisión" },
    { role: "improvement", label: "Mejora" },
  ],
  models: { analysis: "claude-sonnet-5", decision: "claude-opus-5-5", improvement: "claude-opus-5-5" },
  catalog: {
    anthropic: [
      { id: "claude-sonnet-5", label: "Sonnet 5" },
      { id: "claude-opus-5-5", label: "Opus 5.5" },
      { id: "claude-fable-5-1", label: "Fable 5.1" },
      { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
      { id: "default", label: "Predeterminado de Claude Code" },
    ],
    openai: [{ id: "default", label: "Predeterminado de Codex" }],
    xai: [{ id: "default", label: "Predeterminado de Grok" }],
    gemini: [{ id: "default", label: "Predeterminado de Gemini" }],
  },
};

const inHours = (hours: number) => new Date(Date.parse("2026-09-28T14:00:00Z") + hours * 3_600_000).toISOString();

export const demoAiUsage: AiUsage = {
  primary: null,
  providers: [
    { provider_id: "anthropic", available: true, session_used_percent: "41", session_resets_at: "Sep 28 at 9:49am (America/New_York)", weekly_used_percent: "62", weekly_resets_at: "Oct 1 at 6:59pm (America/New_York)", source: "claude /usage", detail: "", checked_at: "2026-09-28T14:00:00Z", exhausted: false },
    { provider_id: "openai", available: true, session_used_percent: "3", session_resets_at: inHours(4), weekly_used_percent: "47", weekly_resets_at: inHours(80), source: "codex session log", detail: "", checked_at: "2026-09-28T14:00:00Z", exhausted: false },
    { provider_id: "xai", available: false, session_used_percent: null, session_resets_at: null, weekly_used_percent: null, weekly_resets_at: null, source: "unavailable", detail: "provider_publishes_no_limits", checked_at: "2026-09-28T14:00:00Z", exhausted: false },
  ],
};
demoAiUsage.primary = demoAiUsage.providers[0]!;
