/**
 * Demo data for the views added in the simplification: unified trades, orders,
 * risk rejections, chart candles, component versions and the change inbox.
 * Deterministic (fixed clock, seeded noise) so screenshots stay stable.
 */

import type {
  Candle,
  ChangeItem,
  ChartInterval,
  ComponentVersion,
  MarketSession,
  OptimizerStatus,
  OrderRow,
  RejectedEntry,
  ReplayStats,
  StrategyParams,
  Trade,
  VersionEntry,
} from "../types";
import { DEMO_NOW } from "./demo";

const iso = (minutesAgo: number) => new Date(DEMO_NOW.getTime() - minutesAgo * 60_000).toISOString();

const INTERVAL_MINUTES: Record<ChartInterval, number> = { "1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440 };

function prng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 2 ** 32;
  };
}

/** Candles for any interval, ending at the demo clock and at the snapshot's last price. */
export function demoCandles(symbol: string, interval: ChartInterval, limit: number, last: number): Candle[] {
  const step = INTERVAL_MINUTES[interval];
  const base = symbol === "QQQ" ? 0.35 : 0.45;
  const vol = base * Math.sqrt(step);
  const rand = prng((symbol === "QQQ" ? 7 : 11) * 31 + step);
  // Walk backwards from the current price so every interval ends where the market is.
  const closes: number[] = [last];
  for (let i = 1; i < limit; i += 1) closes.unshift(Math.max(1, closes[0]! - (rand() - 0.5) * vol));
  return closes.map((close, index) => {
    const open = index === 0 ? close - (rand() - 0.5) * vol : closes[index - 1]!;
    const high = Math.max(open, close) + rand() * vol * 0.5;
    const low = Math.min(open, close) - rand() * vol * 0.5;
    return {
      symbol,
      interval,
      open: open.toFixed(2),
      high: high.toFixed(2),
      low: low.toFixed(2),
      close: close.toFixed(2),
      volume: ((20000 + rand() * 80000) * step).toFixed(0),
      event_time: iso((limit - 1 - index) * step),
    };
  });
}

function trade(partial: Partial<Trade> & Pick<Trade, "id" | "asset" | "view">): Trade {
  return {
    operation_id: partial.id,
    position_id: null,
    side: "BUY",
    mode: "paper",
    state: null,
    position_status: null,
    needs_attention: partial.view === "attention",
    protected: partial.view === "open",
    quantity: null,
    entry_price: null,
    current_price: null,
    exit_price: null,
    stop_price: null,
    target_price: null,
    pnl_usd: null,
    fees_usd: "0",
    exit_reason: null,
    opened_at: null,
    closed_at: null,
    updated_at: null,
    fills: [],
    ...partial,
  };
}

export function demoTrades(qqqLast: string): Trade[] {
  return [
    trade({
      id: "op-7f3a91c2", asset: "QQQ", view: "open", state: "OPEN", position_id: "pos-qqq-01", position_status: "OPEN",
      quantity: "2.35", entry_price: "481.18", current_price: qqqLast, stop_price: "478.30", target_price: "486.94",
      pnl_usd: "41.20", fees_usd: "1.12", opened_at: iso(45), updated_at: iso(1),
      fills: [{ fill_id: "f-1", price: "481.18", quantity: "2.35", fee_usd: "1.12", filled_at: iso(45), side: "BUY" }],
    }),
    trade({
      id: "op-b82e0d17", asset: "QQQ", view: "attention", state: "RECOVERY_REQUIRED",
      quantity: "2.10", entry_price: "480.12", stop_price: "477.60", target_price: "485.16", opened_at: iso(95), updated_at: iso(12),
    }),
    trade({
      id: "op-2c44e8aa", asset: "QQQ", view: "closed", state: "EVALUATED", position_id: "pos-qqq-00", position_status: "CLOSED",
      quantity: "2.00", entry_price: "480.51", exit_price: "483.72", stop_price: "477.60", target_price: "486.30",
      pnl_usd: "4.72", fees_usd: "1.88", exit_reason: "TIME_EXIT", opened_at: iso(260), closed_at: iso(181), updated_at: iso(180),
    }),
    trade({
      id: "op-4e6a02cd", asset: "QQQ", view: "closed", state: "CLOSED", position_id: "pos-qqq-99", position_status: "CLOSED",
      quantity: "1.80", entry_price: "482.90", exit_price: "479.70", stop_price: "479.70", target_price: "489.30",
      pnl_usd: "-7.44", fees_usd: "1.74", exit_reason: "protective_stop_reached", opened_at: iso(420), closed_at: iso(355), updated_at: iso(355),
    }),
    trade({
      id: "op-5d21aa04", asset: "QQQ", view: "closed", state: "EVALUATED", position_id: "pos-qqq-07", position_status: "CLOSED",
      quantity: "2.40", entry_price: "476.20", exit_price: "483.60", stop_price: "472.60", target_price: "483.40",
      pnl_usd: "16.93", fees_usd: "1.87", exit_reason: "target_reached", opened_at: iso(60 * 26), closed_at: iso(60 * 25), updated_at: iso(60 * 25),
    }),
    trade({
      id: "op-3a90cc61", asset: "QQQ", view: "closed", state: "EVALUATED", position_id: "pos-qqq-98", position_status: "CLOSED",
      quantity: "2.00", entry_price: "474.10", exit_price: "474.52", stop_price: "469.40", target_price: "483.50",
      pnl_usd: "-0.73", fees_usd: "1.56", exit_reason: "TIME_EXIT", opened_at: iso(60 * 50), closed_at: iso(60 * 49), updated_at: iso(60 * 49),
    }),
  ];
}

