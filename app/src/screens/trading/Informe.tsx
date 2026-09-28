import { Download, Printer } from "lucide-react";
import { useMemo, useState } from "react";

import { useExportTrades, usePeriodReport } from "@/api/queries";
import type { PeriodReport, PeriodTrade } from "@/api/types";
import { apiError } from "@/i18n/labels";
import { decimal, money, pct, price } from "@/lib/format";
import { printPage, saveDownload } from "@/lib/native";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { DataTable, Metric, Section, Signed } from "@/ui/data";
import { EmptyState, Skeleton } from "@/ui/feedback";
import { DatePicker, MonthPicker, YearPicker } from "@/ui/date-picker";
import { Segmented } from "@/ui/nav";
import { useToast } from "@/ui/toast";
import { EquityArea } from "@/ui/viz";

type Mode = "dia" | "mes" | "anio" | "todo";

/** Today in New York as YYYY-MM-DD (the trading calendar's day). */
function todayNewYork(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

function lastDayOfMonth(month: string): string {
  const [year, m] = month.split("-").map(Number);
  const last = new Date(Date.UTC(year!, m!, 0)).getUTCDate();
  return `${month}-${String(last).padStart(2, "0")}`;
}

/** Format a calendar label; an invalid date shows the raw value instead of crashing. */
function label(options: Intl.DateTimeFormatOptions, iso: string, fallback: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat("es", { ...options, timeZone: "UTC" }).format(date);
}

function bounds(mode: Mode, day: string, month: string, year: string): { start: string | null; end: string | null; label: string } {
  if (mode === "dia") return { start: day, end: day, label: label({ dateStyle: "long" }, `${day}T12:00:00Z`, day) };
  if (mode === "mes") {
    return { start: `${month}-01`, end: lastDayOfMonth(month), label: label({ month: "long", year: "numeric" }, `${month}-15T12:00:00Z`, month) };
  }
  if (mode === "anio") return { start: `${year}-01-01`, end: `${year}-12-31`, label: `Año ${year}` };
  return { start: null, end: null, label: "Desde el inicio" };
}

function DayBars({ report }: { report: PeriodReport }) {
  const days = report.days.filter((day) => Number(day.net_pnl_usd) !== 0);
  if (!days.length) return <p className="text-body-2 text-fg-muted">Sin días con operaciones cerradas.</p>;
  const max = Math.max(...days.map((day) => Math.abs(Number(day.net_pnl_usd))), 1);
  return (
    <div className="flex h-40 items-end gap-1" role="img" aria-label="Resultado por día">
      {days.map((day) => {
        const value = Number(day.net_pnl_usd);
        return (
          <div key={day.day} className="group relative flex h-full min-w-[6px] flex-1 flex-col justify-end" title={`${day.day}: ${money(day.net_pnl_usd)}`}>
            <div className={cn("w-full rounded-t-xs", value >= 0 ? "bg-positive" : "bg-negative")} style={{ height: `${Math.max(3, (Math.abs(value) / max) * 100)}%` }} />
          </div>
        );
      })}
    </div>
  );
}

function Distribution({ trades }: { trades: PeriodTrade[] }) {
  if (trades.length < 2) return <p className="text-body-2 text-fg-muted">Hacen falta más operaciones para ver la distribución.</p>;
  const values = trades.map((trade) => Number(trade.net_pnl_usd));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const buckets = 12;
  const step = (max - min || 1) / buckets;
  const counts = Array.from({ length: buckets }, () => 0);
  for (const value of values) counts[Math.min(buckets - 1, Math.floor((value - min) / step))]! += 1;
  const top = Math.max(...counts, 1);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex h-32 items-end gap-1" role="img" aria-label="Distribución del resultado por operación">
        {counts.map((count, index) => {
          const from = min + index * step;
          return (
            <div key={index} className="flex h-full flex-1 flex-col justify-end" title={`${money(from.toFixed(0))} a ${money((from + step).toFixed(0))}: ${count}`}>
              <div className={cn("w-full rounded-t-xs", from + step / 2 >= 0 ? "bg-positive/70" : "bg-negative/70")} style={{ height: `${(count / top) * 100}%` }} />
            </div>
          );
        })}
      </div>
      <div className="flex justify-between text-caption text-fg-muted">
        <span className="num">{money(min.toFixed(0))}</span>
        <span className="num">{money(max.toFixed(0))}</span>
      </div>
    </div>
  );
}

