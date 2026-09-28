import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { useOrders, useRejectedEntries } from "@/api/queries";
import type { OrderRow, RejectedEntry } from "@/api/types";
import { orderStatus, orderStatusTone, riskReason, side as sideLabel } from "@/i18n/labels";
import { money, pct, price, qty, shortId, sign } from "@/lib/format";
import { Button } from "@/ui/button";
import { DataTable, KeyValue, Metric, Section, Signed } from "@/ui/data";
import { Badge, EmptyState, ErrorState, InfoHint, Status } from "@/ui/feedback";
import { Select } from "@/ui/form";
import { Segmented } from "@/ui/nav";
import { Sheet } from "@/ui/overlay";

import { errorText, When } from "../shared";
import { filterOrders, NO_FILTERS, orderKpis, type OrderFilters } from "./kpis";

const ALL = "all";
const STATUS_OPTIONS = [
  { value: ALL, label: "Todos los estados" },
  { value: "FILLED", label: "Ejecutadas" },
  { value: "PARTIALLY_FILLED", label: "Parcialmente ejecutadas" },
  { value: "OPEN", label: "Abiertas" },
  { value: "CANCELED", label: "Canceladas" },
  { value: "REJECTED", label: "Rechazadas" },
  { value: "UNKNOWN", label: "Sin confirmar" },
];
type Period = "1" | "7" | "all";

function Filters({ filters, onChange, assets }: { filters: OrderFilters; onChange: (next: OrderFilters) => void; assets: string[] }) {
  const period: Period = filters.days === null ? "all" : (String(filters.days) as Period);
  const active = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);
  return (
    <div className="flex flex-wrap items-center gap-2" role="search" aria-label="Filtrar órdenes">
      <div className="relative">
        <Search size={14} strokeWidth={1.5} aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-muted" />
        <input
          value={filters.text}
          onChange={(event) => onChange({ ...filters, text: event.target.value })}
          placeholder="Buscar por orden o activo"
          aria-label="Buscar órdenes"
          className="h-8 w-56 rounded-sm bg-surface-2 pl-8 pr-2 text-body-2 text-fg shadow-[inset_0_0_0_1px_var(--border-default)] placeholder:text-fg-muted"
        />
      </div>
      <Select
        aria-label="Activo"
        value={filters.asset || ALL}
        onValueChange={(value) => onChange({ ...filters, asset: value === ALL ? "" : value })}
        options={[{ value: ALL, label: "Todos los activos" }, ...assets.map((asset) => ({ value: asset, label: asset }))]}
        className="w-44"
      />
      <Segmented
        size="md"
        label="Lado"
        value={filters.side || ALL}
        onChange={(value) => onChange({ ...filters, side: value === ALL ? "" : value })}
        items={[
          { value: ALL, label: "Todas" },
          { value: "BUY", label: "Compras" },
          { value: "SELL", label: "Ventas" },
        ]}
      />
      <Select
        aria-label="Estado"
        value={filters.status || ALL}
        onValueChange={(value) => onChange({ ...filters, status: value === ALL ? "" : value })}
        options={STATUS_OPTIONS}
        className="w-48"
      />
      <Segmented
        size="md"
        label="Periodo"
        value={period}
        onChange={(value) => onChange({ ...filters, days: value === "all" ? null : Number(value) })}
        items={[
          { value: "1", label: "24 h" },
          { value: "7", label: "7 días" },
          { value: "all", label: "Todo" },
        ]}
      />
      {active ? (
        <Button size="sm" variant="ghost" icon={X} onClick={() => onChange(NO_FILTERS)}>
          Quitar filtros
        </Button>
      ) : null}
    </div>
  );
}

