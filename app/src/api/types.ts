/**
 * Contract types for the control API (`src/trading_bot/control_api.py`).
 *
 * Money, prices and quantities are Decimal values serialised as strings by the
 * backend. They stay strings here: never parse them to float for arithmetic.
 * Top-level snapshot keys are pinned by `snapshot-keys.json`, which a Python
 * contract test compares against `build_snapshot()`.
 */

export type Dec = string;
export type ISODate = string;

export interface PublicConfig {
  mode: "paper" | "live" | string;
  asset_class: string;
  /** The one executable instrument (QQQ). */
  primary_instrument: string;
  live_trading: boolean;
  shadow_trading: boolean;
  allowed_symbols: string[];
  operating_region: string | null;
  broker: "simulator" | "alpaca" | string;
  broker_environment: "paper" | string;
  market_data_provider: string;
  market_data_feed: string;
  ai_provider: string;
  ai_auth_mode: string;
  ai_fallback_providers: string[];
  ai_models: AiRoleModels;
  risk_profiles: Record<"conservador" | "medio" | "alto", RiskProfileValues>;
  autonomy: AutonomySettings;
  external_data: {
    news: string;
    social: string;
    news_feeds: Record<string, string>;
    news_reviewed_sources: string[];
    collection_interval_seconds: number;
    collection_max_backoff_seconds: number;
  };
  risk: RiskLimits;
  strategies: { enabled: string[]; signal_weights_version: string; signal_weights: Record<string, Dec> };
}

export type RiskProfileName = "conservador" | "medio" | "alto" | "personalizado";

export interface RiskProfileValues {
  base_risk_percent: Dec;
  daily_loss_percent: Dec;
  weekly_loss_percent: Dec;
  max_account_drawdown_percent: Dec;
}

export interface AutonomySettings {
  mode: "paper_autonomous" | "supervised";
  auto_promote_paper: boolean;
  shadow_sessions_before_promotion: number;
  envelope: Record<string, { minimum: Dec; maximum: Dec }>;
}

export interface LadderLevel {
  level: number;
  /** Percent of broker equity. */
  minimum_pnl_percent: Dec;
  maximum_pnl_percent: Dec;
  risk_multiplier: Dec;
  minimum_score: number;
  minimum_confirmations: number;
  protected_fraction: Dec;
}

export interface RiskLimits {
  profile: RiskProfileName;
  base_risk_percent: Dec;
  /** Optional dollar caps: null in PAPER (percent of equity only). */
  max_base_risk_usd: Dec | null;
  daily_loss_percent: Dec;
  daily_loss_hard_cap_usd: Dec | null;
  weekly_loss_percent: Dec;
  weekly_loss_hard_cap_usd: Dec | null;
  max_profit_giveback_percent: Dec;
  max_positions: number;
  max_total_exposure_percent: Dec;
  max_asset_exposure_percent: Dec;
  max_correlated_risk_percent: Dec;
  max_spread_bps: Dec;
  max_slippage_bps: Dec;
  min_liquidity_usd: Dec;
  max_losing_streak: number;
  cooldown_minutes_after_loss: number;
  max_account_drawdown_percent: Dec;
  daily_profit_hard_cap_usd: Dec | null;
  max_trades_per_day: number;
  opening_no_trade_minutes: number;
  eod_flatten_minutes_before_close: number;
  max_position_hold_minutes: number;
  max_entry_deviation_bps: Dec;
  max_decision_age_seconds: number;
  ladder: LadderLevel[];
}

export interface MarketTick {
  symbol: string;
  bid: Dec;
  ask: Dec;
  last: Dec;
  /** Regular-session shares and dollar value traded so far. */
  session_volume: Dec;
  session_dollar_volume: Dec;
  event_time: ISODate;
  provider?: string;
  feed?: string | null;
  is_delayed?: boolean;
}

/** `GET /api/v1/broker/status`: read-only paper broker view (account label is masked). */
export interface BrokerStatus {
  provider: "simulator" | "alpaca" | string;
  environment: "paper" | string;
  connected: boolean;
  detail: string;
  capabilities: Record<string, unknown> | null;
  account: { account_label: string; currency: string; status: string; equity: Dec; cash: Dec; buying_power: Dec } | null;
  positions: Array<{ symbol: string; quantity: Dec; average_entry_price: Dec; market_value: Dec; unrealized_pnl: Dec }>;
}

