import { ArrowRight, ArrowUpRight, CircleCheck, OctagonAlert, ShieldAlert, ShieldCheck, TriangleAlert, type LucideIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useLocation } from "wouter";

import { useAiUsage, useReadiness, useStartEngine } from "@/api/queries";
import type { Snapshot } from "@/api/types";
import { UNRESOLVED_STATES } from "@/api/types";
import { alertMessage, engineErrorMessage, whyNow, operationState, readinessDetail, readinessLabel, riskReason, sessionAction, side as sideLabel, traceType, verdict } from "@/i18n/labels";
import { addDec, dateUTC, decimal, displayRatio, money, pct, price, qty, relative, sign, subDec, timeNewYork } from "@/lib/format";
import { PageBody, PageHeader } from "@/shell/PageHeader";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { DataTable, Section, Signed } from "@/ui/data";
import { EmptyState } from "@/ui/feedback";
import { Segmented } from "@/ui/nav";
import { useToast } from "@/ui/toast";
import { AnimatedNumber, EquityArea, PipelineFlow, RadialGauge, type StageState } from "@/ui/viz";

import { SnapshotGate } from "../common";
import { MarketChart } from "../trading/MarketChart";

/* ------------------------------------------------------------- attention */

interface AttentionItem {
  id: string;
  icon: LucideIcon;
  tone: "negative" | "warning";
  title: string;
  description: string;
  action: { label: string; to: string; run?: () => void; busy?: boolean };
}

function useAttention(snapshot: Snapshot): AttentionItem[] {
  const readiness = useReadiness();
  const startEngine = useStartEngine();
  const toast = useToast();
  const items: AttentionItem[] = [];
  const first = snapshot.operations.find((op) => UNRESOLVED_STATES.has(op.state));
  if (snapshot.unresolved_operations > 0) {
    items.push({
      id: "operations",
      icon: OctagonAlert,
      tone: "negative",
      title: snapshot.unresolved_operations === 1 ? "Una operación requiere tu confirmación" : `${snapshot.unresolved_operations} operaciones requieren tu confirmación`,
      description: first ? `${first.asset} · ${operationState(first.state)} · bloquea nuevas entradas` : "Bloquean nuevas entradas hasta confirmar el estado real en el broker",
      action: { label: "Resolver", to: "/trading/operaciones" },
    });
  }
  if (snapshot.protection_recovery.safe_mode) {
    items.push({
      id: "protection",
      icon: ShieldAlert,
      tone: "negative",
      title: "Posiciones sin stop protector verificado",
      description: `${snapshot.protection_recovery.unprotected_position_ids.length} sin protección confirmada · modo seguro activo`,
      action: { label: "Ver posiciones", to: "/trading/operaciones" },
    });
  }
  if (snapshot.engine.state === "stopped" || snapshot.engine.state === "exited") {
    const crashed = snapshot.engine.state === "exited";
    items.push({
      id: "engine",
      icon: crashed ? OctagonAlert : TriangleAlert,
      tone: crashed ? "negative" : "warning",
      title: crashed ? "El motor se detuvo con un error" : "El motor de simulación está detenido",
      description: crashed ? "Revisa el registro del motor y vuelve a iniciarlo" : "No se evalúa el mercado ni se abren operaciones simuladas",
      action: {
        label: "Iniciar motor",
        to: "/inicio",
        busy: startEngine.isPending,
        run: () =>
          startEngine.mutate(60, {
            onSuccess: () => toast({ tone: "success", title: "Motor iniciado", description: "Evalúa el mercado cada 60 s, solo en simulación." }),
            onError: (error) => toast({ tone: "error", title: "No se pudo iniciar el motor", description: engineErrorMessage(error.message) }),
          }),
      },
    });
  }
  for (const alert of snapshot.alerts.filter((a) => !a.acknowledged && a.source !== "operations").slice(0, 2)) {
    items.push({
      id: alert.alert_id,
      icon: TriangleAlert,
      tone: alert.severity === "CRITICAL" ? "negative" : "warning",
      title: alertMessage(alert.message),
      description: `${alert.source} · ${relative(alert.created_at)}`,
      action: { label: "Revisar", to: alert.severity === "CRITICAL" ? "/ajustes/salud" : "/noticias/fuentes" },
    });
  }
  for (const check of readiness.data?.checks.filter((c) => c.status === "FAIL") ?? []) {
    items.push({ id: check.check_id, icon: TriangleAlert, tone: "warning", title: readinessLabel(check.check_id, check.label), description: readinessDetail(check.check_id, check.detail), action: { label: "Ver", to: "/ajustes/salud" } });
  }
  return items;
}