function OrderSheet({ order, onClose }: { order: OrderRow | null; onClose: () => void }) {
  return (
    <Sheet
      open={order !== null}
      onOpenChange={(open) => !open && onClose()}
      title={order ? `${sideLabel(order.side)} de ${order.asset}` : ""}
      subtitle={order ? <span className="mono">{order.order_id}</span> : undefined}
    >
      {order ? (
        <div className="flex flex-col gap-6">
          <KeyValue
            items={[
              { label: "Estado", value: <Status tone={orderStatusTone(order.status)}>{orderStatus(order.status)}</Status> },
              { label: "Pedida", value: qty(order.requested_quantity), mono: true },
              { label: "Ejecutada", value: qty(order.filled_quantity), mono: true },
              { label: "Stop de protección", value: order.protective_stop_active ? "Activo" : "No" },
              { label: "Orden de cliente", value: <span className="mono">{order.client_order_id ?? "—"}</span> },
              { label: "Creada", value: <When iso={order.created_at} /> },
            ]}
          />
          <Section title="Ejecuciones" description="Cada parte de la orden que se llenó, con su precio y su comisión.">
            {order.fills?.length ? (
              <ul className="flex flex-col divide-y divide-line text-body-2">
                {order.fills.map((fill, index) => (
                  <li key={String(fill.fill_id ?? index)} className="flex items-center gap-3 py-1.5">
                    <span className="num text-fg">{qty(fill.quantity as string)}</span>
                    <span className="num text-fg-muted">a {price(fill.price as string)}</span>
                    <span className="num text-fg-muted">comisión ${String(fill.fee_usd ?? fill.fee ?? "0")}</span>
                    <span className="flex-1" />
                    <When iso={(fill.filled_at ?? fill.created_at) as string} className="text-caption" />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-body-2 text-fg-muted">Sin ejecuciones.</p>
            )}
          </Section>
        </div>
      ) : null}
    </Sheet>
  );
}

function Executed({ orders, filters, onFilters }: { orders: OrderRow[]; filters: OrderFilters; onFilters: (next: OrderFilters) => void }) {
  const [inspect, setInspect] = useState<OrderRow | null>(null);
  const assets = useMemo(() => [...new Set(orders.map((order) => order.asset))].sort(), [orders]);
  const rows = useMemo(() => filterOrders(orders, filters), [orders, filters]);
  return (
    <Section>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Filters filters={filters} onChange={onFilters} assets={assets} />
        <span className="text-caption text-fg-muted">
          {rows.length} de {orders.length} · órdenes simuladas, sin dinero real
        </span>
      </div>
      <DataTable<OrderRow>
        label="Órdenes simuladas"
        data={rows}
        getRowId={(row) => row.order_id}
        selectedId={inspect?.order_id ?? null}
        onRowClick={setInspect}
        empty={
          <EmptyState
            compact
            title={orders.length ? "Ninguna orden coincide con los filtros" : "Sin órdenes todavía"}
            description={orders.length ? "Cambia o quita los filtros." : "Las órdenes aparecen cuando una propuesta supera al crítico y al control de riesgo."}
          />
        }
        columns={[
          { header: "Activo", id: "asset", cell: ({ row }) => <span className="font-medium text-fg">{row.original.asset}</span> },
          { header: "Lado", id: "side", cell: ({ row }) => <span className="text-fg-2">{sideLabel(row.original.side)}</span> },
          { header: "Pedida", id: "requested", meta: { align: "right" }, cell: ({ row }) => <span className="num">{qty(row.original.requested_quantity)}</span> },
          { header: "Ejecutada", id: "filled", meta: { align: "right" }, cell: ({ row }) => <span className="num">{qty(row.original.filled_quantity)}</span> },
          {
            header: "Precio",
            id: "price",
            meta: { align: "right", hideBelow: "lg" },
            cell: ({ row }) => <span className="num text-fg-2">{row.original.fills?.[0]?.price ? price(row.original.fills[0].price as string) : "—"}</span>,
          },
          { header: "Estado", id: "status", cell: ({ row }) => <Status tone={orderStatusTone(row.original.status)}>{orderStatus(row.original.status)}</Status> },
          {
            header: "Stop",
            id: "stop",
            meta: { hideBelow: "lg" },
            cell: ({ row }) => (row.original.protective_stop_active ? <Badge tone="positive">Activo</Badge> : <span className="text-fg-muted">—</span>),
          },
          { header: "Creada", id: "created", meta: { align: "right" }, cell: ({ row }) => <When iso={row.original.created_at} /> },
        ]}
      />
      <OrderSheet order={inspect} onClose={() => setInspect(null)} />
    </Section>
  );
}

function Rejected({ entries }: { entries: RejectedEntry[] }) {
  return (
    <Section description="Entradas que el sistema quiso hacer y el control de riesgo no dejó. Se simulan igual, para saber si el freno ahorró una pérdida o dejó pasar una ganancia.">
      <DataTable<RejectedEntry>
        label="Frenadas por riesgo"
        data={entries}
        getRowId={(row) => row.operation_id}
        empty={<EmptyState compact title="Nada frenado todavía" description="Cuando el control de riesgo rechace una entrada, aparecerá aquí con su motivo." />}
        columns={[
          { header: "Activo", id: "asset", cell: ({ row }) => <span className="font-medium text-fg">{row.original.asset}</span> },
          {
            header: "Por qué se frenó",
            id: "reasons",
            cell: ({ row }) => <span className="text-fg-2">{row.original.reasons.map((reason) => riskReason(reason)).join(" · ") || "—"}</span>,
          },
          {
            header: "Habría dado",
            id: "would",
            meta: { align: "right" },
            cell: ({ row }) => (row.original.would_have_net_pnl_usd ? <Signed value={row.original.would_have_net_pnl_usd} /> : <span className="text-fg-muted">—</span>),
          },
          { header: "Cuándo", id: "when", meta: { align: "right" }, cell: ({ row }) => <When iso={row.original.rejected_at} /> },
          { header: "Operación", id: "id", meta: { align: "right", hideBelow: "xl" }, cell: ({ row }) => <span className="mono text-caption text-fg-muted">{shortId(row.original.operation_id)}</span> },
        ]}
      />
    </Section>
  );
}

/** Orders or risk-stopped entries, shown inside Trading › Operaciones. */
export function OrdersPanel({ view }: { view: "orders" | "rejected" }) {
  const orders = useOrders();
  const rejected = useRejectedEntries();
  const [filters, setFilters] = useState<OrderFilters>(NO_FILTERS);
  if (view === "orders" && orders.error)
    return <ErrorState title="No se pudieron cargar las órdenes" detail={errorText(orders.error)} onRetry={() => void orders.refetch()} retrying={orders.isFetching} />;
  return view === "orders" ? <Executed orders={orders.data ?? []} filters={filters} onFilters={setFilters} /> : <Rejected entries={rejected.data ?? []} />;
}

/** Fees, fill rate and "what the stopped entries would have made". */
export function useOrderSummary() {
  const orders = useOrders();
  const rejected = useRejectedEntries();
  const allOrders = useMemo(() => orders.data ?? [], [orders.data]);
  const allRejected = useMemo(() => rejected.data ?? [], [rejected.data]);
  return useMemo(() => ({ ...orderKpis(allOrders, allRejected), orders: allOrders.length }), [allOrders, allRejected]);
}

/** Trading › Órdenes: every simulated order and every entry risk stopped, with KPIs and filters. */
export function Ordenes() {
  const orders = useOrders();
  const rejected = useRejectedEntries();
  const [view, setView] = useState<"ejecutadas" | "frenadas">("ejecutadas");
  const [filters, setFilters] = useState<OrderFilters>(NO_FILTERS);
  const allOrders = useMemo(() => orders.data ?? [], [orders.data]);
  const allRejected = useMemo(() => rejected.data ?? [], [rejected.data]);
  const kpis = useMemo(() => orderKpis(filterOrders(allOrders, filters), allRejected), [allOrders, allRejected, filters]);
  const saved = sign(kpis.wouldHave);

  if (orders.error)
    return <ErrorState title="No se pudieron cargar las órdenes" detail={errorText(orders.error)} onRetry={() => void orders.refetch()} retrying={orders.isFetching} />;

  return (
    <div className="flex flex-col gap-7">
      <div className="stagger grid grid-cols-4 gap-x-8 gap-y-5 max-lg:grid-cols-2">
        <Metric label="Órdenes" value={kpis.total} context={kpis.fillRate === null ? "Sin órdenes" : `${pct(kpis.fillRate.toFixed(0), { dp: 0 })} ejecutadas por completo`} />
        <Metric
          label="Comisiones pagadas"
          value={money(kpis.fees)}
          context={
            <span className="inline-flex items-center gap-1">
              Lo que cobra el broker en cada compra y venta <InfoHint term="fees" label="comisiones" />
            </span>
          }
        />
        <Metric label="Frenadas por riesgo" value={kpis.rejected} context="Entradas que no se hicieron" />
        <Metric
          label="Si se hubieran hecho"
          value={kpis.rejected ? <Signed value={kpis.wouldHave} /> : "—"}
          context={saved < 0 ? "El freno ahorró esa pérdida" : saved > 0 ? "El freno dejó pasar esa ganancia" : "Sin diferencia"}
        />
      </div>
      <div>
        <Segmented
          label="Ver"
          value={view}
          onChange={setView}
          size="md"
          items={[
            { value: "ejecutadas", label: `Órdenes (${allOrders.length})` },
            { value: "frenadas", label: `Frenadas por riesgo (${allRejected.length})` },
          ]}
        />
      </div>
      {view === "ejecutadas" ? <Executed orders={allOrders} filters={filters} onFilters={setFilters} /> : <Rejected entries={allRejected} />}
    </div>
  );
}