/** `GET /api/v1/market/session`: New York session from the NYSE calendar. */
export interface MarketSession {
  state: "CLOSED" | "PRE_MARKET" | "OPENING" | "REGULAR" | "MIDDAY" | "POWER_HOUR" | "CLOSING" | "AFTER_HOURS";
  is_open: boolean;
  trading_day: string;
  is_session_day: boolean;
  open_at: ISODate | null;
  close_at: ISODate | null;
  early_close: boolean;
  next_open: ISODate;
  next_close: ISODate;
  minutes_since_open: number | null;
  minutes_to_close: number | null;
  time_bucket: string;
  timezone: string;
}

export interface Candle {
  symbol: string;
  interval: string;
  open: Dec;
  high: Dec;
  low: Dec;
  close: Dec;
  volume: Dec;
  vwap?: Dec | null;
  event_time: ISODate;
}

export interface Market extends MarketTick {
  history: MarketTick[];
  candles: Candle[];
}

export interface RiskDecision {
  decision_id?: string;
  proposal_id?: string;
  verdict?: "ALLOW" | "DENY" | "REDUCE" | string;
  reasons?: string[];
  level?: number;
  risk_multiplier?: Dec;
  base_risk_usd?: Dec;
  allowed_risk_usd?: Dec;
  candidate_worst_case_loss_usd?: Dec;
  protected_profit_floor_usd?: Dec;
  live_trading_allowed?: boolean;
  asset?: string;
  decided_at?: ISODate;
}

/** Last sync of the broker account: the only source of equity (no configured capital). */
export interface BrokerAccountSync {
  provider: string;
  environment: "paper" | string;
  account_label: string;
  currency: string;
  equity: Dec;
  cash: Dec;
  buying_power: Dec;
  unrealized_pnl: Dec;
  synced_at: ISODate;
}

export interface Pnl {
  /** Broker equity as of the last sync; null until the broker was reconciled once. */
  equity: Dec | null;
  broker_account?: BrokerAccountSync | null;
  realized_net_pnl: Dec;
  unrealized_pnl: Dec;
  peak_realized_pnl: Dec;
  peak_total_pnl: Dec;
  fees: Dec;
  losing_streak: number;
  cash_equity: Dec;
  account_high_water_mark: Dec;
  marked_equity: Dec;
  session_date?: string;
  live_stopped?: boolean;
  stop_reason?: string;
}

export interface PnlDay {
  session_date: string;
  realized_net_pnl: Dec;
  unrealized_pnl: Dec;
  fees: Dec;
  losing_streak: number;
  live_stopped: boolean;
  stop_reason?: string;
}

export interface Reconciliation {
  status: string;
  safe_mode: boolean;
  mismatches: string[];
  checked_at: ISODate | null;
}

export interface ProtectionRecovery {
  ok: boolean;
  safe_mode: boolean;
  open_positions: number;
  protected_positions: number;
  unprotected_position_ids: string[];
  reason: string | null;
}

export interface Proposal {
  proposal_id: string;
  asset: string;
  side: "buy" | "sell" | string;
  entry_price: Dec;
  stop_price: Dec;
  target_price: Dec;
  quantity: Dec;
  expected_r: Dec;
  signal_score: Dec;
  confirmation_categories: string[];
  invalidations: string[];
  expected_net_value_usd: Dec;
  why_now: string;
  why_not_trade: string[];
  is_a_plus: boolean;
  created_at: ISODate;
}

export interface Order {
  order_id: string;
  mode: string;
  asset: string;
  side: string;
  requested_quantity: Dec;
  filled_quantity: Dec;
  status: string;
  protective_stop_active?: boolean;
  created_at: ISODate;
}

export interface Fill {
  fill_id?: string;
  asset?: string;
  side?: string;
  price?: Dec;
  quantity?: Dec;
  fee?: Dec;
  created_at?: ISODate;
  [key: string]: unknown;
}

export interface Position {
  position_id?: string;
  id?: string;
  asset: string;
  side?: string;
  quantity: Dec;
  entry_price: Dec;
  mark_price?: Dec;
  stop_price?: Dec;
  unrealized_pnl?: Dec;
  status?: string;
  opened_at?: ISODate;
  created_at?: ISODate;
  [key: string]: unknown;
}

export interface Alert {
  alert_id: string;
  severity: "INFO" | "WARNING" | "CRITICAL";
  source: string;
  message: string;
  created_at: ISODate;
  acknowledged: boolean;
}

