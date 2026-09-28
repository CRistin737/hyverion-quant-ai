import type { DailyReport, DataSourceStatus, Intelligence, LiveReadiness, MacroEventView, PeriodReport, PeriodTrade } from "../types";
import { DEMO_NOW } from "./demo";

const at = (minutes: number) => new Date(DEMO_NOW.getTime() + minutes * 60_000).toISOString();

function event(minutes: number, type: string, title: string, importance: MacroEventView["importance"], speaker: string | null = null): MacroEventView {
  return { event_id: `${type}-${minutes}`, event_type: type, title, scheduled_at: at(minutes), time_known: true, importance, source: type.startsWith("FED") || type.startsWith("FOMC") ? "fed" : "bls", speaker, minutes_to_event: minutes };
}

export const demoIntelligence: Intelligence = {
  as_of: DEMO_NOW.toISOString(),
  macro: {
    gate: null,
    gate_event: null,
    calendar_refreshed_at: at(-180),
    upcoming: [
      event(38, "FED_SPEECH", "Discurso: Governor Christopher J. Waller — Economic Outlook", "MEDIUM", "Governor Christopher J. Waller"),
      event(60 * 24 + 30, "PCE", "Personal Income and Outlays, August 2026", "HIGH"),
      event(60 * 24 * 3 + 30, "NFP", "Employment Situation", "HIGH"),
      event(60 * 24 * 5 + 240, "FED_SPEECH", "Discurso: Chair — Monetary Policy Outlook", "HIGH", "Chair"),
      event(60 * 24 * 12 + 30, "CPI", "Consumer Price Index", "HIGH"),
      event(60 * 24 * 26 + 240, "FOMC_DECISION", "Decisión de tipos del FOMC", "HIGH"),
    ],
  },
  breadth: {
    as_of: DEMO_NOW.toISOString(),
    components: 101,
    coverage_weight: "0.994",
    advancers: 58,
    decliners: 41,
    pct_green: "0.574",
    weighted_breadth: "0.712",
    top10_breadth: "0.81",
    ex_top10_breadth: "0.538",
    pct_above_vwap: "0.61",
    dispersion: "0.0094",
    estimated_index_change: "0.0041",
    top10_contribution_share: "0.64",
    leaders: [
      { symbol: "NVDA", weight: "0.091", change: "0.0182", contribution_bps: "16.6", above_vwap: true },
      { symbol: "MSFT", weight: "0.084", change: "0.0071", contribution_bps: "6.0", above_vwap: true },
      { symbol: "AVGO", weight: "0.052", change: "0.0096", contribution_bps: "5.0", above_vwap: true },
    ],
    laggards: [
      { symbol: "TSLA", weight: "0.031", change: "-0.0142", contribution_bps: "-4.4", above_vwap: false },
      { symbol: "AMZN", weight: "0.055", change: "-0.0035", contribution_bps: "-1.9", above_vwap: false },
    ],
    megacaps: [
      { symbol: "NVDA", weight: "0.091", change: "0.0182", contribution_bps: "16.6", above_vwap: true },
      { symbol: "MSFT", weight: "0.084", change: "0.0071", contribution_bps: "6.0", above_vwap: true },
      { symbol: "AAPL", weight: "0.078", change: "0.0021", contribution_bps: "1.6", above_vwap: true },
      { symbol: "AMZN", weight: "0.055", change: "-0.0035", contribution_bps: "-1.9", above_vwap: false },
      { symbol: "AVGO", weight: "0.052", change: "0.0096", contribution_bps: "5.0", above_vwap: true },
      { symbol: "META", weight: "0.041", change: "0.0044", contribution_bps: "1.8", above_vwap: true },
      { symbol: "GOOGL", weight: "0.029", change: "0.0012", contribution_bps: "0.3", above_vwap: true },
      { symbol: "GOOG", weight: "0.028", change: "0.0011", contribution_bps: "0.3", above_vwap: true },
      { symbol: "TSLA", weight: "0.031", change: "-0.0142", contribution_bps: "-4.4", above_vwap: false },
      { symbol: "COST", weight: "0.024", change: "0.0005", contribution_bps: "0.1", above_vwap: true },
    ],
    divergences: [],
  },
  rates: {
    status: "ok",
    error: null,
    series: [
      { series_id: "DGS2", label: "Tipo a 2 años", as_of: "2026-09-24", value: "3.62", change_1d: "-0.03", change_5d: "-0.08" },
      { series_id: "DGS10", label: "Tipo a 10 años", as_of: "2026-09-24", value: "4.11", change_1d: "0.01", change_5d: "-0.04" },
      { series_id: "T10Y2Y", label: "Pendiente 10a-2a", as_of: "2026-09-25", value: "0.49", change_1d: "0.04", change_5d: "0.04" },
      { series_id: "VIXCLS", label: "VIX (cierre)", as_of: "2026-09-24", value: "15.67", change_1d: "-0.24", change_5d: "0.9" },
    ],
  },
  volatility: {
    status: "ok",
    realized_vol_annual: "0.162",
    atm_iv: "0.178",
    next_atm_iv: "0.184",
    term_slope: "0.006",
    skew_25d: "0.041",
    expected_move_pct: "0.0122",
    near_expiry: "2026-09-28",
    vix_close: "15.67",
  },
  news: [
    { cluster_id: "news:1", headline: "Nvidia raises data-center guidance ahead of product event", symbols: ["NVDA"], sources: ["benzinga"], first_published_at: at(-42), materiality: "HIGH", sentiment: 1, qqq_relevance: "0.091", copies: 4 },
    { cluster_id: "news:2", headline: "Tesla shares slip after delivery estimate cut", symbols: ["TSLA"], sources: ["benzinga"], first_published_at: at(-95), materiality: "MEDIUM", sentiment: -1, qqq_relevance: "0.031", copies: 2 },
  ],
  filings: [
    { symbol: "MSFT", form: "8-K", accession: "0000789019-26-000101", filed_at: at(-600), is_earnings_release: false, url: "https://www.sec.gov/", items: ["8.01"] },
  ],
  earnings: [
    { symbol: "MU", date: "2026-09-29", session: "after_close", eps_estimate: "2.84" },
    { symbol: "COST", date: "2026-10-01", session: "after_close", eps_estimate: "5.10" },
  ],
};

