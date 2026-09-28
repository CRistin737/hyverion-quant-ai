import { ArrowRight, ListTree, Lock, ShieldCheck } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useLocation, useParams } from "wouter";

import { useBrokerStatus } from "@/api/queries";
import type { ConfigPatch, LadderLevel, Proposal, PublicConfig, RiskLimits, RiskProfileName, RiskProfileValues, Snapshot, TraceRow } from "@/api/types";
import { agentName, errorCode, whyNow, side as sideLabel, riskReason, traceStatus, traceStatusTone, traceType, verdict, verdictTone } from "@/i18n/labels";
import { dateUTC, decimal, money, pct, relative, sign, subDec, timeUTC } from "@/lib/format";
import { PageBody, PageHeader } from "@/shell/PageHeader";
import { DOMAINS, tabPurpose, tabValues } from "@/shell/routes";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { DataTable, KeyValue, Section } from "@/ui/data";
import { Badge, EmptyState, InfoHint } from "@/ui/feedback";
import { DecimalInput, Input } from "@/ui/form";
import { Sheet, Tooltip } from "@/ui/overlay";

import { SnapshotGate, useTab } from "../common";
import { ApplyBar, SettingRow, useDraft } from "../settings-kit";

/** Codes look like `snake_case`; free text from agents is shown as-is. */
function traceDetail(detail: string): string {
  return /^[a-z0-9_]+$/.test(detail) ? riskReason(detail) : detail;
}

function bps(value: string): string {
  return `${decimal(value, { dp: 1, trim: true })} bps`;
}

/** A fraction such as "0.6" shown as "60%" — the exponent shift keeps it exact. */
function fractionPct(value: string): string {
  return pct(`${value}e2`, { dp: 0 });
}

function multiplier(value: string | null | undefined): string {
  return value ? `×${decimal(value, { dp: 2, trim: true })}` : "—";
}

/* ───────────────────────────── Decisión ───────────────────────────── */

function TraceSheet({ row, onClose }: { row: TraceRow | null; onClose: () => void }) {
  return (
    <Sheet
      open={row !== null}
      onOpenChange={(open) => !open && onClose()}
      title={row ? `${traceType(row.type)}${row.asset ? ` · ${row.asset}` : ""}` : "Traza"}
      subtitle={row ? dateUTC(row.created_at) : undefined}
    >
      {row ? (
        <KeyValue
          items={[
            { label: "Tipo", value: traceType(row.type) },
            { label: "Estado", value: row.status ? <Badge tone={traceStatusTone(row.status)}>{traceStatus(row.status)}</Badge> : "—" },
            { label: "Activo", value: row.asset ?? "—", mono: true },
            { label: "Fecha", value: dateUTC(row.created_at), mono: true },
            { label: "Detalle", value: <span className="text-fg-2">{traceDetail(row.detail) || "—"}</span> },
            { label: "Error", value: row.error_code ? <span className="text-negative">{errorCode(row.error_code)}</span> : "Ninguno" },
            { label: "Código de error", value: row.error_code ?? "—", mono: true },
            { label: "Agente", value: row.agent_id ? agentName(row.agent_id) : "—" },
            { label: "Proveedor", value: row.provider ?? "—", mono: true },
            { label: "Modelo", value: row.model ?? "—", mono: true },
            { label: "ID de decisión", value: <span className="selectable">{row.decision_id ?? "—"}</span>, mono: true },
            { label: "ID de propuesta", value: <span className="selectable">{row.proposal_id ?? "—"}</span>, mono: true },
            { label: "ID de traza", value: <span className="selectable">{row.id}</span>, mono: true },
          ]}
        />
      ) : null}
    </Sheet>
  );
}