export interface ShadowTrade {
  proposal_id: string;
  asset: string;
  outcome: string;
  entry_price: Dec;
  exit_price: Dec | null;
  net_pnl_usd: Dec;
  exit_reason: string | null;
  created_at: ISODate;
}

export interface Experiment {
  experiment_id: string;
  status: string;
  period: string;
  net_pnl: Dec;
  max_drawdown: Dec;
  aggregate?: Record<string, unknown>;
  assumptions?: string[];
  created_at: ISODate;
}

export type OperationState =
  | "PROPOSED" | "CRITIC_REVIEWED" | "RISK_APPROVED" | "EXECUTION_PENDING" | "SUBMITTED"
  | "ACKNOWLEDGED" | "PARTIALLY_FILLED" | "OPEN" | "EXIT_PENDING" | "CLOSING" | "CLOSED"
  | "RECONCILED" | "EVALUATED" | "REJECTED" | "CANCEL_PENDING" | "CANCELED" | "UNKNOWN"
  | "SAFE_MODE" | "RECOVERY_REQUIRED";

/** States whose venue outcome is unproven; each blocks new entries until resolved. */
export const UNRESOLVED_STATES: ReadonlySet<OperationState> = new Set([
  "UNKNOWN",
  "SAFE_MODE",
  "RECOVERY_REQUIRED",
]);

export interface Operation {
  id: string;
  proposal_id: string;
  asset: string;
  mode: string;
  state: OperationState;
  client_order_id: string | null;
  position_id: string | null;
  created_at: ISODate;
  updated_at: ISODate;
}

export interface OperationEvent {
  id: string;
  operation_id: string;
  sequence: number;
  from_state: OperationState | null;
  to_state: OperationState;
  requested_state: OperationState;
  legal: boolean;
  reason: string;
  details: Record<string, unknown>;
  occurred_at: ISODate;
}

/** Operator-verified resolution targets accepted by POST /operations/{id}/resolve. */
export type ResolutionTarget = "REJECTED" | "CANCELED" | "OPEN" | "CLOSED";

export interface LearningMetrics {
  sample_size: number;
  wins: number;
  losses: number;
  win_rate_percent: Dec;
  expectancy_usd: Dec;
  profit_factor: Dec | null;
  max_drawdown_usd: Dec;
  fees_usd: Dec;
  slippage_usd: Dec;
  cost_drag_percent: Dec | null;
}

export interface AuditRow {
  created_at: ISODate;
  type: string;
  asset: string | null;
  status: string;
  detail: string;
}

export interface TraceRow {
  id: string;
  type: string;
  asset: string | null;
  status: string | null;
  created_at: ISODate;
  decision_id: string | null;
  proposal_id: string | null;
  agent_id: string | null;
  provider: string | null;
  model: string | null;
  error_code: string | null;
  detail: string;
}

export interface Agent {
  agent_id: string;
  role: string;
  objective: string;
  version: string;
  spec_hash: string;
  status: "IDLE" | "ACTIVE" | "ERROR" | string;
  last_run_at: ISODate | null;
  last_error: string | null;
  provider?: string | null;
  model?: string | null;
  latency_ms?: number | null;
}

export interface Provider {
  provider_id: string;
  display_name: string;
  api_configured: boolean;
  official_login_available: boolean;
  auth_state: string;
  billing_mode: string;
  usage_state: string;
  context_window: number | null;
  detail: string;
  subscription?: Record<string, unknown>;
  /** Set while the engine has paused this subscription (quota, login, rate limit). */
  circuit?: { state: "open" | "half_open"; failures: number; last_code: string | null; retry_at: ISODate };
  [key: string]: unknown;
}

/** AI calls today. Statistics only: subscriptions have no USD budget. */
export interface Usage {
  calls_today: number;
  input_tokens: number;
  output_tokens: number;
  records: Array<Record<string, unknown>>;
}

export interface Observability {
  agent_runs_total: number;
  agent_failures: number;
  provider_fallbacks_today: number;
  source_runs_total: number;
  alerts_open: number;
  database: string;
  runtime_metrics: Array<{ name: string; value: string; labels?: Record<string, string> }>;
}

export interface Source {
  source_id: string;
  category: string;
  enabled: boolean;
  allowlisted: boolean;
  compliance_status: string;
  freshness_seconds: number | null;
  last_success_at: ISODate | null;
  last_error: string | null;
  records_today: number;
  detail: string;
  recovery_action: string;
}

