import {
  AreaSeries,
  BarSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import type { Candle } from "@/api/types";
import { heikinAshi, toBars, type Bar, type ChartType } from "@/lib/chart";

import { cn } from "./cn";

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Re-render charts when the theme attribute changes. */
function useThemeKey(): string {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? "dark");
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(document.documentElement.dataset.theme ?? "dark"));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

export interface ChartLevel {
  price: number;
  label: string;
  tone: "entry" | "stop" | "target";
}

export interface ChartMarker {
  time: number; // UTC seconds
  kind: "entry" | "exit";
  text: string;
}

type AnySeries = ISeriesApi<"Candlestick"> | ISeriesApi<"Bar"> | ISeriesApi<"Line"> | ISeriesApi<"Area">;

const LEVEL_TONE: Record<ChartLevel["tone"], string> = { entry: "--text-secondary", stop: "--negative", target: "--positive" };

function Legend({ bar, compact }: { bar: Bar | null; compact: boolean }) {
  if (!bar || compact) return null;
  const up = bar.close >= bar.open;
  const fmt = (value: number) => value.toLocaleString("es", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (
    <div className="pointer-events-none absolute left-3 top-2 z-[1] flex flex-wrap gap-x-3 font-mono text-[11px] text-fg-muted" aria-hidden>
      <span>
        A <span className={up ? "text-positive" : "text-negative"}>{fmt(bar.open)}</span>
      </span>
      <span>
        Máx <span className={up ? "text-positive" : "text-negative"}>{fmt(bar.high)}</span>
      </span>
      <span>
        Mín <span className={up ? "text-positive" : "text-negative"}>{fmt(bar.low)}</span>
      </span>
      <span>
        C <span className={up ? "text-positive" : "text-negative"}>{fmt(bar.close)}</span>
      </span>
      <span>Vol {bar.volume.toLocaleString("es", { maximumFractionDigits: 2 })}</span>
    </div>
  );
}

/**
 * Price chart with TradingView-style chart types (velas, barras, Heikin Ashi,
 * línea, área), optional position levels (entry/stop/target) and trade markers.
 * `compact` is the small version used in Inicio's market tiles.
 */
export function PriceChart({
  candles,
  type,
  compact = false,
  levels = [],
  markers = [],
  label,
  className,
}: {
  candles: Candle[];
  type: ChartType;
  compact?: boolean;
  levels?: ChartLevel[];
  markers?: ChartMarker[];
  label: string;
  className?: string;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const priceSeries = useRef<AnySeries | null>(null);
  const volumeSeries = useRef<ISeriesApi<"Histogram"> | null>(null);
  const theme = useThemeKey();
  const bars = useMemo(() => toBars(candles), [candles]);
  const shown = useMemo(() => (type === "heikin" ? heikinAshi(bars) : bars), [bars, type]);
  const [hovered, setHovered] = useState<Bar | null>(null);
  const byTime = useMemo(() => new Map(shown.map((bar) => [bar.time, bar])), [shown]);
  const byTimeRef = useRef(byTime);
  byTimeRef.current = byTime;

  // Build the chart when the look changes (type, size, theme); data updates reuse it.
  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const up = cssVar("--positive");
    const down = cssVar("--negative");
    const line = cssVar("--text-primary");
    const api = createChart(element, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: cssVar("--text-muted"),
        fontFamily: "Geist Mono Variable, ui-monospace, monospace",
        fontSize: compact ? 10 : 11,
        // TradingView attribution required by the Lightweight Charts license (see NOTICE).
        attributionLogo: true,
      },
      grid: compact
        ? { vertLines: { visible: false }, horzLines: { visible: false } }
        : { vertLines: { color: cssVar("--border-subtle") }, horzLines: { color: cssVar("--border-subtle") } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.1, bottom: compact ? 0.08 : 0.24 } },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, fixLeftEdge: true, fixRightEdge: true },
      handleScroll: !compact,
      handleScale: !compact,
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: cssVar("--border-strong"), labelBackgroundColor: cssVar("--surface-3") },
        horzLine: { color: cssVar("--border-strong"), labelBackgroundColor: cssVar("--surface-3") },
      },
      // Same number style as the rest of the app (79,231.87).
      localization: { locale: "es", priceFormatter: (value: number) => value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    });
    let series: AnySeries;
    if (type === "linea") series = api.addSeries(LineSeries, { color: line, lineWidth: 2, priceLineColor: cssVar("--text-muted") });
    else if (type === "area")
      series = api.addSeries(AreaSeries, { lineColor: line, lineWidth: 2, topColor: `${line}33`, bottomColor: `${line}00`, priceLineColor: cssVar("--text-muted") });
    else if (type === "barras") series = api.addSeries(BarSeries, { upColor: up, downColor: down, thinBars: false, priceLineColor: cssVar("--text-muted") });
    else
      series = api.addSeries(CandlestickSeries, {
        upColor: up,
        downColor: down,
        wickUpColor: up,
        wickDownColor: down,
        borderVisible: false,
        priceLineColor: cssVar("--text-muted"),
      });
    priceSeries.current = series;
    if (!compact) {
      const volume = api.addSeries(HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      api.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      volumeSeries.current = volume;
    }
    api.subscribeCrosshairMove((param) => {
      const time = typeof param.time === "number" ? param.time : null;
      setHovered(time === null ? null : byTimeRef.current.get(time) ?? null);
    });
    chart.current = api;
    return () => {
      api.remove();
      chart.current = null;
      priceSeries.current = null;
      volumeSeries.current = null;
    };
  }, [type, compact, theme]);

  // Data, levels and markers.
  useEffect(() => {
    const api = chart.current;
    const series = priceSeries.current;
    if (!api || !series) return;
    const time = (value: number) => value as UTCTimestamp;
    if (type === "linea" || type === "area") (series as ISeriesApi<"Line">).setData(shown.map((bar) => ({ time: time(bar.time), value: bar.close })));
    else (series as ISeriesApi<"Candlestick">).setData(shown.map((bar) => ({ time: time(bar.time), open: bar.open, high: bar.high, low: bar.low, close: bar.close })));
    // Canvas colours: tokens are 6-digit hex, so append an alpha byte (~35%).
    const upSoft = `${cssVar("--positive")}59`;
    const downSoft = `${cssVar("--negative")}59`;
    volumeSeries.current?.setData(shown.map((bar) => ({ time: time(bar.time), value: bar.volume, color: bar.close >= bar.open ? upSoft : downSoft })));
    const lines = levels.map((level) =>
      series.createPriceLine({
        price: level.price,
        color: cssVar(LEVEL_TONE[level.tone]),
        lineWidth: 1,
        lineStyle: level.tone === "entry" ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
        title: level.label,
      }),
    );
    const first = shown[0]?.time ?? 0;
    const plugin = createSeriesMarkers(
      series,
      markers
        .filter((marker) => marker.time >= first)
        .sort((a, b) => a.time - b.time)
        .map((marker) => ({
          time: time(marker.time),
          position: marker.kind === "entry" ? ("belowBar" as const) : ("aboveBar" as const),
          shape: marker.kind === "entry" ? ("arrowUp" as const) : ("arrowDown" as const),
          color: cssVar("--text-primary"),
          text: marker.text,
        })),
    );
    api.timeScale().fitContent();
    return () => {
      // When the chart itself is being rebuilt or unmounted it is already disposed.
      if (chart.current !== api) return;
      for (const priceLine of lines) series.removePriceLine(priceLine);
      plugin.detach();
    };
  }, [shown, levels, markers, type, theme]);

  const lastBar = shown[shown.length - 1] ?? null;
  return (
    <div className={cn("relative h-full w-full", className)}>
      <Legend bar={hovered ?? lastBar} compact={compact} />
      <div ref={container} className="h-full w-full" role="img" aria-label={label} />
    </div>
  );
}