function Timeline({ rows, onOpen }: { rows: TraceRow[]; onOpen: (row: TraceRow) => void }) {
  if (!rows.length) {
    return (
      <EmptyState
        compact
        icon={ListTree}
        title="La traza está vacía"
        description="Cada propuesta, revisión del crítico y decisión de riesgo deja aquí un registro auditable. Aparecerá en cuanto el pipeline complete su primer ciclo."
      />
    );
  }
  return (
    <ol className="flex flex-col" aria-label="Traza de decisiones">
      {rows.map((row) => (
        <li key={row.id}>
          <button
            type="button"
            onClick={() => onOpen(row)}
            className="grid w-full grid-cols-[72px_minmax(0,1fr)_auto] items-start gap-3 border-b border-line py-2.5 text-left transition-colors duration-[var(--duration-fast)] hover:bg-surface-hover focus-visible:bg-surface-hover"
          >
            <Tooltip content={dateUTC(row.created_at)}>
              <span className="num pt-px text-caption text-fg-muted">{timeUTC(row.created_at)}</span>
            </Tooltip>
            <span className="flex min-w-0 flex-col">
              <span className="truncate text-body text-fg">
                {traceType(row.type)}
                {row.asset ? <span className="num text-fg-2"> · {row.asset}</span> : null}
                {row.agent_id ? <span className="text-fg-muted"> · {agentName(row.agent_id)}</span> : null}
              </span>
              <span className={cn("truncate text-body-2", row.error_code ? "text-negative" : "text-fg-muted")}>
                {row.error_code ? `Error: ${errorCode(row.error_code)}` : traceDetail(row.detail)}
              </span>
            </span>
            {row.status ? <Badge tone={traceStatusTone(row.status)}>{traceStatus(row.status)}</Badge> : <span />}
          </button>
        </li>
      ))}
    </ol>
  );
}

function TradeProposals({ proposals }: { proposals: Proposal[] }) {
  return (
    <DataTable
      label="Propuestas recientes"
      data={proposals.slice(0, 8)}
      getRowId={(row) => row.proposal_id}
      empty={
        <EmptyState
          compact
          title="Aún no hay propuestas"
          description="Si el mercado no ofrece una oportunidad clara, no proponer es lo correcto."
        />
      }
      columns={[
        {
          header: "Activo",
          id: "asset",
          cell: ({ row }) => (
            <span className="inline-flex items-center gap-2">
              <span className="num text-fg">{row.original.asset}</span>
              {row.original.is_a_plus ? (
                <Tooltip content="Cumple todas las confirmaciones exigidas.">
                  <span>
                    <Badge tone="positive">A+</Badge>
                  </span>
                </Tooltip>
              ) : null}
            </span>
          ),
        },
        { header: "Lado", id: "side", cell: ({ row }) => <span className="text-fg-2">{sideLabel(row.original.side)}</span> },
        {
          header: "Puntuación",
          id: "score",
          meta: { align: "right" },
          accessorFn: (row) => Number(row.signal_score),
          cell: ({ row }) => <span className="num">{decimal(row.original.signal_score, { dp: 1 })}</span>,
        },
        {
          header: "Por qué ahora",
          id: "why",
          meta: { hideBelow: "lg", className: "max-w-0 w-[50%]" },
          cell: ({ row }) => (
            <Tooltip content={whyNow(row.original.why_now ?? "")}>
              <span className="block truncate text-fg-2">{row.original.why_now ? whyNow(row.original.why_now) : "—"}</span>
            </Tooltip>
          ),
        },
        { header: "Cuándo", id: "created", meta: { align: "right" }, cell: ({ row }) => <span className="text-caption text-fg-muted">{relative(row.original.created_at)}</span> },
      ]}
    />
  );
}