export interface AgentMemory {
  agent_id: string;
  version: string;
  summary: string;
  evidence_count: number;
  outcome: string;
  recorded_at: ISODate;
}

export type EngineState = "stopped" | "running" | "external" | "exited";

export interface EngineStatus {
  state: EngineState;
  pid: number | null;
  started_at: ISODate | null;
  stopped_at: ISODate | null;
  exit_code: number | null;
  interval_seconds: number | null;
  mode: "PAPER";
  /** The operator asked to close every position; entries stay blocked until flat. */
  flatten_pending?: boolean;
}

export type LoginState = "idle" | "starting" | "waiting_browser" | "verifying" | "connected" | "failed" | "manual";

export interface LoginSession {
  provider_id: string;
  state: LoginState;
  url: string | null;
  detail: string;
  manual_command: string | null;
  started_at: ISODate;
  finished_at: ISODate | null;
  timeout_seconds: number;
}

export interface Snapshot {
  generated_at: ISODate;
  engine: EngineStatus;
  config: PublicConfig;
  markets: Market[];
  risk: RiskDecision;
  pnl: Pnl;
  session: { action: string; reasons: string[] };
  reconciliation: Reconciliation;
  protection_recovery: ProtectionRecovery;
  pnl_history: PnlDay[];
  proposals: Proposal[];
  orders: Order[];
  fills: Fill[];
  positions: Position[];
  position_history: Position[];
  alerts: Alert[];
  shadow_trades: ShadowTrade[];
  change_proposals: Array<Record<string, unknown>>;
  experiments: Experiment[];
  trade_evaluations: Array<Record<string, unknown>>;
  operations: Operation[];
  unresolved_operations: number;
  learning_metrics: LearningMetrics;
  audit: AuditRow[];
  decision_trace: TraceRow[];
  agents: Agent[];
  providers: Provider[];
  usage: Usage;
  observability: Observability;
  sources: Source[];
  source_runs: Array<Record<string, unknown>>;
  agent_memory: AgentMemory[];
}

export interface SetupStatus {
  configured: boolean;
  missing: string[];
  local_config_exists: boolean;
  mode: string;
  live_trading: boolean;
}

export interface ConfigPatch {
  broker_provider?: "alpaca";
  model_analysis?: string;
  model_decision?: string;
  model_improvement?: string;
  primary_provider?: string;
  primary_auth_mode?: "api" | "subscription";
  fallback_providers?: string[];
  news_provider?: string;
  news_feeds?: Record<string, string>;
  news_reviewed_sources?: string[];
  collection_interval_seconds?: number;
  collection_max_backoff_seconds?: number;
  social_provider?: string;
  risk_profile?: "conservador" | "medio" | "alto";
  base_risk_percent?: Dec;
  daily_loss_percent?: Dec;
  weekly_loss_percent?: Dec;
  max_account_drawdown_percent?: Dec;
  max_profit_giveback_percent?: Dec;
  max_trades_per_day?: number;
  opening_no_trade_minutes?: number;
  eod_flatten_minutes_before_close?: number;
  max_position_hold_minutes?: number;
  operating_region?: string;
}

export interface ConfigValidation {
  valid: boolean;
  errors: string[];
  changed_fields: string[];
}

export interface ConfigApplyResult {
  applied: boolean;
  requires_restart: boolean;
  changed_fields: string[];
  backup_path: string | null;
  detail: string;
}

export interface ReadinessCheck {
  check_id: string;
  label: string;
  status: "PASS" | "INFO" | "GATED" | "FAIL";
  detail: string;
  evidence: string[];
}

export interface Readiness {
  generated_at: ISODate;
  overall: string;
  live_authorized: boolean;
  checks: ReadinessCheck[];
}

export interface MemoryHealthComponent {
  component: string;
  status: "HEALTHY" | "DEGRADED" | "FAILED" | string;
  detail: string;
}

export interface MemoryStatus {
  health: { overall: "HEALTHY" | "DEGRADED" | "FAILED" | string; components: MemoryHealthComponent[] };
  overview: {
    knowledge: Record<string, number>;
    candidates: Record<string, number>;
    conflicts: number;
    created_last_7d: number;
    created_last_30d: number;
    retrieval_latency_ms: number;
    last_cycle_at: ISODate | null;
    recent_lessons: Array<{ id: string; title?: string; summary?: string; created_at?: ISODate }>;
    agents: Array<Record<string, unknown>>;
  };
}

