import { TrendingDown, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";

import { useCandles } from "@/api/queries";
import type { Candle, ChartInterval } from "@/api/types";
import {
  CHART_INTERVALS,
  CHART_TYPES,
  spanLabel,
  storeChartType,
  storedChartType,
  storedInterval,
  storeInterval,
  summarize,
  toBars,
  type ChartType,
} from "@/lib/chart";
import { price } from "@/lib/format";
import { PriceChart, type ChartLevel, type ChartMarker } from "@/ui/charts";
import { cn } from "@/ui/cn";
import { EmptyState, Skeleton } from "@/ui/feedback";
import { Segmented } from "@/ui/nav";
import { Tooltip } from "@/ui/overlay";

const COMPACT_TYPES: ChartType[] = ["velas", "linea", "area"];

/** "▲ +0,42 % en 1 h · máx 64.900 · mín 64.410" — the chart said in words. */
export function MoveSummary({ candles, interval, className }: { candles: Candle[]; interval: ChartInterval; className?: string }) {
  const bars = useMemo(() => toBars(candles), [candles]);
  const summary = summarize(bars);
  if (!summary) return null;
  const up = summary.changePercent >= 0;
  const Icon = up ? TrendingUp : TrendingDown;
  const change = `${up ? "+" : ""}${summary.changePercent.toLocaleString("es", { maximumFractionDigits: 2, minimumFractionDigits: 2 })} %`;
  return (
    <p className={cn("flex flex-wrap items-center gap-x-2 text-body-2", className)}>
      <span className={cn("inline-flex items-center gap-1 font-medium", up ? "text-positive" : "text-negative")}>
        <Icon size={14} strokeWidth={1.75} aria-hidden />
        <span className="num">{change}</span>
      </span>
      <span className="text-fg-2">{spanLabel(interval, bars.length)}</span>
      <span className="num text-fg-muted">
        · máx {price(String(summary.high))} · mín {price(String(summary.low))}
      </span>
    </p>
  );
}

/**
 * Market chart with its controls. `compact` (Inicio): last hour in 1-minute
 * candles with a small type switch. Full (Trading › Mercado): every type and
 * interval. Falls back to the snapshot's own candles if the chart data fails.
 */
export function MarketChart({
  symbol,
  fallback,
  compact = false,
  levels,
  markers,
  height,
}: {
  symbol: string;
  fallback: Candle[];
  compact?: boolean;
  levels?: ChartLevel[];
  markers?: ChartMarker[];
  height?: number;
}) {
  const [type, setType] = useState<ChartType>(() => {
    const stored = storedChartType();
    return compact && !COMPACT_TYPES.includes(stored) ? "velas" : stored;
  });
  const [interval, setChartInterval] = useState<ChartInterval>(() => (compact ? "1m" : storedInterval()));
  const query = useCandles(symbol, interval, compact ? 60 : 150);
  const candles = query.data?.length ? query.data : interval === "1m" ? fallback.slice(compact ? -60 : 0) : [];

  const chooseType = (value: ChartType) => {
    setType(value);
    storeChartType(value);
  };
  const chooseInterval = (value: ChartInterval) => {
    setChartInterval(value);
    storeInterval(value);
  };
  const typeItems = CHART_TYPES.filter((item) => !compact || COMPACT_TYPES.includes(item.value));

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <MoveSummary candles={candles} interval={interval} />
        <div className="flex items-center gap-2">
          {!compact ? (
            <Segmented label="Temporalidad" value={interval} onChange={chooseInterval} items={CHART_INTERVALS.map(({ value, label }) => ({ value, label }))} />
          ) : null}
          <Tooltip content={CHART_TYPES.find((item) => item.value === type)?.hint}>
            <span>
              <Segmented label="Tipo de gráfica" value={type} onChange={chooseType} items={typeItems.map(({ value, label }) => ({ value, label }))} />
            </span>
          </Tooltip>
        </div>
      </div>
      <div className={cn(height ? "shrink-0" : "min-h-0 flex-1", compact ? "" : "rounded-md bg-surface-1 p-2")} style={height ? { height } : undefined}>
        {candles.length ? (
          <PriceChart candles={candles} type={type} compact={compact} levels={levels} markers={markers} label={`Precio de ${symbol}`} />
        ) : query.isLoading ? (
          <Skeleton className="h-full w-full" />
        ) : (
          <EmptyState compact title="Sin datos para esta temporalidad" description="No se pudieron descargar los precios. Revisa la conexión o elige 1m." />
        )}
      </div>
    </div>
  );
}