function AttentionStrip({ items }: { items: AttentionItem[] }) {
  const [, navigate] = useLocation();
  if (!items.length) return null;
  return (
    <div className="rise grid gap-2 max-lg:grid-cols-1" style={{ gridTemplateColumns: `repeat(${Math.min(items.length, 3)}, minmax(0, 1fr))` }}>
      {items.slice(0, 3).map((item) => {
        const Icon = item.icon;
        return (
          <button
            key={item.id}
            type="button"
            disabled={item.action.busy}
            onClick={() => (item.action.run ? item.action.run() : navigate(item.action.to))}
            className={cn(
              "group flex items-center gap-3 rounded-md px-4 py-3 text-left transition-colors",
              item.tone === "negative" ? "bg-negative-soft hover:brightness-125" : "bg-surface-1 hover:bg-surface-hover",
            )}
          >
            <Icon size={18} strokeWidth={1.5} className={cn("shrink-0", item.tone === "negative" ? "text-negative" : "text-warning")} aria-hidden />
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="truncate text-body font-medium text-fg">{item.title}</span>
              <span className="truncate text-caption text-fg-2">{item.description}</span>
            </span>
            <span className="flex shrink-0 items-center gap-1 text-caption font-medium text-fg-2 group-hover:text-fg">
              {item.action.label} <ArrowRight size={12} aria-hidden />
            </span>
          </button>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ hero */

type Range = "7" | "30" | "all";

function Hero({ snapshot }: { snapshot: Snapshot }) {
  const { pnl, pnl_history: history } = snapshot;
  const account = pnl.broker_account ?? null;
  // The broker account is the capital: headline its marked equity, not a local projection.
  const headline = account?.equity ?? pnl.marked_equity;
  const [range, setRange] = useState<Range>("30");
  const dayPnl = addDec(pnl.realized_net_pnl, pnl.unrealized_pnl);
  const dayPct = displayRatio(dayPnl, subDec(pnl.marked_equity, dayPnl));

  // Equity curve reconstructed backwards from today's cash equity (display only).
  const points = useMemo(() => {
    const days = range === "all" ? history : history.slice(-Number(range));
    const values: number[] = [];
    let equity = Number(pnl.cash_equity);
    for (let i = days.length - 1; i >= 0; i -= 1) {
      values.unshift(equity);
      equity -= Number(days[i]!.realized_net_pnl);
    }
    const series = days.map((d, i) => ({ label: dateUTC(`${d.session_date}T00:00:00Z`, { seconds: false, zone: false }).replace(" 00:00", ""), value: values[i]! }));
    if (series.length) series.push({ label: "Ahora", value: Number(headline) });
    return series;
  }, [history, pnl.cash_equity, headline, range]);

  if (!account) {
    return (
      <section aria-label="Patrimonio" className="rise relative min-w-0 overflow-hidden rounded-lg bg-surface-1 px-7 py-6">
        <EmptyState
          title="Conecta tu cuenta Alpaca Paper"
          description="Tu patrimonio es el de tu cuenta paper de Alpaca. Guarda sus claves y pulsa Probar conexión en Ajustes › Cuenta de trading."
          action={
            <Link href="/ajustes" className="text-body-2 text-fg underline underline-offset-2">
              Ir a Ajustes
            </Link>
          }
        />
      </section>
    );
  }

  const s = sign(dayPnl);
  return (
    <section aria-label="Patrimonio" className="rise relative min-w-0 overflow-hidden rounded-lg bg-surface-1 px-7 pb-5 pt-6">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="flex flex-col gap-2">
          <span className="text-label text-fg-muted">
            Patrimonio · Alpaca Paper <span className="mono">{account.account_label}</span>
          </span>
          <AnimatedNumber
            value={Number(headline)}
            final={money(headline)}
            format={(n) => money(n.toFixed(2))}
            className="text-[44px] font-semibold leading-[48px] tracking-[-0.03em] text-fg"
          />
          <div className="flex flex-wrap items-center gap-3">
            <span className={cn("inline-flex items-center gap-1 rounded-sm px-2 py-0.5 text-body font-medium", s > 0 ? "bg-positive-soft text-positive" : s < 0 ? "bg-negative-soft text-negative" : "bg-surface-3 text-fg-2")}>
              <ArrowUpRight size={14} aria-hidden className={cn(s < 0 && "rotate-90")} />
              <span className="num">{money(dayPnl, { signed: true })}</span>
              {dayPct !== null ? <span className="num text-[12px] opacity-80">({pct(dayPct.toFixed(2), { signed: true })})</span> : null}
            </span>
            <span className="text-body-2 text-fg-muted">hoy · sincronizado {timeNewYork(account.synced_at)}</span>
          </div>
        </div>
        <div className="flex flex-col items-end gap-3">
          <Segmented
            label="Periodo"
            value={range}
            onChange={setRange}
            items={[
              { value: "7", label: "7 D" },
              { value: "30", label: "30 D" },
              { value: "all", label: "Todo" },
            ]}
          />
          <dl className="grid grid-cols-3 gap-6 text-right">
            {[
              ["Efectivo", money(account.cash)],
              ["Poder de compra", money(account.buying_power)],
              ["Pico", money(pnl.account_high_water_mark)],
            ].map(([label, value]) => (
              <div key={label} className="flex flex-col">
                <dt className="text-caption text-fg-muted">{label}</dt>
                <dd className="num text-body font-medium text-fg">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
      <div className="mt-4">
        {points.length >= 2 ? (
          <EquityArea key={range} points={points} format={(n) => money(n.toFixed(2))} height={200} />
        ) : (
          <EmptyState compact title="La curva aparece tras la primera sesión" description="Cada sesión cerrada agrega un punto a tu curva de patrimonio." />
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------ protection */

function Protection({ snapshot }: { snapshot: Snapshot }) {
  const { pnl, config, protection_recovery: protection, session, risk } = snapshot;
  const drawdown = displayRatio(subDec(pnl.account_high_water_mark, pnl.marked_equity), pnl.account_high_water_mark);
  const maxDrawdown = Number(config.risk.max_account_drawdown_percent) || 5;
  const lossUsed = sign(pnl.realized_net_pnl) < 0 ? subDec("0", pnl.realized_net_pnl) : "0";
  // Daily limit = % of broker equity, bounded by the optional dollar cap. Display only.
  // Until the broker is synced there is no equity: show "—", never "$0".
  const percentLimit = pnl.equity == null ? Number.NaN : (Number(pnl.equity) * Number(config.risk.daily_loss_percent)) / 100;
  const cap = config.risk.daily_loss_hard_cap_usd === null ? Infinity : Number(config.risk.daily_loss_hard_cap_usd);
  const dailyLimit = Number.isFinite(percentLimit) ? Math.min(percentLimit, cap).toFixed(2) : null;
  const lossRatio = displayRatio(lossUsed, dailyLimit);
  const ai = useAiUsage().data?.primary;
  const sessionUsed = ai?.session_used_percent == null ? null : Number(ai.session_used_percent);
  const weeklyUsed = ai?.weekly_used_percent == null ? null : Number(ai.weekly_used_percent);
  const protectedAll = protection.open_positions === protection.protected_positions;

  return (
    <section aria-labelledby="protection-title" className="rise flex min-w-0 flex-col gap-5 rounded-lg bg-surface-1 p-5" style={{ animationDelay: "80ms" }}>
      <header className="flex items-center justify-between">
        <h2 id="protection-title" className="text-section font-semibold text-fg">
          Protección
        </h2>
        <span className={cn("inline-flex items-center gap-1.5 text-caption", session.action === "CONTINUE" ? "text-fg-2" : "text-negative")}>
          {session.action === "CONTINUE" ? <ShieldCheck size={14} aria-hidden /> : <ShieldAlert size={14} aria-hidden />}
          {sessionAction(session.action)}
        </span>
      </header>
      <div className="grid grid-cols-2 gap-x-2 gap-y-4">
        <RadialGauge
          ratio={drawdown === null ? 0 : Math.max(0, drawdown) / maxDrawdown}
          label="Drawdown"
          value={drawdown === null ? "—" : pct(Math.max(0, drawdown).toFixed(1), { dp: 1 })}
          detail={`límite ${pct(config.risk.max_account_drawdown_percent, { dp: 1 })}`}
        />
        <RadialGauge ratio={lossRatio === null ? 0 : lossRatio / 100} label="Pérdida diaria" value={money(lossUsed, { dp: 0 })} detail={dailyLimit === null ? "—" : `de ${money(dailyLimit, { dp: 0 })}`} />
        <RadialGauge
          ratio={sessionUsed === null ? 0 : sessionUsed / 100}
          label="IA · 5 horas"
          value={sessionUsed === null ? "—" : `${Math.round(100 - sessionUsed)}%`}
          detail={sessionUsed === null ? "sin datos" : "disponible"}
        />
        <RadialGauge
          ratio={weeklyUsed === null ? 0 : weeklyUsed / 100}
          label="IA · semana"
          value={weeklyUsed === null ? "—" : `${Math.round(weeklyUsed)}%`}
          detail={weeklyUsed === null ? "sin datos" : "usado"}
        />
      </div>
      <dl className="grid grid-cols-3 gap-3 border-t border-line pt-4 text-center">
        <div className="flex flex-col">
          <dt className="text-caption text-fg-muted">Nivel</dt>
          <dd className="num text-body font-medium text-fg">
            {risk.level ?? 0} · ×{decimal(config.risk.ladder.find((step) => step.level === (risk.level ?? 0))?.risk_multiplier ?? "1", { dp: 2, trim: true })}
          </dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-caption text-fg-muted">Ganancia protegida</dt>
          <dd className="num text-body font-medium text-fg">{money(risk.protected_profit_floor_usd)}</dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-caption text-fg-muted">Stops</dt>
          <dd className={cn("text-body font-medium", protectedAll ? "text-fg" : "text-negative")}>
            {protection.open_positions ? `${protection.protected_positions}/${protection.open_positions}` : "—"}
          </dd>
        </div>
      </dl>
    </section>
  );
}

/* -------------------------------------------------------------- pipeline */

function useStages(snapshot: Snapshot) {
  return useMemo(() => {
    const proposal = snapshot.proposals[0];
    const trace = snapshot.decision_trace;
    const critic = trace.find((t) => t.type === "critic_reviews" && (!proposal || t.proposal_id === proposal.proposal_id));
    const riskRow = snapshot.risk;
    const riskForProposal = !proposal || riskRow.proposal_id === proposal.proposal_id;
    const order = snapshot.orders.find((o) => o.asset === proposal?.asset);
    const fresh = snapshot.markets.length > 0;
    const agentsOk = snapshot.agents.some((a) => a.status !== "ERROR" && a.last_run_at);
    // The snapshot from the WebSocket carries no engine block; treat unknown as on.
    const engineOn = !snapshot.engine || snapshot.engine.state === "running" || snapshot.engine.state === "external";
    const verdictState: StageState = !riskForProposal || !riskRow.verdict ? "idle" : riskRow.verdict === "DENY" ? "blocked" : "done";
    const stages: Array<{ key: string; label: string; state: StageState; detail: string }> = [
      { key: "data", label: "Datos", state: fresh ? "done" : "idle", detail: fresh ? `${snapshot.markets.length} mercados en vivo` : "Sin datos" },
      { key: "agents", label: "Agentes", state: agentsOk ? "done" : "idle", detail: engineOn ? `${snapshot.agents.filter((a) => a.status === "ACTIVE").length} analizando` : "Motor detenido" },
      { key: "proposal", label: "Propuesta", state: proposal ? "done" : "idle", detail: proposal ? `${proposal.asset} · puntuación ${Number(proposal.signal_score).toFixed(0)}` : "Sin propuestas" },
      { key: "critic", label: "Crítico", state: critic ? (critic.status === "REJECT" ? "blocked" : "done") : proposal ? "active" : "idle", detail: critic ? (critic.status === "REJECT" ? "Refutada" : "Sin objeciones") : "Revisando" },
      { key: "risk", label: "Riesgo", state: verdictState === "idle" && proposal ? "active" : verdictState, detail: riskRow.verdict ? `${verdict(riskRow.verdict)} · ${riskReason(riskRow.reasons?.[0] ?? "")}` : "Pendiente" },
      { key: "execution", label: "Ejecución", state: verdictState === "done" ? (order ? "done" : "active") : "idle", detail: order ? `Orden ${order.status === "FILLED" ? "ejecutada" : "enviada"} · simulación` : "Sin orden" },
    ];
    return { stages, proposal };
  }, [snapshot]);
}

function Pipeline({ snapshot }: { snapshot: Snapshot }) {
  const [, navigate] = useLocation();
  const { stages, proposal } = useStages(snapshot);
  return (
    <section aria-labelledby="pipeline-title" className="rise flex flex-col gap-5 rounded-lg bg-surface-1 p-5" style={{ animationDelay: "140ms" }}>
      <header className="flex items-center justify-between gap-4">
        <div className="flex flex-col">
          <h2 id="pipeline-title" className="text-section font-semibold text-fg">
            Última decisión
          </h2>
          <span className="text-caption text-fg-muted">
            {proposal ? `${proposal.asset} · ${sideLabel(proposal.side)} · ${relative(proposal.created_at)}` : "La IA propone; el riesgo determinista decide"}
          </span>
        </div>
        <Button size="sm" variant="ghost" iconRight={ArrowRight} onClick={() => navigate("/riesgo/decisiones")}>
          Ver traza
        </Button>
      </header>
      <PipelineFlow stages={stages} />
      {proposal?.why_now ? <p className="border-t border-line pt-4 text-body-2 text-fg-2">“{whyNow(proposal.why_now)}”</p> : null}
    </section>
  );
}

/* --------------------------------------------------------------- markets */

function Markets({ snapshot }: { snapshot: Snapshot }) {
  if (!snapshot.markets.length) {
    return <EmptyState compact title="Sin datos de mercado todavía" description="Los precios aparecen cuando el motor recibe datos del mercado público." />;
  }
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(snapshot.markets.length, 2)}, minmax(0, 1fr))` }}>
      {snapshot.markets.map((market, index) => (
        <article
          key={market.symbol}
          aria-label={`Mercado ${market.symbol}`}
          className="rise flex min-w-0 flex-col gap-3 rounded-lg bg-surface-1 p-4"
          style={{ animationDelay: `${200 + index * 60}ms` }}
        >
          <header className="flex items-baseline justify-between gap-3">
            <div className="flex items-baseline gap-3">
              <span className="text-body font-medium text-fg">{market.symbol}</span>
              <span key={market.last} className="value-flash num text-[20px] font-semibold leading-7 tracking-[-0.01em] text-fg">
                {price(market.last)}
              </span>
            </div>
            <Link
              href={`/trading/mercado/${market.symbol.replace("/", "-")}`}
              className="inline-flex items-center gap-1 text-body-2 text-fg-2 underline-offset-2 hover:text-fg hover:underline"
            >
              Gráfica completa <ArrowUpRight size={13} aria-hidden />
            </Link>
          </header>
          <MarketChart symbol={market.symbol} fallback={market.candles} compact height={176} />
        </article>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------ positions */

function Positions({ snapshot }: { snapshot: Snapshot }) {
  const [, navigate] = useLocation();
  return (
    <DataTable
      label="Posiciones abiertas"
      data={snapshot.positions}
      getRowId={(row) => String(row.position_id ?? row.id ?? row.asset)}
      onRowClick={() => navigate("/trading/operaciones")}
      empty={<EmptyState compact title="Sin posiciones abiertas" description="Cuando el motor abra una posición verás aquí su resultado y su stop protector." />}
      columns={[
        { header: "Activo", accessorKey: "asset", cell: ({ row }) => <span className="font-medium text-fg">{row.original.asset}</span> },
        { header: "Lado", id: "side", cell: ({ row }) => <span className="text-fg-2">{sideLabel(row.original.side)}</span> },
        { header: "Cantidad", id: "qty", meta: { align: "right" }, cell: ({ row }) => <span className="num">{qty(row.original.quantity)}</span> },
        { header: "Entrada", id: "entry", meta: { align: "right" }, cell: ({ row }) => <span className="num">{price(row.original.entry_price)}</span> },
        { header: "Stop", id: "stop", meta: { align: "right", hideBelow: "xl" }, cell: ({ row }) => <span className="num text-fg-2">{price(row.original.stop_price)}</span> },
        { header: "Resultado", id: "pnl", meta: { align: "right" }, cell: ({ row }) => <Signed value={row.original.unrealized_pnl} /> },
      ]}
    />
  );
}

function Activity({ snapshot }: { snapshot: Snapshot }) {
  const rows = snapshot.decision_trace.slice(0, 6);
  if (!rows.length) return <p className="text-body-2 text-fg-muted">La actividad del pipeline aparecerá aquí.</p>;
  return (
    <ol className="relative ml-1 flex flex-col gap-3 border-l border-line pl-4">
      {rows.map((row) => (
        <li key={row.id} className="relative flex flex-col">
          <span aria-hidden className={cn("absolute -left-[20.5px] top-1.5 size-2 rounded-full ring-4 ring-bg", row.error_code || row.status === "DENY" ? "bg-negative" : "bg-fg-muted")} />
          <span className="truncate text-body-2 text-fg">
            {traceType(row.type)}
            {row.asset ? <span className="text-fg-2"> · {row.asset}</span> : null}
          </span>
          <span className="truncate text-caption text-fg-muted">
            {relative(row.created_at)} · {row.error_code ? `error ${row.error_code}` : riskReason(row.detail)}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Dashboard({ snapshot }: { snapshot: Snapshot }) {
  const attention = useAttention(snapshot);
  return (
    <div className="flex flex-col gap-6">
      {attention.length ? (
        <AttentionStrip items={attention} />
      ) : (
        <p className="rise flex items-center gap-2 text-body-2 text-fg-2">
          <CircleCheck size={16} strokeWidth={1.5} className="text-positive" aria-hidden />
          Nada requiere tu atención. El sistema opera en simulación con todas las protecciones activas.
        </p>
      )}

      <div className="grid grid-cols-[minmax(0,1fr)_380px] gap-6 max-xl:grid-cols-1">
        <Hero snapshot={snapshot} />
        <Protection snapshot={snapshot} />
      </div>

      <Pipeline snapshot={snapshot} />

      <Section title="Mercados" description="Precio de la última hora, vela a vela. Verde: el periodo cerró más alto de lo que abrió; rojo: más bajo.">
        <Markets snapshot={snapshot} />
      </Section>

      <div className="grid grid-cols-[minmax(0,1fr)_340px] gap-10 max-xl:grid-cols-1">
        <Section title="Posiciones abiertas">
          <Positions snapshot={snapshot} />
        </Section>
        <Section title="Actividad reciente">
          <Activity snapshot={snapshot} />
        </Section>
      </div>
    </div>
  );
}

export default function Inicio() {
  return (
    <>
      <PageHeader title="Inicio" />
      <PageBody>
        <SnapshotGate>{(snapshot) => <Dashboard snapshot={snapshot} />}</SnapshotGate>
      </PageBody>
    </>
  );
}