export interface KnowledgeItem {
  id: string;
  knowledge_id: string;
  title: string;
  summary: string;
  category: string;
  symbol: string | null;
  strategy: string | null;
  market_regime: string | null;
  confidence: number;
  reliability: number;
  importance: number;
  status: string;
  valid_from: ISODate;
  valid_until: ISODate | null;
  last_validated_at: ISODate | null;
  validation_count: number;
  successful_uses: number;
  failed_uses: number;
  created_at: ISODate;
  updated_at: ISODate;
}

export interface ChangeProposal {
  proposal_id?: string;
  id?: string;
  title?: string;
  status: string;
  target?: string;
  rationale?: string;
  created_at?: ISODate;
  [key: string]: unknown;
}

export interface VaultNoteSummary {
  path: string;
  name: string;
  title: string;
  folder: string;
  kind: "knowledge" | "index" | "map";
  status: string | null;
  type: string | null;
  has_owner_notes: boolean;
}

export interface VaultOverview {
  root_exists: boolean;
  notes: VaultNoteSummary[];
  edges: Array<{ source: string; target: string }>;
}

export interface VaultNote {
  path: string;
  name: string;
  title: string;
  frontmatter: Record<string, unknown>;
  body: string;
  owner_notes: string;
  editable: boolean;
  links: Array<{ name: string; path: string | null }>;
  backlinks: Array<{ path: string; title: string }>;
}

export interface MemoryCandidate {
  id: string;
  memory_type: string;
  title: string;
  agent_id: string | null;
  symbol: string | null;
  market_regime: string | null;
  confidence: number | string;
  status: "PENDING" | "VALIDATING" | string;
  created_at: ISODate;
}

export interface MemoryConflict {
  id: string;
  memory_a_id: string;
  memory_b_id: string;
  conflict_type: string;
  resolution: string | null;
  resolved_by: string | null;
  created_at: ISODate;
}

export interface ReplayStats {
  trades: number;
  wins: number;
  net_pnl_usd: Dec;
  fees_usd: Dec;
  max_drawdown_usd: Dec;
  exits: Record<string, number>;
}

export type TradeView = "open" | "closed" | "attention";

/** One row per trade: lifecycle + latest position projection + fills (`/api/v1/trades`). */
export interface Trade {
  id: string;
  operation_id: string | null;
  position_id: string | null;
  asset: string;
  side: string | null;
  mode: string | null;
  state: OperationState | null;
  position_status: string | null;
  view: TradeView;
  needs_attention: boolean;
  protected: boolean;
  quantity: Dec | null;
  entry_price: Dec | null;
  current_price: Dec | null;
  exit_price: Dec | null;
  stop_price: Dec | null;
  target_price: Dec | null;
  pnl_usd: Dec | null;
  fees_usd: Dec;
  exit_reason: string | null;
  opened_at: ISODate | null;
  closed_at: ISODate | null;
  updated_at: ISODate | null;
  fills: Fill[];
}

/** Simulated order with its fills (`/api/v1/orders`). */
export interface OrderRow extends Order {
  client_order_id?: string;
  fills?: Fill[];
}

/** Entry stopped by the risk engine, with what it would have made (`/api/v1/orders/rejected`). */
export interface RejectedEntry {
  operation_id: string;
  proposal_id: string | null;
  asset: string;
  reasons: string[];
  rejected_at: ISODate | null;
  would_have_net_pnl_usd: Dec | null;
  would_have_outcome: string | null;
}

export type ChartInterval = "1m" | "5m" | "15m" | "1h" | "4h" | "1d";

export interface StrategyParams {
  stop_percent: Dec;
  reward_multiple: Dec;
  horizon_minutes: number;
  blocked_regimes: string[];
}

/** Active version of one agent or strategy (`/api/v1/components`). */
export interface ComponentVersion {
  component_id: string;
  kind: "agent" | "strategy";
  version: string;
  params: StrategyParams | null;
  active_from: ISODate;
  overridden: boolean;
}

export interface VersionResults {
  samples: number;
  correct: number;
  accuracy_percent: Dec | null;
  net_pnl_usd: Dec | null;
}

export interface ReviewStep {
  status: string;
  from_status: string | null;
  reason: string | null;
  at: ISODate;
}

