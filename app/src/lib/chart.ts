/**
 * Pure helpers for price charts. Prices arrive as Decimal strings; they become
 * floats only for chart geometry and the chart's own labels, never for money
 * shown elsewhere.
 */

import type { Candle, ChartInterval } from "@/api/types";

import { readPreference, writePreference } from "./theme";
import { parseUTC } from "./format";

export type ChartType = "velas" | "barras" | "heikin" | "linea" | "area";

export const CHART_TYPES: Array<{ value: ChartType; label: string; hint: string }> = [
  { value: "velas", label: "Velas", hint: "Cada vela muestra apertura, máximo, mínimo y cierre del periodo." },
  { value: "barras", label: "Barras", hint: "Lo mismo que las velas, dibujado como barras finas." },
  { value: "heikin", label: "Heikin Ashi", hint: "Velas suavizadas: la tendencia se ve más limpia, pero no son precios exactos." },
  { value: "linea", label: "Línea", hint: "Solo el precio de cierre de cada periodo." },
  { value: "area", label: "Área", hint: "Línea de cierre con el área sombreada." },
];

export const CHART_INTERVALS: Array<{ value: ChartInterval; label: string; minutes: number }> = [
  { value: "1m", label: "1m", minutes: 1 },
  { value: "5m", label: "5m", minutes: 5 },
  { value: "15m", label: "15m", minutes: 15 },
  { value: "1h", label: "1H", minutes: 60 },
  { value: "4h", label: "4H", minutes: 240 },
  { value: "1d", label: "1D", minutes: 1440 },
];

export interface Bar {
  time: number; // UTC seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Candles → sorted bars, one per close time (duplicates dropped). */
export function toBars(candles: Candle[]): Bar[] {
  const byTime = new Map<number, Bar>();
  for (const candle of candles) {
    const date = parseUTC(candle.event_time);
    if (!date) continue;
    const time = Math.floor(date.getTime() / 1000);
    byTime.set(time, {
      time,
      open: Number(candle.open),
      high: Number(candle.high),
      low: Number(candle.low),
      close: Number(candle.close),
      volume: Number(candle.volume),
    });
  }
  return [...byTime.values()].sort((a, b) => a.time - b.time);
}

/**
 * Heikin Ashi: close = mean(O,H,L,C); open = mean(previous HA open, previous HA close);
 * high/low include the HA open and close. The first bar seeds with (O+C)/2.
 */
export function heikinAshi(bars: Bar[]): Bar[] {
  const out: Bar[] = [];
  for (const bar of bars) {
    const close = (bar.open + bar.high + bar.low + bar.close) / 4;
    const previous = out[out.length - 1];
    const open = previous ? (previous.open + previous.close) / 2 : (bar.open + bar.close) / 2;
    out.push({ ...bar, open, close, high: Math.max(bar.high, open, close), low: Math.min(bar.low, open, close) });
  }
  return out;
}

export interface RangeSummary {
  first: number;
  last: number;
  high: number;
  low: number;
  changePercent: number;
}

/** What happened in the visible window: change from the first open to the last close, high and low. */
export function summarize(bars: Bar[]): RangeSummary | null {
  if (!bars.length) return null;
  const first = bars[0]!.open;
  const last = bars[bars.length - 1]!.close;
  return {
    first,
    last,
    high: Math.max(...bars.map((bar) => bar.high)),
    low: Math.min(...bars.map((bar) => bar.low)),
    changePercent: first ? ((last - first) / first) * 100 : 0,
  };
}

/** "en 1 h", "en 5 h", "en 2 días" for the span covered by `count` bars of `interval`. */
export function spanLabel(interval: ChartInterval, count: number): string {
  const minutes = (CHART_INTERVALS.find((item) => item.value === interval)?.minutes ?? 1) * count;
  if (minutes < 60) return `en ${minutes} min`;
  if (minutes < 60 * 48) return `en ${Math.round(minutes / 60)} h`;
  return `en ${Math.round(minutes / 1440)} días`;
}

const TYPE_KEY = "hyverion.chart.type";
const INTERVAL_KEY = "hyverion.chart.interval";

export function storedChartType(fallback: ChartType = "velas"): ChartType {
  const value = readPreference(TYPE_KEY);
  return CHART_TYPES.some((item) => item.value === value) ? (value as ChartType) : fallback;
}

export function storeChartType(value: ChartType) {
  writePreference(TYPE_KEY, value);
}

export function storedInterval(fallback: ChartInterval = "15m"): ChartInterval {
  const value = readPreference(INTERVAL_KEY);
  return CHART_INTERVALS.some((item) => item.value === value) ? (value as ChartInterval) : fallback;
}

export function storeInterval(value: ChartInterval) {
  writePreference(INTERVAL_KEY, value);
}
