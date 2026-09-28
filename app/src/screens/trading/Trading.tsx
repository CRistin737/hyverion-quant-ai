import { ArrowRight, CheckCircle2, OctagonAlert, ShieldCheck, ShieldAlert } from "lucide-react";
import { useMemo, useState } from "react";
import { useLocation, useParams } from "wouter";

import { useOperationEvents, useResolveOperation, useTrades } from "@/api/queries";
import { UNRESOLVED_STATES, type Market, type ResolutionTarget, type Snapshot, type Trade } from "@/api/types";
import { exitReason, operationState, operationTone, side as sideLabel } from "@/i18n/labels";
import { dateUTC, displayRatio, money, parseUTC, pct, price, qty, relative, shortId, subDec } from "@/lib/format";
import { PageBody, PageHeader } from "@/shell/PageHeader";
import { DOMAINS, tabPurpose, tabValues } from "@/shell/routes";
import { Button } from "@/ui/button";
import type { ChartLevel, ChartMarker } from "@/ui/charts";
import { cn } from "@/ui/cn";
import { DataTable, KeyValue, Metric, Section, Signed } from "@/ui/data";
import { Badge, EmptyState, ErrorState, InfoHint, SkeletonRows } from "@/ui/feedback";
import { Field, Input, Select } from "@/ui/form";
import { Segmented } from "@/ui/nav";
import { Dialog, Sheet } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

import { SnapshotGate, useTab } from "../common";
import { errorText, When } from "../shared";
import { durationMinutes, durationText, stopTargetProgress, tradeKpis } from "./kpis";
import { MarketChart } from "./MarketChart";
import { Informe } from "./Informe";
import { OrdersPanel, useOrderSummary } from "./Ordenes";

/* ---------------------------------------------------------------- Mercado */

function changeOf(market: Market): number | null {
  const first = market.candles[0]?.close;
  const last = market.candles[market.candles.length - 1]?.close;
  return first && last ? displayRatio(subDec(last, first), first) : null;
}

/** Entry/stop/target lines and entry/exit arrows for the trades of one symbol. */
function tradeOverlays(trades: Trade[], symbol: string): { levels: ChartLevel[]; markers: ChartMarker[] } {
  const mine = trades.filter((trade) => trade.asset === symbol);
  const open = mine.find((trade) => trade.view === "open");
  const levels: ChartLevel[] = open
    ? ([
        open.entry_price ? { price: Number(open.entry_price), label: "Entrada", tone: "entry" } : null,
        open.stop_price ? { price: Number(open.stop_price), label: "Stop", tone: "stop" } : null,
        open.target_price ? { price: Number(open.target_price), label: "Objetivo", tone: "target" } : null,
      ].filter(Boolean) as ChartLevel[])
    : [];
  const markers: ChartMarker[] = [];
  for (const trade of mine) {
    const opened = parseUTC(trade.opened_at);
    const closed = parseUTC(trade.closed_at);
    if (opened) markers.push({ time: Math.floor(opened.getTime() / 1000), kind: "entry", text: "Compra" });
    if (closed) markers.push({ time: Math.floor(closed.getTime() / 1000), kind: "exit", text: "Venta" });
  }
  return { levels, markers };
}