/** One version in a component's history (`/api/v1/components/{id}/history`). */
export interface VersionEntry {
  version: string;
  kind: "agent" | "strategy";
  active_from: ISODate;
  active_to: ISODate | null;
  active: boolean;
  reason: string | null;
  params: StrategyParams | null;
  proposal: {
    id: string;
    reason: string;
    affected_rules: string[];
    expected_improvement: string;
    risk: string;
    status: string;
    history: ReviewStep[];
  } | null;
  results: VersionResults;
}

export interface ChangeExperiment {
  experiment_id: string;
  symbols: string[];
  minutes: number;
  before: ReplayStats;
  after: ReplayStats;
  champion_params: StrategyParams;
  challenger_params: StrategyParams;
}

export type ChangeStatus =
  | "PROPOSED" | "TESTING" | "READY_FOR_REVIEW" | "APPROVED" | "REJECTED" | "DEPLOYED" | "ROLLED_BACK";

/** Latest state of a change proposal with its review history (`/api/v1/learning/timeline`). */
export interface ChangeItem {
  id: string;
  agent: string;
  current_version: string;
  candidate_version: string;
  reason: string;
  evidence: string[];
  affected_rules: string[];
  expected_improvement: string;
  risk: string;
  candidate_spec: Record<string, unknown>;
  created_at: ISODate;
  status: ChangeStatus;
  history: ReviewStep[];
  updated_at: ISODate;
  experiment?: ChangeExperiment;
}

export interface OptimizerResult {
  strategy_id: string;
  outcome: "proposed" | "not_proven" | "no_better_candidate" | "pending_review";
  reasons?: string[];
  proposal_id?: string;
  experiment_id?: string;
  changes?: string[];
  before?: ReplayStats;
  after?: ReplayStats;
}

export interface OptimizerStatus {
  running: boolean;
  error: string | null;
  last_run: { symbols: string[]; results: OptimizerResult[]; proposed: number; created_at: ISODate } | null;
}

export interface AgentSpec {
  agent_id: string;
  version: string;
  spec_markdown: string;
}

/** `GET /api/v1/sources/status`: one free data source (no secret values). */
export interface DataSourceStatus {
  id: string;
  name: string;
  category: string;
  access: "api" | "feed" | "page";
  hosts: string[];
  key: string | null;
  purpose: string;
  terms_url: string;
  terms_checked_at: string;
  trust: number;
  key_present: boolean;
  state: "ok" | "error" | "never_run" | "key_missing";
  last_run_at: ISODate | null;
  last_error: string | null;
  last_records: number | null;
}

export interface MacroEventView {
  event_id: string;
  event_type: string;
  title: string;
  scheduled_at: ISODate;
  time_known: boolean;
  importance: "HIGH" | "MEDIUM" | "LOW";
  source: string;
  speaker: string | null;
  minutes_to_event: number;
}

export interface NewsClusterView {
  cluster_id: string;
  headline: string;
  symbols: string[];
  sources: string[];
  first_published_at: ISODate;
  materiality: "HIGH" | "MEDIUM" | "LOW";
  sentiment: number;
  qqq_relevance: Dec;
  copies: number;
}

export interface BreadthView {
  as_of: ISODate;
  components: number;
  coverage_weight: Dec;
  advancers: number;
  decliners: number;
  pct_green: Dec;
  weighted_breadth: Dec;
  top10_breadth: Dec;
  ex_top10_breadth: Dec;
  pct_above_vwap: Dec | null;
  dispersion: Dec;
  estimated_index_change: Dec;
  top10_contribution_share: Dec;
  leaders: Array<{ symbol: string; weight: Dec; change: Dec; contribution_bps: Dec; above_vwap: boolean | null }>;
  laggards: Array<{ symbol: string; weight: Dec; change: Dec; contribution_bps: Dec; above_vwap: boolean | null }>;
  megacaps: Array<{ symbol: string; weight: Dec; change: Dec; contribution_bps: Dec; above_vwap: boolean | null }>;
  divergences: string[];
}

