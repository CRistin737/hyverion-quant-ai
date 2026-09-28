import { describe, expect, it } from "vitest";

import type { Candle } from "@/api/types";

import { heikinAshi, spanLabel, summarize, toBars } from "./chart";

const candle = (minute: number, open: number, high: number, low: number, close: number): Candle => ({
  symbol: "QQQ",
  interval: "1m",
  open: String(open),
  high: String(high),
  low: String(low),
  close: String(close),
  volume: "1",
  event_time: new Date(Date.UTC(2026, 8, 25, 14, minute)).toISOString(),
});

describe("toBars", () => {
  it("sorts by time and keeps one bar per close time (latest wins)", () => {
    const bars = toBars([candle(2, 10, 12, 9, 11), candle(1, 9, 10, 8, 10), candle(2, 10, 13, 9, 12)]);
    expect(bars.map((bar) => bar.close)).toEqual([10, 12]);
  });
});

describe("heikinAshi", () => {
  it("averages each bar and chains the open from the previous HA bar", () => {
    const ha = heikinAshi(toBars([candle(1, 10, 14, 8, 12), candle(2, 12, 16, 11, 15)]));
    expect(ha[0]).toMatchObject({ open: 11, close: 11, high: 14, low: 8 });
    expect(ha[1]!.close).toBe((12 + 16 + 11 + 15) / 4);
    expect(ha[1]!.open).toBe((11 + 11) / 2);
    expect(ha[1]!.high).toBe(16);
  });
});

describe("summarize", () => {
  it("reports the change from the first open to the last close with the range", () => {
    const summary = summarize(toBars([candle(1, 100, 104, 99, 102), candle(2, 102, 103, 97, 101)]))!;
    expect(summary.changePercent).toBeCloseTo(1);
    expect(summary.high).toBe(104);
    expect(summary.low).toBe(97);
  });

  it("is null without data", () => {
    expect(summarize([])).toBeNull();
  });
});

describe("spanLabel", () => {
  it("says the covered time in plain words", () => {
    expect(spanLabel("1m", 60)).toBe("en 1 h");
    expect(spanLabel("1m", 30)).toBe("en 30 min");
    expect(spanLabel("1h", 150)).toBe("en 6 días");
  });
});