function source(id: string, name: string, category: string, purpose: string, key: string | null, state: DataSourceStatus["state"], minutesAgo: number | null, error: string | null = null): DataSourceStatus {
  return {
    id,
    name,
    category,
    access: "api",
    hosts: [],
    key,
    purpose,
    terms_url: "https://example.gov",
    terms_checked_at: "2026-09-27",
    trust: category === "OFFICIAL_REGULATOR" ? 1 : 0.8,
    key_present: state !== "key_missing",
    state,
    last_run_at: minutesAgo === null ? null : at(-minutesAgo),
    last_error: error,
    last_records: null,
  };
}

export const demoSources: DataSourceStatus[] = [
  source("alpaca-market-data", "Alpaca Market Data (IEX)", "LICENSED_WIRE", "Precios, barras y cotizaciones de QQQ y de los componentes del Nasdaq-100.", "broker:alpaca_paper:key_id", "ok", 1),
  source("alpaca-news", "Alpaca News (Benzinga)", "LICENSED_WIRE", "Noticias de QQQ y de sus componentes.", "broker:alpaca_paper:key_id", "ok", 4),
  source("sec-edgar", "SEC EDGAR", "OFFICIAL_REGULATOR", "Presentaciones 8-K/10-Q/10-K de los componentes y cartera trimestral oficial de QQQ (N-PORT).", "data:contact_email", "ok", 22),
  source("fed-calendar", "Reserva Federal · calendario", "OFFICIAL_REGULATOR", "Reuniones y ruedas de prensa del FOMC, actas, Beige Book, discursos y comparecencias.", null, "ok", 180),
  source("bls-schedule", "BLS · calendario de publicaciones", "OFFICIAL_REGULATOR", "Fechas y horas de CPI, PPI, empleo (NFP), JOLTS.", "data:contact_email", "ok", 180),
  source("fred", "FRED (Fed de San Luis)", "OFFICIAL_REGULATOR", "Tipos a 2 y 10 años, pendiente de la curva, dólar amplio y VIX (cierre diario).", "data:fred:api_key", "key_missing", null),
  source("finnhub-earnings", "Finnhub · calendario de resultados", "AGGREGATOR", "Fechas de resultados de los componentes del Nasdaq-100 (antes o después del mercado).", "data:finnhub:api_key", "key_missing", null),
];

export const demoDailyReport: DailyReport = {
  session: DEMO_NOW.toISOString().slice(0, 10),
  account_profile: "alpaca_paper_100k · PA…DEMO",
  broker_paper_pnl_usd: "82.17",
  adjusted_simulated_pnl_usd: "79.40",
  fees_usd: "4.88",
  entries: 2,
  rejected: 7,
  rejection_reasons: { opening_protection_window: 3, macro_event_pre_block: 2, critic_rejected: 1, max_trades_per_day_reached: 1 },
  shadow_trades: 6,
  shadow_pnl_usd: "-3.10",
  by_regime: { TRENDING_UP: 5, RANGING: 4 },
  by_time_bucket: { OPENING: 3, MIDDAY: 4, POWER_HOUR: 2 },
  ai_cost_usd: "0.84",
  realized_slippage_bps_mean: "0.80",
  modelled_slippage_bps_mean: "2.50",
  notes: ["El paper llenó mejor que el modelo de costes: el PnL ajustado es el prudente."],
};