function DecisionView({ snapshot }: { snapshot: Snapshot }) {
  const [selected, setSelected] = useState<TraceRow | null>(null);
  const { risk, decision_trace: trace, config } = snapshot;
  const level = risk.level ?? null;
  const ladder = level === null ? undefined : config.risk.ladder.find((l) => l.level === level);
  const linked = trace.find((row) => (risk.decision_id && row.decision_id === risk.decision_id) || (risk.proposal_id && row.proposal_id === risk.proposal_id && row.type === "risk_decisions"));
  const exceeds = Boolean(risk.candidate_worst_case_loss_usd && risk.allowed_risk_usd) && sign(subDec(risk.candidate_worst_case_loss_usd, risk.allowed_risk_usd)) > 0;

  return (
    <div className="flex flex-col gap-8">
      <div className="grid grid-cols-[minmax(0,1fr)_340px] gap-10 max-xl:grid-cols-1">
        <Section title="Última decisión" description="¿Por qué se aprobó o rechazó?">
          {risk.verdict ? (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={verdictTone(risk.verdict)}>{verdict(risk.verdict)}</Badge>
                <span className="num text-body text-fg">{risk.asset ?? "—"}</span>
                <Tooltip content={dateUTC(risk.decided_at)}>
                  <span className="text-caption text-fg-muted">{relative(risk.decided_at)}</span>
                </Tooltip>
              </div>
              {risk.reasons?.length ? (
                <ul className="flex flex-col gap-1">
                  {risk.reasons.map((reason) => (
                    <li key={reason} className="flex items-baseline gap-2 text-body text-fg-2">
                      <span aria-hidden className="size-1 shrink-0 translate-y-[-2px] rounded-full bg-fg-muted" />
                      {riskReason(reason)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-body-2 text-fg-muted">El motor no registró motivos para esta decisión.</p>
              )}
              {linked ? (
                <div>
                  <Button size="sm" variant="ghost" iconRight={ArrowRight} onClick={() => setSelected(linked)}>
                    Ver en la traza
                  </Button>
                </div>
              ) : null}
            </div>
          ) : (
            <EmptyState
              compact
              icon={ShieldCheck}
              title="Aún no hay decisiones de riesgo"
              description="El motor de riesgo evalúa cada propuesta revisada por el crítico con reglas deterministas. Cuando llegue la primera, verás aquí el veredicto y sus motivos."
            />
          )}
        </Section>
        <Section title="Cálculo del tamaño">
          {risk.verdict ? (
            <KeyValue
              items={[
                { label: "Riesgo base", value: money(risk.base_risk_usd), mono: true },
                { label: "Riesgo permitido", value: money(risk.allowed_risk_usd), mono: true },
                {
                  label: "Pérdida máxima",
                  value: <span className={exceeds ? "text-negative" : undefined}>{money(risk.candidate_worst_case_loss_usd)}</span>,
                  mono: true,
                },
                { label: "Ganancia protegida", value: money(risk.protected_profit_floor_usd), mono: true },
                {
                  label: "Nivel de protección",
                  value: (
                    <span>
                      Nivel {level ?? "—"} · riesgo <span className="num">{multiplier(risk.risk_multiplier ?? ladder?.risk_multiplier)}</span>
                    </span>
                  ),
                },
                { label: "Modo real", value: risk.live_trading_allowed ? <span className="text-warning">Permitido</span> : <span className="text-fg-2">No autorizado</span> },
              ]}
            />
          ) : (
            <p className="text-body-2 text-fg-muted">Sin decisión, no hay cálculo de tamaño que mostrar.</p>
          )}
        </Section>
      </div>
      <Section title="Propuestas recientes" description="Lo que la estrategia quiso hacer, antes de pasar por el crítico y el control de riesgo.">
        <TradeProposals proposals={snapshot.proposals} />
      </Section>
      <Section title="Paso a paso" description="De la propuesta al veredicto. Pulsa un paso para ver todos sus datos.">
        <Timeline rows={trace} onOpen={setSelected} />
      </Section>
      <TraceSheet row={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

/* ───────────────────────────── Límites ───────────────────────────── */

function LadderTable({ ladder, current }: { ladder: LadderLevel[]; current: number | null }) {
  const last = ladder.length ? ladder[ladder.length - 1] : undefined;
  return (
    <DataTable
      label="Escalera de protección"
      data={ladder}
      getRowId={(row) => String(row.level)}
      selectedId={current === null ? null : String(current)}
      empty={
        <EmptyState
          compact
          title="La escalera no está configurada"
          description="Sin escalera el riesgo no se reduce al acumular ganancias en el día."
        />
      }
      columns={[
        {
          header: "Nivel",
          id: "level",
          cell: ({ row }) => (
            <span className="inline-flex items-center gap-2">
              <span className="num text-fg">{row.original.level}</span>
              {row.original.level === current ? <Badge tone="accent">Actual</Badge> : null}
            </span>
          ),
        },
        {
          header: "Resultado del día",
          id: "range",
          cell: ({ row }) => (
            <span className="num text-fg-2">
              {row.original === last ? `desde ${pctShort(row.original.minimum_pnl_percent)}` : `${pctShort(row.original.minimum_pnl_percent)} – ${pctShort(row.original.maximum_pnl_percent)}`}
            </span>
          ),
        },
        {
          header: "Multiplicador de riesgo",
          id: "multiplier",
          meta: { align: "right" },
          cell: ({ row }) =>
            sign(row.original.risk_multiplier) === 0 ? <span className="text-fg-muted">Sin entradas</span> : <span className="num">{multiplier(row.original.risk_multiplier)}</span>,
        },
        { header: "Puntuación mínima", id: "score", meta: { align: "right" }, cell: ({ row }) => <span className="num">{row.original.minimum_score}</span> },
        { header: "Confirmaciones", id: "confirmations", meta: { align: "right" }, cell: ({ row }) => <span className="num">{row.original.minimum_confirmations}</span> },
        { header: "Ganancia protegida", id: "protected", meta: { align: "right" }, cell: ({ row }) => <span className="num">{fractionPct(row.original.protected_fraction)}</span> },
      ]}
    />
  );
}

/** "35%" rather than "35.00%" for configured limits. */
function pctShort(value: string | number | null | undefined): string {
  return `${decimal(value ?? "0", { dp: 2, trim: true })}%`;
}

/** Display-only example amounts ("con tu cuenta…"); never used for decisions. */
function share(capital: string | null, percent: string | number): string {
  const value = (Number(capital ?? 0) * Number(percent)) / 100;
  return capital && Number.isFinite(value) ? money(value.toFixed(0), { dp: 0 }) : "—";
}

type NamedProfile = Exclude<RiskProfileName, "personalizado">;

const PROFILES: Array<{ id: NamedProfile; title: string; blurb: string }> = [
  { id: "conservador", title: "Conservador", blurb: "Pocas pérdidas, crecimiento lento. Para validar una estrategia nueva." },
  { id: "medio", title: "Medio", blurb: "Lo habitual en un trader profesional: suficiente para medir sin que un mal día arruine el año." },
  { id: "alto", title: "Alto", blurb: "Resultados más rápidos y más ruido. Solo con una estrategia ya probada." },
];

function ProfileCard({ id, title, blurb, values, capital, selected, onSelect }: { id: NamedProfile; title: string; blurb: string; values: RiskProfileValues; capital: string | null; selected: boolean; onSelect: (id: NamedProfile) => void }) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={() => onSelect(id)}
      className={cn(
        "flex flex-col gap-3 rounded-lg border p-4 text-left transition-colors",
        selected ? "border-accent bg-accent-soft" : "border-line bg-surface-1 hover:border-line-strong",
      )}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="text-section font-semibold text-fg">{title}</span>
        {selected ? <Badge tone="accent">Activo</Badge> : null}
      </span>
      <span className="text-body-2 text-fg-muted">{blurb}</span>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-body-2">
        <dt className="text-fg-muted">Por operación</dt>
        <dd className="num text-right text-fg">{pctShort(values.base_risk_percent)} · {share(capital, values.base_risk_percent)}</dd>
        <dt className="text-fg-muted">Al día</dt>
        <dd className="num text-right text-fg">{pctShort(values.daily_loss_percent)} · {share(capital, values.daily_loss_percent)}</dd>
        <dt className="text-fg-muted">A la semana</dt>
        <dd className="num text-right text-fg">{pctShort(values.weekly_loss_percent)} · {share(capital, values.weekly_loss_percent)}</dd>
        <dt className="text-fg-muted">Caída máxima</dt>
        <dd className="num text-right text-fg">{pctShort(values.max_account_drawdown_percent)}</dd>
      </dl>
    </button>
  );
}

function EditableLimits({ config }: { config: PublicConfig }) {
  const risk = config.risk;
  // The broker account is the capital. Display-only; the RiskEngine sizes with Decimal.
  const capital = useBrokerStatus().data?.account?.equity ?? null;
  const [custom, setCustom] = useState(risk.profile === "personalizado");
  const initial = useMemo<ConfigPatch>(
    () => ({
      base_risk_percent: risk.base_risk_percent,
      daily_loss_percent: risk.daily_loss_percent,
      weekly_loss_percent: risk.weekly_loss_percent,
      max_account_drawdown_percent: risk.max_account_drawdown_percent,
      max_profit_giveback_percent: risk.max_profit_giveback_percent,
      max_trades_per_day: risk.max_trades_per_day,
      max_position_hold_minutes: risk.max_position_hold_minutes,
    }),
    [risk],
  );
  const { draft, set, changes, reset } = useDraft(initial);
  const selected = draft.risk_profile ?? risk.profile;
  const envelope = config.autonomy.envelope;
  const range = (key: string) => (envelope[key] ? `Entre ${decimal(envelope[key].minimum, { trim: true })} y ${decimal(envelope[key].maximum, { trim: true })}.` : "");
  const decimalField = (key: keyof ConfigPatch, label: string, description: ReactNode) => (
    <SettingRow label={label} description={<>{description} {range(key)}</>}>
      <DecimalInput value={String(draft[key] ?? "")} onValueChange={(value) => set(key, value as never)} suffix="%" aria-label={label} />
    </SettingRow>
  );
  const intField = (key: "max_trades_per_day" | "max_position_hold_minutes", label: string, description: string, suffix: string, max: number) => (
    <SettingRow label={label} description={`${description} ${range(key)}`}>
      <Input
        type="number"
        min={1}
        max={max}
        step={1}
        value={draft[key] ?? ""}
        onChange={(event) => set(key, event.target.value === "" ? undefined : Math.trunc(Number(event.target.value)))}
        suffix={suffix}
        aria-label={label}
      />
    </SettingRow>
  );

  return (
    <Section
      title="Perfil de riesgo"
      description={capital ? `Porcentajes sobre el patrimonio de tu cuenta Alpaca Paper (${money(capital, { dp: 0 })}).` : "Conecta tu cuenta Alpaca Paper para ver los importes en dólares."}
    >
      <div role="radiogroup" aria-label="Perfil de riesgo" className="grid grid-cols-3 gap-4 max-lg:grid-cols-1">
        {PROFILES.map((profile) => (
          <ProfileCard
            key={profile.id}
            {...profile}
            values={config.risk_profiles[profile.id]}
            capital={capital}
            selected={selected === profile.id}
            onSelect={(id) => {
              reset();
              if (id !== risk.profile) set("risk_profile", id);
            }}
          />
        ))}
      </div>
      {selected === "personalizado" ? <p className="mt-3 text-body-2 text-fg-muted">Estás usando valores personalizados. Elige un perfil para volver a sus valores.</p> : null}
      <button type="button" className="mt-5 self-start text-body-2 text-accent-text hover:underline" aria-expanded={custom} onClick={() => setCustom((open) => !open)}>
        {custom ? "Ocultar ajustes personalizados" : "Personalizar"}
      </button>
      {custom ? (
        <div className="mt-2">
          {decimalField("base_risk_percent", "Riesgo por operación", "Lo máximo que se puede perder en una operación.")}
          {decimalField("daily_loss_percent", "Pérdida máxima del día", "Al llegar aquí no se abren más operaciones hasta mañana.")}
          {decimalField("weekly_loss_percent", "Pérdida máxima de la semana", "Igual que la diaria, en 7 días. Es una protección fija.")}
          {decimalField("max_account_drawdown_percent", "Caída máxima de la cuenta", "Desde el máximo que alcanzó la cuenta.")}
          {decimalField("max_profit_giveback_percent", "Ganancia que se puede devolver", "Si el día devuelve más de esta parte de lo ganado, se deja de operar hasta mañana.")}
          {intField("max_trades_per_day", "Operaciones por día", "Entradas nuevas por sesión; las salidas no cuentan.", "máx.", 20)}
          {intField("max_position_hold_minutes", "Tiempo máximo abierta", "Pasado este tiempo la operación se cierra aunque no toque el stop ni el objetivo.", "min", 1440)}
        </div>
      ) : null}
      <ApplyBar changes={changes} onReset={reset} />
    </Section>
  );
}

function Fixed({ label, value, hint }: { label: string; value: ReactNode; hint?: "spread" | "slippage" | "drawdown" | "exposure" | "bps" }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line py-2 last:border-0">
      <span className="flex items-center gap-1.5 text-body-2 text-fg-2">
        {label}
        {hint ? <InfoHint term={hint} /> : null}
      </span>
      <span className="num text-body-2 text-fg">{value}</span>
    </div>
  );
}

function LimitsView({ snapshot }: { snapshot: Snapshot }) {
  const r: RiskLimits = snapshot.config.risk;
  const current = snapshot.risk.level ?? null;

  return (
    <div className="stagger flex flex-col gap-9">
      <p className="flex items-start gap-2 text-body-2 text-fg-2">
        <ShieldCheck size={16} strokeWidth={1.5} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden />
        El control de riesgo aplica estos límites a cada operación y tiene la última palabra, por encima de la IA y del optimizador.
      </p>
      <EditableLimits config={snapshot.config} />
      <details className="group flex flex-col gap-9">
        <summary className="cursor-pointer text-body-2 text-fg-2 hover:text-fg">Detalles avanzados: protecciones fijas y escalera de beneficios</summary>
        <div className="mt-6 flex flex-col gap-9">
      <Section
        title={
          <span className="inline-flex items-center gap-2">
            <Lock size={14} aria-hidden /> Fijos por seguridad
          </span>
        }
        description="No se cambian desde la app: protegen la cuenta aunque todo lo demás falle."
      >
        <div className="grid grid-cols-3 gap-x-10 max-xl:grid-cols-1">
          <div>
            <Fixed label="Spread máximo" hint="spread" value={bps(r.max_spread_bps)} />
            <Fixed label="Deslizamiento máximo" hint="slippage" value={bps(r.max_slippage_bps)} />
            <Fixed label="Liquidez mínima" value={money(r.min_liquidity_usd, { dp: 0 })} />
          </div>
          <div>
            <Fixed label="Caída máxima de la cuenta" hint="drawdown" value={pctShort(r.max_account_drawdown_percent)} />
            <Fixed label="Ganancia máxima del día" value={r.daily_profit_hard_cap_usd ? money(r.daily_profit_hard_cap_usd) : "Sin tope"} />
            <Fixed label="Pérdidas seguidas" value={`${r.max_losing_streak} y se pausa ${r.cooldown_minutes_after_loss} min`} />
          </div>
          <div>
            <Fixed label="Operaciones abiertas a la vez" value={`Máximo ${r.max_positions}`} />
            <Fixed label="Capital invertido en total" hint="exposure" value={pctShort(r.max_total_exposure_percent)} />
            <Fixed label="Capital en un solo instrumento" value={pctShort(r.max_asset_exposure_percent)} />
          </div>
        </div>
      </Section>
      <Section title="Escalera de protección" description="Cuanto más gana el día (en % de la cuenta), menos se arriesga por operación y más calidad se exige. Así una buena racha no se devuelve de golpe.">
        <LadderTable ladder={r.ladder} current={current} />
      </Section>
        </div>
      </details>
    </div>
  );
}

/* ───────────────────────────── Screen ───────────────────────────── */

export default function Riesgo() {
  const params = useParams<{ tab?: string }>();
  const [, navigate] = useLocation();
  const tab = useTab(params.tab, tabValues("riesgo"), "limites");

  return (
    <>
      <PageHeader title="Riesgo" tabs={DOMAINS.riesgo.tabs} tab={tab} onTab={(value) => navigate(`/riesgo/${value}`)} />
      <PageBody intro={tabPurpose("riesgo", tab)}>
        <SnapshotGate>{(snapshot) => (tab === "decisiones" ? <DecisionView snapshot={snapshot} /> : <LimitsView snapshot={snapshot} />)}</SnapshotGate>
      </PageBody>
    </>
  );
}
