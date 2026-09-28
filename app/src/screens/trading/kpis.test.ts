import { describe, expect, it } from "vitest";

import type { OrderRow, RejectedEntry, Trade } from "@/api/types";

import { durationText, filterOrders, NO_FILTERS, orderKpis, stopTargetProgress, tradeKpis } from "./kpis";

const NOW = new Date("2026-09-25T14:00:00Z");

function trade(partial: Partial<Trade>): Trade {
  return {
    id: "t", operation_id: null, position_id: null, asset: "QQQ", side: "BUY", mode: "paper", state: null, position_status: null,
    view: "closed", needs_attention: false, protected: false, quantity: null, entry_price: null, current_price: null, exit_price: null,
    stop_price: null, target_price: null, pnl_usd: null, fees_usd: "0", exit_reason: null, opened_at: null, closed_at: null, updated_at: null,
    fills: [], ...partial,
  };
}

describe("tradeKpis", () => {
  it("sums money as decimals and only counts the last 7 days of closed trades", () => {
    const kpis = tradeKpis(
      [
        trade({ view: "open", pnl_usd: "0.1" }),
        trade({ view: "open", pnl_usd: "0.2" }),
        trade({ view: "closed", pnl_usd: "5.50", closed_at: "2026-09-24T10:00:00Z" }),
        trade({ view: "closed", pnl_usd: "-2.25", closed_at: "2026-09-23T10:00:00Z" }),
        trade({ view: "closed", pnl_usd: "100", closed_at: "2026-09-01T10:00:00Z" }),
        trade({ view: "attention" }),
      ],
      NOW,
    );
    expect(kpis.openPnl).toBe("0.3");
    expect(kpis.closed7d).toBe(2);
    expect(kpis.closedPnl7d).toBe("3.25");
    expect(kpis.winRate7d).toBe(50);
    expect(kpis.attention).toBe(1);
  });
});

describe("stopTargetProgress", () => {
  it("places the price between stop (0) and target (1), clamped", () => {
    expect(stopTargetProgress({ stop_price: "90", target_price: "110", current_price: "100", exit_price: null })).toBe(0.5);
    expect(stopTargetProgress({ stop_price: "90", target_price: "110", current_price: "80", exit_price: null })).toBe(0);
    expect(stopTargetProgress({ stop_price: null, target_price: "110", current_price: "100", exit_price: null })).toBeNull();
  });
});

describe("durationText", () => {
  it("reads naturally", () => {
    expect(durationText(45)).toBe("45 min");
    expect(durationText(125)).toBe("2 h 5 min");
    expect(durationText(null)).toBe("—");
  });
});

const order = (partial: Partial<OrderRow>): OrderRow => ({
  order_id: "o", mode: "paper", asset: "QQQ", side: "BUY", requested_quantity: "1", filled_quantity: "1", status: "FILLED",
  created_at: "2026-09-25T10:00:00Z", fills: [], ...partial,
});

describe("orders", () => {
  const orders = [
    order({ order_id: "a", side: "BUY", fills: [{ fee_usd: "0.10" }] }),
    order({ order_id: "b", side: "SELL", asset: "SPY", status: "CANCELED", created_at: "2026-09-10T10:00:00Z" }),
    order({ order_id: "c", side: "SELL", fills: [{ fee_usd: "0.25" }] }),
  ];

  it("filters by side, asset, status, period and text", () => {
    expect(filterOrders(orders, { ...NO_FILTERS, side: "SELL" }, NOW).map((o) => o.order_id)).toEqual(["b", "c"]);
    expect(filterOrders(orders, { ...NO_FILTERS, asset: "SPY" }, NOW).map((o) => o.order_id)).toEqual(["b"]);
    expect(filterOrders(orders, { ...NO_FILTERS, days: 7 }, NOW).map((o) => o.order_id)).toEqual(["a", "c"]);
    expect(filterOrders(orders, { ...NO_FILTERS, text: "spy" }, NOW).map((o) => o.order_id)).toEqual(["b"]);
  });

  it("aggregates fees and what rejected entries would have made", () => {
    const rejected: RejectedEntry[] = [
      { operation_id: "r1", proposal_id: null, asset: "SPY", reasons: [], rejected_at: null, would_have_net_pnl_usd: "-4.11", would_have_outcome: null },
      { operation_id: "r2", proposal_id: null, asset: "SPY", reasons: [], rejected_at: null, would_have_net_pnl_usd: "3.92", would_have_outcome: null },
    ];
    const kpis = orderKpis(orders, rejected);
    expect(kpis.fees).toBe("0.35");
    expect(kpis.filled).toBe(2);
    expect(kpis.wouldHave).toBe("-0.19");
  });
});