export const demoLiveReadiness: LiveReadiness = {
  verdict: "NOT_READY",
  live_enabled: false,
  note: "Informe solamente. Esta versión no puede activar LIVE; aunque todo pasara, hace falta la revisión humana y una versión futura con esa activación explícita.",
  items: [
    { item: "Datos suficientes", status: "FAIL", evidence: "6 sesiones registradas (mínimo 40)" },
    { item: "Calidad del backtest", status: "FAIL", evidence: "último replay: trend_pullback · not_profitable" },
    { item: "Fuera de muestra", status: "FAIL", evidence: "6 operaciones fuera de muestra, PnL -2.28" },
    { item: "Walk-forward", status: "FAIL", evidence: "2 de 4 tramos positivos" },
    { item: "Periodo en paper", status: "FAIL", evidence: "6 sesiones en paper" },
    { item: "Coherencia con shadow", status: "PENDING", evidence: "Se revisa en el informe diario (shadow vs real)" },
    { item: "Fiabilidad de ejecución", status: "PASS", evidence: "0 operaciones sin resolver" },
    { item: "Conciliación del broker", status: "PASS", evidence: "última conciliación: correcta" },
    { item: "Cero errores críticos", status: "PASS", evidence: "0 eventos críticos recientes" },
    { item: "Interruptores de riesgo", status: "PASS", evidence: "topes USD, puerta macro, cierre antes del final, stop nativo" },
    { item: "Revisión humana", status: "PENDING", evidence: "Siempre pendiente: LIVE es una decisión manual del dueño" },
  ],
};

const tradeAt = (day: number, pnl: string, fee = "1.02"): PeriodTrade => ({
  position_id: `demo-${day}-${pnl}`,
  asset: "QQQ",
  side: "buy",
  opened_at: `2026-09-${String(day).padStart(2, "0")}T14:05:00Z`,
  closed_at: `2026-09-${String(day).padStart(2, "0")}T15:40:00Z`,
  quantity: "40",
  entry_price: "478.20",
  exit_price: (478.2 + Number(pnl) / 40).toFixed(2),
  stop_price: "475.80",
  target_price: "483.00",
  exit_reason: Number(pnl) > 0 ? "target" : "stop",
  fees_usd: fee,
  net_pnl_usd: pnl,
});

const demoTradesSeptember = [tradeAt(2, "412.50"), tradeAt(3, "-238.10"), tradeAt(8, "305.00"), tradeAt(9, "-241.60"), tradeAt(15, "498.20"), tradeAt(16, "-236.00"), tradeAt(22, "180.40"), tradeAt(24, "352.75")];

export const demoPeriodReport: PeriodReport = {
  start: "2026-09-01T04:00:00+00:00",
  end: "2026-10-01T03:59:59.999999+00:00",
  equity_start: "100000.00",
  equity_end: "101033.00",
  equity_change_usd: "1033.00",
  return_percent: "1.03",
  net_pnl_usd: "1033.15",
  fees_usd: "8.16",
  trades: 8,
  wins: 5,
  losses: 3,
  win_rate_percent: "62.50",
  profit_factor: "2.44",
  average_win_usd: "349.77",
  average_loss_usd: "-238.57",
  expectancy_usd: "129.14",
  best_trade_usd: "498.20",
  worst_trade_usd: "-241.60",
  max_drawdown_usd: "241.60",
  max_drawdown_percent: "0.24",
  days: [
    { day: "2026-09-02", net_pnl_usd: "412.50", equity: "100412.50" },
    { day: "2026-09-03", net_pnl_usd: "-238.10", equity: "100174.40" },
    { day: "2026-09-08", net_pnl_usd: "305.00", equity: "100479.40" },
    { day: "2026-09-09", net_pnl_usd: "-241.60", equity: "100237.80" },
    { day: "2026-09-15", net_pnl_usd: "498.20", equity: "100736.00" },
    { day: "2026-09-16", net_pnl_usd: "-236.00", equity: "100500.00" },
    { day: "2026-09-22", net_pnl_usd: "180.40", equity: "100680.40" },
    { day: "2026-09-24", net_pnl_usd: "352.75", equity: "101033.00" },
  ],
  trade_rows: demoTradesSeptember,
};