/** `GET /api/v1/intelligence`: stored sensors (never fetched by opening the app). */
export interface Intelligence {
  as_of: ISODate;
  macro: { gate: string | null; gate_event: MacroEventView | null; calendar_refreshed_at: ISODate | null; upcoming: MacroEventView[] };
  breadth: BreadthView | null;
  rates: { status: string; series: Array<{ series_id: string; label: string; as_of: string; value: Dec; change_1d: Dec | null; change_5d: Dec | null }>; error: string | null } | null;
  volatility: {
    status: string;
    realized_vol_annual: Dec | null;
    atm_iv: Dec | null;
    next_atm_iv: Dec | null;
    term_slope: Dec | null;
    skew_25d: Dec | null;
    expected_move_pct: Dec | null;
    near_expiry: string | null;
    vix_close: Dec | null;
  } | null;
  news: NewsClusterView[];
  filings: Array<{ symbol: string; form: string; accession: string; filed_at: ISODate; is_earnings_release: boolean; url: string; items: string[] }>;
  earnings: Array<{ symbol: string; date: string; session: string; eps_estimate: Dec | null }>;
  errors?: Record<string, string>;
}

/** `GET /api/v1/reports/daily`: paper campaign day (§98). Paper and adjusted PnL stay apart. */
export interface DailyReport {
  session: string;
  account_profile: string | null;
  broker_paper_pnl_usd: Dec;
  adjusted_simulated_pnl_usd: Dec;
  fees_usd: Dec;
  entries: number;
  rejected: number;
  rejection_reasons: Record<string, number>;
  shadow_trades: number;
  shadow_pnl_usd: Dec;
  by_regime: Record<string, number>;
  by_time_bucket: Record<string, number>;
  ai_cost_usd: Dec;
  realized_slippage_bps_mean: Dec | null;
  modelled_slippage_bps_mean: Dec | null;
  notes: string[];
}

/** `GET /api/v1/reports/live-readiness`: §105 checklist. Always NOT_READY in this release. */
export interface LiveReadiness {
  verdict: "NOT_READY";
  live_enabled: false;
  items: Array<{ item: string; status: "PASS" | "FAIL" | "PENDING"; evidence: string }>;
  note: string;
}

/** Model per role for the primary subscription; null = the CLI default. */
export interface AiRoleModels {
  analysis: string | null;
  decision: string | null;
  improvement: string | null;
}

export type AiRole = keyof AiRoleModels;

/** `GET /api/v1/ai/models`. */
export interface AiModels {
  primary_provider: string;
  roles: Array<{ role: AiRole; label: string }>;
  models: Record<AiRole, string>;
  catalog: Record<string, Array<{ id: string; label: string }>>;
}

/** 5-hour window and weekly limit of one subscription (official sources only). */
export interface SubscriptionUsage {
  provider_id: string;
  available: boolean;
  session_used_percent: Dec | null;
  session_resets_at: string | null;
  weekly_used_percent: Dec | null;
  weekly_resets_at: string | null;
  source: string;
  detail: string;
  checked_at: ISODate;
  exhausted: boolean;
}

/** `GET /api/v1/ai/usage`. */
export interface AiUsage {
  primary: SubscriptionUsage | null;
  providers: SubscriptionUsage[];
}

/** `GET /api/v1/service`: the engine as a background (launchd) service. */
export interface ServiceStatus {
  installed: boolean;
  loaded: boolean;
  pid: number | null;
  plist_path: string;
  log_path: string;
  detail: string;
}

export interface PeriodTrade {
  position_id: string;
  asset: string;
  side: string | null;
  opened_at: string | null;
  closed_at: string;
  quantity: string | null;
  entry_price: string | null;
  exit_price: string | null;
  stop_price: string | null;
  target_price: string | null;
  exit_reason: string | null;
  fees_usd: Dec;
  net_pnl_usd: Dec;
}

/** `GET /api/v1/reports/period`: business-style report for any period. */
export interface PeriodReport {
  start: string | null;
  end: string;
  equity_start: Dec | null;
  equity_end: Dec | null;
  equity_change_usd: Dec | null;
  return_percent: Dec | null;
  net_pnl_usd: Dec;
  fees_usd: Dec;
  trades: number;
  wins: number;
  losses: number;
  win_rate_percent: Dec | null;
  profit_factor: Dec | null;
  average_win_usd: Dec | null;
  average_loss_usd: Dec | null;
  expectancy_usd: Dec | null;
  best_trade_usd: Dec | null;
  worst_trade_usd: Dec | null;
  max_drawdown_usd: Dec;
  max_drawdown_percent: Dec | null;
  days: Array<{ day: string; net_pnl_usd: Dec; equity: Dec | null }>;
  trade_rows: PeriodTrade[];
}