export const demoOrders: OrderRow[] = [
  {
    order_id: "o-7f3a", client_order_id: "bot300-7f3a91c2", mode: "paper", asset: "QQQ", side: "BUY", requested_quantity: "2.35",
    filled_quantity: "2.35", status: "FILLED", protective_stop_active: true, created_at: iso(45),
    fills: [{ fill_id: "f-1", price: "481.18", quantity: "2.35", fee_usd: "1.12", filled_at: iso(45) }],
  },
  {
    order_id: "o-b82e", client_order_id: "bot300-b82e0d17", mode: "paper", asset: "QQQ", side: "BUY", requested_quantity: "2.10",
    filled_quantity: "0", status: "UNKNOWN", protective_stop_active: false, created_at: iso(94), fills: [],
  },
  {
    order_id: "o-2c44", client_order_id: "bot300-2c44e8aa-x", mode: "paper", asset: "QQQ", side: "SELL", requested_quantity: "2.00",
    filled_quantity: "2.00", status: "FILLED", protective_stop_active: false, created_at: iso(181),
    fills: [{ fill_id: "f-2", price: "483.72", quantity: "2.00", fee_usd: "0.95", filled_at: iso(181) }],
  },
  {
    order_id: "o-4e6a", client_order_id: "bot300-4e6a02cd-x", mode: "paper", asset: "QQQ", side: "SELL", requested_quantity: "1.80",
    filled_quantity: "1.80", status: "FILLED", protective_stop_active: false, created_at: iso(355),
    fills: [{ fill_id: "f-3", price: "479.70", quantity: "1.80", fee_usd: "0.87", filled_at: iso(355) }],
  },
  {
    order_id: "o-0a7b", client_order_id: "bot300-0a7bb219", mode: "paper", asset: "QQQ", side: "BUY", requested_quantity: "1.90",
    filled_quantity: "0", status: "CANCELED", protective_stop_active: false, created_at: iso(506), fills: [],
  },
];

export const demoRejected: RejectedEntry[] = [
  {
    operation_id: "op-91d0f3b5", proposal_id: "p-5188", asset: "QQQ", reasons: ["score_below_level_minimum"],
    rejected_at: iso(310), would_have_net_pnl_usd: "3.92", would_have_outcome: "CLOSED",
  },
  {
    operation_id: "op-61aa9f02", proposal_id: "p-5160", asset: "QQQ", reasons: ["spread_too_wide", "liquidity_below_minimum"],
    rejected_at: iso(500), would_have_net_pnl_usd: "-4.11", would_have_outcome: "CLOSED",
  },
];

const baseParams: StrategyParams = { stop_percent: "0.4", reward_multiple: "2", horizon_minutes: 60, blocked_regimes: [] };
const v11Params: StrategyParams = { ...baseParams, blocked_regimes: ["ranging"] };
const v12Params: StrategyParams = { ...v11Params, horizon_minutes: 120 };

function stats(trades: number, wins: number, net: string, fees: string): ReplayStats {
  return { trades, wins, net_pnl_usd: net, fees_usd: fees, max_drawdown_usd: net.replace("-", ""), exits: { horizon_expired: trades - wins, target: wins } };
}