function MarketView({ snapshot, symbolParam }: { snapshot: Snapshot; symbolParam?: string }) {
  const [, navigate] = useLocation();
  const trades = useTrades();
  const requested = symbolParam?.replace("-", "/");
  const market = snapshot.markets.find((m) => m.symbol === requested) ?? snapshot.markets[0];
  const overlays = useMemo(() => tradeOverlays(trades.data ?? [], market?.symbol ?? ""), [trades.data, market?.symbol]);

  if (!market) {
    return (
      <EmptyState
        title="Sin datos de mercado todavía"
        description="El motor publica precios cuando está en marcha. Revisa que la fuente de mercado público esté activa en Inteligencia › Fuentes."
        action={
          <Button size="sm" iconRight={ArrowRight} onClick={() => navigate("/noticias/fuentes")}>
            Ver fuentes
          </Button>
        }
      />
    );
  }

  const spreadBps = displayRatio(subDec(market.ask, market.bid), market.last);

  return (
    <div className="grid h-[calc(100vh-48px-28px-44px-40px)] min-h-[520px] grid-cols-[220px_minmax(0,1fr)] gap-6 max-lg:h-auto max-lg:grid-cols-1">
      <nav aria-label="Símbolos" className="flex flex-col">
        <p className="pb-2 text-caption font-medium text-fg-muted">Instrumentos</p>
        {snapshot.markets.map((item) => {
          const active = item.symbol === market.symbol;
          const itemChange = changeOf(item);
          return (
            <button
              key={item.symbol}
              type="button"
              aria-current={active ? "true" : undefined}
              onClick={() => navigate(`/trading/mercado/${item.symbol.replace("/", "-")}`)}
              className={cn(
                "flex h-11 items-center gap-2 rounded-sm px-2.5 text-left transition-colors duration-[var(--duration-fast)]",
                active ? "bg-surface-3" : "hover:bg-surface-hover",
              )}
            >
              <span className="flex flex-1 flex-col">
                <span className="text-body font-medium text-fg">
                  {item.symbol}
                  {item.symbol !== snapshot.config.primary_instrument ? <span className="ml-1.5 text-caption font-normal text-fg-muted">sensor</span> : null}
                </span>
                <span className="num text-caption text-fg-muted">{price(item.last)}</span>
              </span>
              <span className={cn("num text-body-2", itemChange === null ? "text-fg-muted" : itemChange >= 0 ? "text-positive" : "text-negative")}>
                {itemChange === null ? "—" : pct(itemChange.toFixed(2), { signed: true })}
              </span>
            </button>
          );
        })}
      </nav>

      <div className="flex min-h-0 min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          <div className="flex flex-col">
            <span className="text-label text-fg-muted">{market.symbol} · precio actual</span>
            <span key={market.last} className="value-flash num text-[28px] font-medium leading-9 tracking-[-0.015em] text-fg">
              {price(market.last)}
            </span>
          </div>
          <KeyStat label="Compra / venta" value={`${price(market.bid)} / ${price(market.ask)}`} />
          <KeyStat label="Spread" hint="spread" value={spreadBps === null ? "—" : `${(spreadBps * 100).toFixed(2)} bps`} />
          <KeyStat label="Volumen de la sesión" value={market.session_volume ? `${qty(market.session_volume, 0)} acciones` : "—"} />
          <span className="ml-auto text-caption text-fg-muted">Actualizado {relative(market.event_time)}</span>
        </div>
        <div className="min-h-[360px] flex-1">
          <MarketChart key={market.symbol} symbol={market.symbol} fallback={market.candles} levels={overlays.levels} markers={overlays.markers} />
        </div>
      </div>
    </div>
  );
}

function KeyStat({ label, value, hint }: { label: string; value: string; hint?: "spread" }) {
  return (
    <div className="flex flex-col">
      <span className="flex items-center gap-1 text-label text-fg-muted">
        {label}
        {hint ? <InfoHint term={hint} /> : null}
      </span>
      <span className="num text-[15px] leading-6 text-fg">{value}</span>
    </div>
  );
}

/* ------------------------------------------------------------ Operaciones */

/** Where the price is between the stop (left, red) and the target (right, green). */
function StopTargetBar({ trade, wide = false }: { trade: Trade; wide?: boolean }) {
  const progress = stopTargetProgress(trade);
  if (progress === null) return <span className="text-fg-muted">—</span>;
  const label = `Precio al ${Math.round(progress * 100)} % del camino entre el stop y el objetivo`;
  return (
    <span className={cn("inline-flex flex-col gap-1", wide ? "w-full" : "w-28")} role="img" aria-label={label} title={label}>
      <span className="relative h-1.5 rounded-full bg-[linear-gradient(90deg,var(--negative-soft),var(--surface-3)_50%,var(--positive-soft))]">
        <span
          className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fg shadow-[0_0_0_2px_var(--background)] transition-[left] duration-[var(--duration-slow)]"
          style={{ left: `${progress * 100}%` }}
        />
      </span>
      {wide ? (
        <span className="flex justify-between text-caption text-fg-muted">
          <span>Stop {price(trade.stop_price)}</span>
          <span>Objetivo {price(trade.target_price)}</span>
        </span>
      ) : null}
    </span>
  );
}

