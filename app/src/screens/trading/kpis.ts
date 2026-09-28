/**
 * KPI aggregation for Trading. Money stays Decimal-as-string (addDec); floats
 * are used only for ratios that are displayed as percentages.
 */

import type { OrderRow, RejectedEntry, Trade } from "@/api/types";
import { addDec, parseUTC, sign } from "@/lib/format";

const DAY = 86_400_000;

export interface TradeKpis {
  open: number;
  attention: number;
  openPnl: string;
  closed7d: number;
  closedPnl7d: string;
  wins7d: number;
  winRate7d: number | null;
}

export function tradeKpis(trades: Trade[], now: Date = new Date()): TradeKpis {
  const open = trades.filter((trade) => trade.view === "open");
  const recent = trades.filter((trade) => {
    if (trade.view !== "closed") return false;
    const closed = parseUTC(trade.closed_at ?? trade.updated_at);
    return closed !== null && now.getTime() - closed.getTime() <= 7 * DAY;
  });
  const wins = recent.filter((trade) => sign(trade.pnl_usd) > 0).length;
  return {
    open: open.length,
    attention: trades.filter((trade) => trade.view === "attention").length,
    openPnl: addDec(...open.map((trade) => trade.pnl_usd)),
    closed7d: recent.length,
    closedPnl7d: addDec(...recent.map((trade) => trade.pnl_usd)),
    wins7d: wins,
    winRate7d: recent.length ? (wins * 100) / recent.length : null,
  };
}

/** Where the price sits between stop (0) and target (1); null without both levels. */
export function stopTargetProgress(trade: Pick<Trade, "stop_price" | "target_price" | "current_price" | "exit_price">): number | null {
  const stop = Number(trade.stop_price);
  const target = Number(trade.target_price);
  const now = Number(trade.exit_price ?? trade.current_price);
  if (!Number.isFinite(stop) || !Number.isFinite(target) || !Number.isFinite(now) || stop === target || !trade.stop_price || !trade.target_price) return null;
  return Math.min(1, Math.max(0, (now - stop) / (target - stop)));
}

export function durationMinutes(trade: Pick<Trade, "opened_at" | "closed_at">, now: Date = new Date()): number | null {
  const start = parseUTC(trade.opened_at);
  if (!start) return null;
  const end = parseUTC(trade.closed_at) ?? now;
  return Math.max(0, Math.round((end.getTime() - start.getTime()) / 60_000));
}

export function durationText(minutes: number | null): string {
  if (minutes === null) return "—";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours < 48) return rest ? `${hours} h ${rest} min` : `${hours} h`;
  return `${Math.round(hours / 24)} días`;
}

export interface OrderFilters {
  asset: string;
  side: string;
  status: string;
  text: string;
  days: number | null;
}

export const NO_FILTERS: OrderFilters = { asset: "", side: "", status: "", text: "", days: null };

export function filterOrders(orders: OrderRow[], filters: OrderFilters, now: Date = new Date()): OrderRow[] {
  const text = filters.text.trim().toLowerCase();
  return orders.filter((order) => {
    if (filters.asset && order.asset !== filters.asset) return false;
    if (filters.side && order.side.toUpperCase() !== filters.side) return false;
    if (filters.status && order.status.toUpperCase() !== filters.status) return false;
    if (filters.days !== null) {
      const created = parseUTC(order.created_at);
      if (!created || now.getTime() - created.getTime() > filters.days * DAY) return false;
    }
    if (text && ![order.order_id, order.client_order_id ?? "", order.asset].some((value) => value.toLowerCase().includes(text))) return false;
    return true;
  });
}

export interface OrderKpis {
  total: number;
  filled: number;
  fillRate: number | null;
  fees: string;
  rejected: number;
  wouldHave: string;
}

export function orderKpis(orders: OrderRow[], rejected: RejectedEntry[]): OrderKpis {
  const filled = orders.filter((order) => order.status.toUpperCase() === "FILLED").length;
  const fees = addDec(...orders.flatMap((order) => (order.fills ?? []).map((fill) => (fill.fee_usd ?? fill.fee) as string | undefined)));
  return {
    total: orders.length,
    filled,
    fillRate: orders.length ? (filled * 100) / orders.length : null,
    fees,
    rejected: rejected.length,
    wouldHave: addDec(...rejected.map((entry) => entry.would_have_net_pnl_usd)),
  };
}