/** Mutable learning state so approve/undo can be demonstrated offline. */
export function createLearningDemo(agentIds: string[]) {
  const strategyHistory: VersionEntry[] = [
    {
      version: "1.0.0", kind: "strategy", active_from: iso(60 * 24 * 12), active_to: iso(60 * 24 * 3), active: false,
      reason: "Versión inicial", params: baseParams, proposal: null,
      results: { samples: 42, correct: 7, accuracy_percent: "16.7", net_pnl_usd: "-26.00" },
    },
    {
      version: "1.1.0", kind: "strategy", active_from: iso(60 * 24 * 3), active_to: null, active: true,
      reason: "Aprobado tras revisar la prueba", params: v11Params,
      proposal: {
        id: "chg-11", reason: "Casi todas las entradas perdedoras ocurrían en mercado lateral, donde una estrategia de tendencia no tiene ventaja.",
        affected_rules: ["No operar en mercado lateral"], expected_improvement: "Pasar de -$26.00 a -$4.10.",
        risk: "Opera menos veces; en otro periodo el resultado puede ser distinto.", status: "DEPLOYED",
        history: [
          { status: "PROPOSED", from_status: null, reason: null, at: iso(60 * 24 * 4) },
          { status: "TESTING", from_status: "PROPOSED", reason: "Prueba automática con precios reales", at: iso(60 * 24 * 4) },
          { status: "READY_FOR_REVIEW", from_status: "TESTING", reason: "Mejora demostrada fuera de muestra", at: iso(60 * 24 * 4) },
          { status: "APPROVED", from_status: "READY_FOR_REVIEW", reason: "Aprobado tras revisar la prueba", at: iso(60 * 24 * 3) },
          { status: "DEPLOYED", from_status: "APPROVED", reason: "Aprobado tras revisar la prueba", at: iso(60 * 24 * 3) },
        ],
      },
      results: { samples: 12, correct: 4, accuracy_percent: "33.3", net_pnl_usd: "-2.10" },
    },
  ];
  const histories: Record<string, VersionEntry[]> = { trend_pullback: strategyHistory };

  const changes: ChangeItem[] = [
    {
      id: "chg-12", agent: "trend_pullback", current_version: "1.1.0", candidate_version: "1.2.0",
      reason: "Con precios reales de los últimos 14 días, este ajuste gana más por operación que la versión actual, sin una caída peor, y lo confirma con datos que no se usaron para elegirlo.",
      evidence: ["experiment:exp-12", "20 sesiones de velas de 1 minuto de QQQ.", "El mejor ajuste se eligió con el 70 % inicial y se validó con el 30 % final."],
      affected_rules: ["Cierre por tiempo a 120 min (antes 60 min)"],
      expected_improvement: "Pasar de -$4.10 en 58 operaciones (acierto 31 %) a $5.62 en 54 operaciones (acierto 39 %).",
      risk: "Cambia cuándo cierra las operaciones; en otro periodo puede variar.",
      candidate_spec: { kind: "strategy_params", strategy_id: "trend_pullback", params: v12Params },
      created_at: iso(60 * 5), status: "READY_FOR_REVIEW", updated_at: iso(60 * 5),
      history: [
        { status: "PROPOSED", from_status: null, reason: null, at: iso(60 * 5) },
        { status: "TESTING", from_status: "PROPOSED", reason: "Prueba automática con precios reales", at: iso(60 * 5) },
        { status: "READY_FOR_REVIEW", from_status: "TESTING", reason: "Mejora demostrada fuera de muestra", at: iso(60 * 5) },
      ],
      experiment: {
        experiment_id: "exp-12", symbols: ["QQQ"], minutes: 7_800,
        before: stats(58, 18, "-4.10", "22.04"), after: stats(54, 21, "5.62", "20.52"),
        champion_params: v11Params, challenger_params: v12Params,
      },
    },
    {
      id: "chg-11", agent: "trend_pullback", current_version: "1.0.0", candidate_version: "1.1.0",
      reason: strategyHistory[1]!.proposal!.reason, evidence: ["experiment:exp-11"], affected_rules: ["No operar en mercado lateral"],
      expected_improvement: "Pasar de -$26.00 a -$4.10.", risk: "Opera menos veces; en otro periodo el resultado puede ser distinto.",
      candidate_spec: { kind: "strategy_params", strategy_id: "trend_pullback", params: v11Params },
      created_at: iso(60 * 24 * 4), status: "DEPLOYED", updated_at: iso(60 * 24 * 3), history: strategyHistory[1]!.proposal!.history,
    },
    {
      id: "chg-09", agent: "news", current_version: "1.0.0", candidate_version: "1.1.0",
      reason: "Pedir al agente de noticias que ignore titulares sin fuente primaria.", evidence: ["Revisión manual de 20 análisis"],
      affected_rules: ["RULES"], expected_improvement: "Menos señales falsas por rumores.", risk: "Puede tardar más en reaccionar.",
      candidate_spec: { kind: "agent_spec" }, created_at: iso(60 * 24 * 8), status: "REJECTED", updated_at: iso(60 * 24 * 7),
      history: [
        { status: "PROPOSED", from_status: null, reason: null, at: iso(60 * 24 * 8) },
        { status: "REJECTED", from_status: "PROPOSED", reason: "Sin datos suficientes para medirlo", at: iso(60 * 24 * 7) },
      ],
    },
  ];

  const agentHistory = (agentId: string): VersionEntry[] => [
    {
      version: "1.0.0", kind: "agent", active_from: iso(60 * 24 * 12), active_to: null, active: true, reason: "Versión inicial",
      params: null, proposal: null,
      results: agentId === "news" ? { samples: 18, correct: 11, accuracy_percent: "61.1", net_pnl_usd: null } : { samples: 0, correct: 0, accuracy_percent: null, net_pnl_usd: null },
    },
  ];

  const components = (): ComponentVersion[] => [
    ...agentIds.map((id) => ({ component_id: id, kind: "agent" as const, version: id === "optimizer" ? "1.1.0" : "1.0.0", params: null, active_from: iso(60 * 24 * 12), overridden: false })),
    ...Object.entries(histories).map(([id, entries]) => {
      const active = entries.find((entry) => entry.active)!;
      return { component_id: id, kind: "strategy" as const, version: active.version, params: active.params, active_from: active.active_from, overridden: false };
    }),
  ];

  const optimizer: OptimizerStatus = {
    running: false,
    error: null,
    last_run: {
      symbols: ["QQQ"], proposed: 1, created_at: iso(60 * 5),
      results: [{ strategy_id: "trend_pullback", outcome: "proposed", proposal_id: "chg-12", experiment_id: "exp-12" }],
    },
  };

  return {
    // Copies, so the query cache sees a new value after approve/undo (like a real response).
    changes: () => structuredClone(changes),
    components,
    history: (id: string) => structuredClone(histories[id] ?? (agentIds.includes(id) ? agentHistory(id) : null)),
    optimizer: () => optimizer,
    apply(id: string, reason: string) {
      const change = changes.find((item) => item.id === id);
      if (!change || !["READY_FOR_REVIEW", "APPROVED"].includes(change.status)) return null;
      const params = (change.candidate_spec as { params?: StrategyParams }).params ?? null;
      const now = DEMO_NOW.toISOString();
      change.status = "DEPLOYED";
      change.history = [
        ...change.history,
        { status: "APPROVED", from_status: "READY_FOR_REVIEW", reason, at: now },
        { status: "DEPLOYED", from_status: "APPROVED", reason, at: now },
      ];
      const history = (histories[change.agent] ??= []);
      history.forEach((entry) => {
        if (entry.active) Object.assign(entry, { active: false, active_to: now });
      });
      history.push({
        version: change.candidate_version, kind: "strategy", active_from: now, active_to: null, active: true, reason, params,
        proposal: { id: change.id, reason: change.reason, affected_rules: change.affected_rules, expected_improvement: change.expected_improvement, risk: change.risk, status: "DEPLOYED", history: change.history },
        results: { samples: 0, correct: 0, accuracy_percent: null, net_pnl_usd: null },
      });
      return { proposal_id: id, status: "DEPLOYED", component_id: change.agent, version: change.candidate_version };
    },
    reject(id: string, reason: string) {
      const change = changes.find((item) => item.id === id);
      if (!change) return null;
      change.history = [...change.history, { status: "REJECTED", from_status: change.status, reason, at: DEMO_NOW.toISOString() }];
      change.status = "REJECTED";
      return change;
    },
    rollback(componentId: string, reason: string) {
      const history = histories[componentId];
      if (!history || history.length < 2 || !history[history.length - 1]!.proposal) return null;
      const now = DEMO_NOW.toISOString();
      const undone = history[history.length - 1]!;
      const previous = history[history.length - 2]!;
      Object.assign(undone, { active: false, active_to: now });
      history.push({ ...previous, active: true, active_from: now, active_to: null, reason: `Deshacer: ${reason}`, proposal: null, results: { samples: 0, correct: 0, accuracy_percent: null, net_pnl_usd: null } });
      const change = changes.find((item) => item.id === undone.proposal?.id);
      if (change) {
        change.status = "ROLLED_BACK";
        change.history = [...change.history, { status: "ROLLED_BACK", from_status: "DEPLOYED", reason, at: now }];
      }
      return { component_id: componentId, undone_version: undone.version, active_version: previous.version };
    },
  };
}

/** Friday 10:02 New York time at the demo clock: regular session, morning bucket. */
export const demoMarketSession: MarketSession = {
  state: "REGULAR",
  is_open: true,
  trading_day: "2026-09-25",
  is_session_day: true,
  open_at: "2026-09-25T13:30:00+00:00",
  close_at: "2026-09-25T20:00:00+00:00",
  early_close: false,
  next_open: "2026-09-28T13:30:00+00:00",
  next_close: "2026-09-25T20:00:00+00:00",
  minutes_since_open: 32,
  minutes_to_close: 357,
  time_bucket: "MORNING",
  timezone: "America/New_York",
};