function Kpis({ report }: { report: PeriodReport }) {
  const net = Number(report.net_pnl_usd);
  return (
    <div className="grid grid-cols-5 gap-x-8 gap-y-6 max-xl:grid-cols-3 max-lg:grid-cols-2">
      <Metric label="Cuenta al inicio" value={report.equity_start ? money(report.equity_start, { dp: 0 }) : "—"} context="patrimonio en Alpaca Paper" />
      <Metric
        label="Cuenta al final"
        value={report.equity_end ? money(report.equity_end, { dp: 0 }) : "—"}
        context={report.return_percent ? `${decimal(report.return_percent, { dp: 2, signed: true })}% en el periodo` : "sin datos de patrimonio"}
      />
      <Metric label="Ganancia neta" value={<Signed value={report.net_pnl_usd} />} context={net >= 0 ? "de las operaciones cerradas" : "pérdida de las operaciones cerradas"} />
      <Metric label="Operaciones" value={report.trades} context={`${report.wins} ganadoras · ${report.losses} perdedoras`} />
      <Metric label="Acierto" value={report.win_rate_percent ? pct(report.win_rate_percent, { dp: 0 }) : "—"} context="operaciones que terminaron ganando" />
      <Metric label="Profit factor" value={report.profit_factor ? decimal(report.profit_factor, { dp: 2 }) : "—"} context="lo ganado entre lo perdido (>1 es bueno)" />
      <Metric label="Mejor operación" value={report.best_trade_usd ? <Signed value={report.best_trade_usd} /> : "—"} context={report.average_win_usd ? `media ganadora ${money(report.average_win_usd)}` : ""} />
      <Metric label="Peor operación" value={report.worst_trade_usd ? <Signed value={report.worst_trade_usd} /> : "—"} context={report.average_loss_usd ? `media perdedora ${money(report.average_loss_usd)}` : ""} />
      <Metric
        label="Caída máxima"
        value={money(report.max_drawdown_usd, { dp: 0 })}
        context={report.max_drawdown_percent ? `${decimal(report.max_drawdown_percent, { dp: 2 })}% desde el máximo` : "desde el punto más alto"}
      />
      <Metric label="Comisiones" value={money(report.fees_usd)} context={report.expectancy_usd ? `esperanza ${money(report.expectancy_usd)} por operación` : "cobradas por el broker"} />
    </div>
  );
}

function TradesTable({ trades }: { trades: PeriodTrade[] }) {
  return (
    <DataTable<PeriodTrade>
      label="Operaciones del periodo"
      data={[...trades].reverse()}
      getRowId={(row) => row.position_id}
      empty={<EmptyState compact title="Sin operaciones cerradas en este periodo" description="Cuando el sistema cierre operaciones aparecerán aquí." />}
      columns={[
        { header: "Cerrada", id: "closed", cell: ({ row }) => <span className="num text-fg-2">{new Date(row.original.closed_at).toLocaleString("es", { dateStyle: "short", timeStyle: "short" })}</span> },
        { header: "Activo", id: "asset", cell: ({ row }) => <span className="font-medium text-fg">{row.original.asset}</span> },
        { header: "Entrada → salida", id: "prices", meta: { align: "right" }, cell: ({ row }) => <span className="num text-fg-2">{price(row.original.entry_price)} → {price(row.original.exit_price)}</span> },
        { header: "Cantidad", id: "qty", meta: { align: "right", hideBelow: "lg" }, cell: ({ row }) => <span className="num text-fg-2">{row.original.quantity ?? "—"}</span> },
        { header: "Comisión", id: "fees", meta: { align: "right", hideBelow: "lg" }, cell: ({ row }) => <span className="num text-fg-2">{money(row.original.fees_usd)}</span> },
        { header: "Resultado", id: "pnl", meta: { align: "right" }, cell: ({ row }) => <Signed value={row.original.net_pnl_usd} /> },
      ]}
    />
  );
}