const RESOLUTION_OPTIONS: Array<{ value: ResolutionTarget; label: string; description: string }> = [
  { value: "OPEN", label: "Abierta en el broker", description: "La orden se ejecutó y la posición existe." },
  { value: "CLOSED", label: "Cerrada en el broker", description: "La posición ya no existe en el broker." },
  { value: "CANCELED", label: "Cancelada en el broker", description: "La orden fue cancelada sin ejecutarse." },
  { value: "REJECTED", label: "Rechazada por el broker", description: "El broker nunca aceptó la orden." },
];

type Resolvable = { id: string; asset: string; state: string | null };

function ResolveDialog({ operation, onClose }: { operation: Resolvable | null; onClose: () => void }) {
  const [target, setTarget] = useState<ResolutionTarget | "">("");
  const [reason, setReason] = useState("");
  const resolve = useResolveOperation();
  const toast = useToast();
  const reasonValid = reason.trim().length >= 3;

  const close = () => {
    setTarget("");
    setReason("");
    resolve.reset();
    onClose();
  };

  return (
    <Dialog
      open={operation !== null}
      onOpenChange={(open) => !open && close()}
      width={560}
      title="Confirmar lo que muestra el broker"
      description="Registra el estado real de la operación tal como aparece en el broker. Esta acción no envía ni cancela órdenes; solo desbloquea nuevas entradas y queda auditada."
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            disabled={!target || !reasonValid}
            loading={resolve.isPending}
            onClick={() => {
              if (!operation || !target) return;
              resolve.mutate(
                { operationId: operation.id, target, reason: reason.trim() },
                {
                  onSuccess: (result) => {
                    toast({ tone: "success", title: "Operación resuelta", description: `${operation.asset} · ${operationState(result.state)}` });
                    close();
                  },
                },
              );
            }}
          >
            Registrar resolución
          </Button>
        </>
      }
    >
      {operation ? (
        <div className="flex flex-col gap-4">
          <KeyValue
            items={[
              { label: "Operación", value: <span className="mono">{shortId(operation.id, 14)}</span> },
              { label: "Activo", value: operation.asset },
              { label: "Estado actual", value: <Badge tone={operationTone(operation.state)}>{operationState(operation.state)}</Badge> },
            ]}
          />
          <Field label="Estado verificado en el broker">
            {(id, describedBy) => (
              <Select
                id={id}
                describedBy={describedBy}
                value={target}
                onValueChange={(value) => setTarget(value as ResolutionTarget)}
                placeholder="Selecciona el estado que ves en el broker…"
                options={RESOLUTION_OPTIONS}
              />
            )}
          </Field>
          <Field
            label="Motivo"
            hint="Describe cómo lo verificaste, por ejemplo: «Historial de órdenes de Alpaca Paper sin la orden hyverion-…»."
            error={resolve.error ? errorText(resolve.error) : !reason || reasonValid ? null : "Escribe al menos 3 caracteres."}
          >
            {(id, describedBy) => (
              <Input id={id} aria-describedby={describedBy} value={reason} maxLength={200} onChange={(event) => setReason(event.target.value)} placeholder="Cómo verificaste el estado" />
            )}
          </Field>
        </div>
      ) : null}
    </Dialog>
  );
}

function Lifecycle({ operationId }: { operationId: string }) {
  const events = useOperationEvents(operationId);
  if (events.isLoading) return <SkeletonRows rows={4} />;
  if (events.error) return <ErrorState title="No se pudo cargar el historial" detail={errorText(events.error)} onRetry={() => void events.refetch()} />;
  if (!events.data?.length) return <p className="text-body-2 text-fg-muted">Sin pasos registrados.</p>;
  return (
    <ol className="relative flex flex-col gap-4 border-l border-line pl-4">
      {events.data.map((event) => (
        <li key={event.id} className="relative flex flex-col gap-0.5">
          <span className={cn("absolute -left-[21px] top-1.5 size-2 rounded-full", UNRESOLVED_STATES.has(event.to_state) ? "bg-negative" : "bg-fg-muted")} aria-hidden />
          <span className="text-body text-fg">{operationState(event.to_state)}</span>
          <span className="text-caption text-fg-muted">
            {dateUTC(event.occurred_at)} · {event.reason.replace(/_/g, " ")}
            {!event.legal ? <span className="text-negative"> · paso rechazado</span> : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

function TradeSheet({ trade, onClose, onResolve }: { trade: Trade | null; onClose: () => void; onResolve: (trade: Trade) => void }) {
  return (
    <Sheet
      open={trade !== null}
      onOpenChange={(open) => !open && onClose()}
      title={trade ? `${trade.asset} · ${trade.view === "open" ? "abierta" : trade.view === "closed" ? "cerrada" : "requiere atención"}` : ""}
      subtitle={trade ? <span className="mono">{trade.id}</span> : undefined}
      actions={
        trade?.needs_attention ? (
          <Button size="sm" variant="primary" onClick={() => onResolve(trade)}>
            Resolver
          </Button>
        ) : null
      }
    >
      {trade ? (
        <div className="flex flex-col gap-6">
          <div className="flex flex-col gap-1">
            <span className="text-label text-fg-muted">{trade.view === "open" ? "Resultado hasta ahora" : "Resultado final"}</span>
            <span className="text-[26px] font-medium leading-8">{trade.pnl_usd ? <Signed value={trade.pnl_usd} /> : "—"}</span>
            <span className="text-caption text-fg-muted">Incluye comisiones de {trade.fees_usd === "0" ? "—" : `$${trade.fees_usd}`}</span>
          </div>
          <StopTargetBar trade={trade} wide />
          <KeyValue
            items={[
              { label: "Lado", value: sideLabel(trade.side) },
              { label: "Cantidad", value: qty(trade.quantity), mono: true },
              { label: "Entrada", value: price(trade.entry_price), mono: true },
              trade.view === "closed"
                ? { label: "Salida", value: price(trade.exit_price), mono: true }
                : { label: "Precio actual", value: price(trade.current_price), mono: true },
              { label: "Motivo de cierre", value: trade.view === "closed" ? exitReason(trade.exit_reason) : "—" },
              { label: "Duración", value: durationText(durationMinutes(trade)) },
              { label: "Protección", value: trade.protected ? "Stop verificado" : trade.view === "open" ? "Sin verificar" : "—" },
              { label: "Abierta", value: trade.opened_at ? dateUTC(trade.opened_at) : "—" },
            ]}
          />
          {trade.fills.length ? (
            <Section title="Ejecuciones">
              <ul className="flex flex-col divide-y divide-line text-body-2">
                {trade.fills.map((fill, index) => (
                  <li key={String(fill.fill_id ?? index)} className="flex items-center gap-3 py-1.5">
                    <span className="text-fg-2">{sideLabel(String(fill.side ?? ""))}</span>
                    <span className="num text-fg">{qty(fill.quantity as string)}</span>
                    <span className="num text-fg-muted">a {price(fill.price as string)}</span>
                    <span className="flex-1" />
                    <When iso={(fill.filled_at ?? fill.created_at) as string} className="text-caption" />
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
          {trade.operation_id ? (
            <Section title="Paso a paso">
              <Lifecycle operationId={trade.operation_id} />
            </Section>
          ) : null}
        </div>
      ) : null}
    </Sheet>
  );
}

type TradeFilter = "open" | "closed" | "attention" | "rejected" | "orders";

function OperationsView() {
  const trades = useTrades();
  const orderSummary = useOrderSummary();
  const all = useMemo(() => trades.data ?? [], [trades.data]);
  const kpis = useMemo(() => tradeKpis(all), [all]);
  const [chosen, setChosen] = useState<TradeFilter | null>(null);
  const filter: TradeFilter = chosen ?? (kpis.attention ? "attention" : kpis.open ? "open" : "closed");
  const [inspect, setInspect] = useState<Trade | null>(null);
  const [resolving, setResolving] = useState<Resolvable | null>(null);
  const rows = all.filter((trade) => trade.view === filter);

  if (trades.isLoading) return <SkeletonRows rows={8} />;
  if (trades.error)
    return (
      <ErrorState
        title="No se pudieron cargar las operaciones"
        impact="La protección de cada posición sigue activa en el motor, aunque no se vea aquí."
        detail={errorText(trades.error)}
        onRetry={() => void trades.refetch()}
        retrying={trades.isFetching}
      />
    );

  const common = [
    { header: "Activo", id: "asset", cell: ({ row }: { row: { original: Trade } }) => <span className="font-medium text-fg">{row.original.asset}</span> },
  ];
  const columns =
    filter === "open"
      ? [
          ...common,
          { header: "Entrada", id: "entry", meta: { align: "right" as const }, cell: ({ row }: { row: { original: Trade } }) => <span className="num">{price(row.original.entry_price)}</span> },
          { header: "Ahora", id: "now", meta: { align: "right" as const }, cell: ({ row }: { row: { original: Trade } }) => <span className="num">{price(row.original.current_price)}</span> },
          { header: "Stop → objetivo", id: "progress", meta: { hideBelow: "lg" as const }, cell: ({ row }: { row: { original: Trade } }) => <StopTargetBar trade={row.original} /> },
          { header: "Resultado", id: "pnl", meta: { align: "right" as const }, cell: ({ row }: { row: { original: Trade } }) => <Signed value={row.original.pnl_usd} /> },
          {
            header: "Protección",
            id: "protection",
            cell: ({ row }: { row: { original: Trade } }) =>
              row.original.protected ? (
                <span className="inline-flex items-center gap-1 text-body-2 text-positive">
                  <ShieldCheck size={14} aria-hidden /> Con stop
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-body-2 text-negative">
                  <ShieldAlert size={14} aria-hidden /> Sin verificar
                </span>
              ),
          },
          { header: "Abierta", id: "opened", meta: { align: "right" as const, hideBelow: "xl" as const }, cell: ({ row }: { row: { original: Trade } }) => <When iso={row.original.opened_at} /> },
        ]
      : filter === "closed"
        ? [
            ...common,
            {
              header: "Entrada → salida",
              id: "prices",
              meta: { align: "right" as const },
              cell: ({ row }: { row: { original: Trade } }) => (
                <span className="num text-fg-2">
                  {price(row.original.entry_price)} → {price(row.original.exit_price)}
                </span>
              ),
            },
            { header: "Resultado", id: "pnl", meta: { align: "right" as const }, cell: ({ row }: { row: { original: Trade } }) => <Signed value={row.original.pnl_usd} /> },
            { header: "Cómo cerró", id: "exit", cell: ({ row }: { row: { original: Trade } }) => <span className="text-fg-2">{exitReason(row.original.exit_reason)}</span> },
            { header: "Duración", id: "duration", meta: { hideBelow: "lg" as const }, cell: ({ row }: { row: { original: Trade } }) => <span className="text-fg-2">{durationText(durationMinutes(row.original))}</span> },
            { header: "Cerrada", id: "closed", meta: { align: "right" as const }, cell: ({ row }: { row: { original: Trade } }) => <When iso={row.original.closed_at} /> },
          ]
        : [
            ...common,
            { header: "Estado", id: "state", cell: ({ row }: { row: { original: Trade } }) => <Badge tone={operationTone(row.original.state)}>{operationState(row.original.state)}</Badge> },
            { header: "Desde", id: "since", cell: ({ row }: { row: { original: Trade } }) => <When iso={row.original.updated_at} /> },
            {
              header: "",
              id: "action",
              meta: { align: "right" as const },
              cell: ({ row }: { row: { original: Trade } }) => (
                <Button
                  size="sm"
                  variant="primary"
                  onClick={(event) => {
                    event.stopPropagation();
                    setResolving({ id: row.original.operation_id ?? row.original.id, asset: row.original.asset, state: row.original.state });
                  }}
                >
                  Resolver
                </Button>
              ),
            },
          ];

  return (
    <div className="flex flex-col gap-7">
      {kpis.attention > 0 ? (
        <div role="alert" className="rise flex items-center gap-3 rounded-md bg-negative-soft px-4 py-3">
          <OctagonAlert size={18} strokeWidth={1.5} className="shrink-0 text-negative" aria-hidden />
          <p className="flex-1 text-body text-fg">
            {kpis.attention === 1 ? "Una operación tiene" : `${kpis.attention} operaciones tienen`} un estado sin confirmar en el broker.{" "}
            <span className="text-fg-2">No se abren operaciones nuevas hasta que confirmes su estado real.</span>
          </p>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-body-2 text-fg-2">
          <CheckCircle2 size={16} strokeWidth={1.5} className="text-positive" aria-hidden />
          Todas las operaciones tienen su estado confirmado.
        </div>
      )}

      <div className="stagger grid grid-cols-5 gap-x-8 gap-y-5 max-xl:grid-cols-3 max-lg:grid-cols-2">
        <Metric label="Abiertas ahora" value={kpis.open} context={kpis.open ? "Cada una con su stop de protección" : "Ninguna posición abierta"} />
        <Metric label="Resultado abierto" value={<Signed value={kpis.openPnl} />} context="Lo que ganarías o perderías si cerraras ya" />
        <Metric label="Resultado cerrado · 7 días" value={<Signed value={kpis.closedPnl7d} />} context={`${kpis.closed7d} operaciones cerradas`} />
        <Metric
          label="Acierto · 7 días"
          value={kpis.winRate7d === null ? "—" : pct(kpis.winRate7d.toFixed(0), { dp: 0 })}
          context={kpis.closed7d ? `${kpis.wins7d} de ${kpis.closed7d} terminaron ganando` : "Sin operaciones cerradas"}
        />
        <Metric
          label="Frenadas por riesgo"
          value={orderSummary.rejected}
          context={orderSummary.rejected ? <>Habrían dado <Signed value={orderSummary.wouldHave} /></> : `Comisiones pagadas: ${money(orderSummary.fees)}`}
        />
      </div>

      <Section
        title="Operaciones"
        description="Pulsa una operación para ver el paso a paso, sus órdenes, sus ejecuciones y cómo cerró."
        actions={
          <Segmented
            label="Mostrar operaciones"
            value={filter}
            onChange={setChosen}
            items={[
              { value: "open", label: `Abiertas${kpis.open ? ` (${kpis.open})` : ""}` },
              { value: "closed", label: "Cerradas" },
              { value: "rejected", label: `Frenadas${orderSummary.rejected ? ` (${orderSummary.rejected})` : ""}` },
              { value: "orders", label: "Órdenes" },
              { value: "attention", label: `Requieren atención${kpis.attention ? ` (${kpis.attention})` : ""}` },
            ]}
          />
        }
      >
        {filter === "rejected" || filter === "orders" ? (
          <OrdersPanel view={filter === "orders" ? "orders" : "rejected"} />
        ) : (
        <DataTable<Trade>
          label="Operaciones"
          data={rows}
          getRowId={(row) => row.id}
          selectedId={inspect?.id ?? null}
          onRowClick={setInspect}
          empty={
            <EmptyState
              compact
              title={filter === "attention" ? "Nada que confirmar" : filter === "open" ? "Sin operaciones abiertas" : "Sin operaciones cerradas todavía"}
              description={
                filter === "attention"
                  ? "Todas las operaciones tienen su estado confirmado en el broker."
                  : filter === "open"
                    ? "Cuando el motor compre, la operación aparecerá aquí con su stop y su objetivo."
                    : "Las operaciones cerradas se evalúan y alimentan Aprendizaje."
              }
            />
          }
          columns={columns}
        />
        )}
      </Section>

      <TradeSheet
        trade={inspect}
        onClose={() => setInspect(null)}
        onResolve={(trade) => setResolving({ id: trade.operation_id ?? trade.id, asset: trade.asset, state: trade.state })}
      />
      <ResolveDialog operation={resolving} onClose={() => setResolving(null)} />
    </div>
  );
}

export default function Trading() {
  const params = useParams<{ tab?: string; symbol?: string }>();
  const [, navigate] = useLocation();
  const tab = useTab(params.tab, tabValues("trading"), "mercado");

  return (
    <>
      <PageHeader title="Trading" tabs={DOMAINS.trading.tabs} tab={tab} onTab={(value) => navigate(`/trading/${value}`)} />
      <PageBody wide={tab === "mercado"} intro={tab === "mercado" ? undefined : tabPurpose("trading", tab)}>
        {tab === "operaciones" ? (
          <OperationsView />
        ) : tab === "informe" ? (
          <Informe />
        ) : (
          <SnapshotGate>{(snapshot) => <MarketView snapshot={snapshot} symbolParam={params.symbol} />}</SnapshotGate>
        )}
      </PageBody>
    </>
  );
}