/** Trading › Informe: a business report for any period, printable and exportable. */
export function Informe() {
  const today = todayNewYork();
  const [mode, setMode] = useState<Mode>("mes");
  const [day, setDay] = useState(today);
  const [month, setMonth] = useState(today.slice(0, 7));
  const [year, setYear] = useState(today.slice(0, 4));
  const range = useMemo(() => bounds(mode, day, month, year), [mode, day, month, year]);
  const report = usePeriodReport(range.start, range.end);
  const exportTrades = useExportTrades();
  const toast = useToast();

  const equity = (report.data?.days ?? []).filter((point) => point.equity !== null).map((point) => ({ label: point.day, value: Number(point.equity) }));

  const download = () =>
    exportTrades.mutate(
      { start: range.start, end: range.end },
      {
        onSuccess: async ({ filename, csv }) => {
          try {
            const where = await saveDownload(filename, csv);
            toast({ tone: "success", title: "Operaciones exportadas", description: where ? `Guardado en ${where}` : filename });
          } catch (error) {
            toast({ tone: "error", title: "No se pudo guardar el archivo", description: String(error) });
          }
        },
        onError: (error) => toast({ tone: "error", title: "No se pudo exportar", description: apiError(error.message) }),
      },
    );

  return (
    <div className="flex flex-col gap-8">
      <div data-print-hide className="flex flex-wrap items-center gap-3">
        <Segmented
          label="Periodo"
          value={mode}
          onChange={setMode}
          size="md"
          items={[
            { value: "dia", label: "Día" },
            { value: "mes", label: "Mes" },
            { value: "anio", label: "Año" },
            { value: "todo", label: "Todo" },
          ]}
        />
        {mode === "dia" ? <DatePicker value={day} max={today} onChange={setDay} /> : null}
        {mode === "mes" ? <MonthPicker value={month} max={today.slice(0, 7)} onChange={setMonth} /> : null}
        {mode === "anio" ? <YearPicker value={year} min={2024} max={Number(today.slice(0, 4))} onChange={setYear} /> : null}
        <div className="ml-auto flex gap-2">
          <Button icon={Download} loading={exportTrades.isPending} onClick={download}>
            Excel (CSV)
          </Button>
          <Button icon={Printer} onClick={() => void printPage()}>
            PDF
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <h2 className="text-title font-semibold text-fg first-letter:uppercase">{range.label}</h2>
        <p className="text-body-2 text-fg-muted">Cuenta Alpaca Paper · QQQ · resultados de operaciones cerradas, en dólares.</p>
      </div>

      {report.isLoading ? (
        <Skeleton className="h-64" />
      ) : !report.data ? (
        <EmptyState title="Sin informe" description={report.error ? apiError(report.error.message) : "El núcleo no respondió."} />
      ) : (
        <>
          <Kpis report={report.data} />
          <div className="grid grid-cols-2 gap-8 max-lg:grid-cols-1">
            <Section title="Evolución de la cuenta">
              {equity.length >= 2 ? <EquityArea points={equity} height={180} format={(n) => money(n.toFixed(0), { dp: 0 })} /> : <p className="text-body-2 text-fg-muted">Hacen falta al menos dos días con la cuenta sincronizada.</p>}
            </Section>
            <Section title="Resultado por día">
              <DayBars report={report.data} />
            </Section>
          </div>
          <Section title="Distribución por operación" description="Cuántas operaciones cayeron en cada tramo de resultado.">
            <Distribution trades={report.data.trade_rows} />
          </Section>
          <Section title="Operaciones">
            <TradesTable trades={report.data.trade_rows} />
          </Section>
        </>
      )}
    </div>
  );
}
