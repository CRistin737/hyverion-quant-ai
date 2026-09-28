import { Bot, ChevronRight, FileText, Undo2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useLocation } from "wouter";

import { useAgentSpec, useAiModels, useComponentHistory, useComponents, useRollbackComponent } from "@/api/queries";
import type { Agent, ComponentVersion, Snapshot, StrategyParams, VersionEntry } from "@/api/types";
import {
  agentDeterministic,
  agentName,
  agentRole,
  agentStatus,
  agentTone,
  changeProposalStatus,
  componentName,
  errorCode,
  researchRegime,
  strategyName,
} from "@/i18n/labels";
import { dateUTC, decimal } from "@/lib/format";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Section, Signed } from "@/ui/data";
import { Badge, EmptyState, ErrorState, SkeletonRows, Status } from "@/ui/feedback";
import { Markdown } from "@/ui/markdown";
import { Sheet } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

import { ActionDialog, errorText, When } from "../shared";

function latency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${decimal(ms / 1000, { dp: 1 })} s`;
}

/** Strategy parameters said in words: "Stop 1 % · objetivo 2 veces el stop · cierre a los 60 min". */
export function paramsText(params: StrategyParams | null | undefined): string {
  if (!params) return "";
  const parts = [`Stop ${params.stop_percent} %`, `objetivo ${params.reward_multiple} veces el stop`, `cierre a los ${params.horizon_minutes} min`];
  if (params.blocked_regimes.length) parts.push(`no opera en ${params.blocked_regimes.map((r) => researchRegime(r).toLowerCase()).join(" ni en ")}`);
  return parts.join(" · ");
}

function Row({ title, version, subtitle, meta, error, onOpen }: { title: string; version: string; subtitle: string; meta: ReactNode; error?: string | null; onOpen: () => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="grid w-full grid-cols-[minmax(0,1fr)_auto_16px] items-center gap-x-4 border-b border-line py-3 text-left transition-colors duration-[var(--duration-fast)] hover:bg-surface-hover"
      >
        <span className="flex min-w-0 flex-col gap-0.5 pl-1">
          <span className="flex items-center gap-2">
            <span className="text-body font-medium text-fg">{title}</span>
            <Badge mono>v{version}</Badge>
          </span>
          <span className="truncate text-body-2 text-fg-2">{subtitle}</span>
          {error ? <span className="text-body-2 text-negative">Último error: {errorCode(error)}</span> : null}
        </span>
        <span className="flex flex-col items-end gap-1">{meta}</span>
        <ChevronRight size={14} className="text-fg-muted" aria-hidden />
      </button>
    </li>
  );
}

function AgentMeta({ agent }: { agent: Agent }) {
  return (
    <>
      <Status tone={agentTone(agent.status)} pulse={agent.status === "ACTIVE"}>
        {agentStatus(agent.status)}
      </Status>
      <span className="num text-caption text-fg-muted">
        {agent.provider ? `${agent.model ?? agent.provider} · ${latency(agent.latency_ms)}` : "Sin ejecuciones aún"}
      </span>
    </>
  );
}

/** Accuracy bar for one version; the previous version's bar is drawn faintly behind it. */
function ResultsLine({ entry, previous }: { entry: VersionEntry; previous?: VersionEntry }) {
  const { results } = entry;
  if (!results.samples)
    return <p className="text-caption text-fg-muted">{entry.active ? "Todavía sin operaciones cerradas con esta versión." : "Sin operaciones cerradas en ese periodo."}</p>;
  const now = Number(results.accuracy_percent ?? 0);
  const before = previous?.results.samples ? Number(previous.results.accuracy_percent ?? 0) : null;
  const diff = before === null ? null : now - before;
  return (
    <div className="flex flex-col gap-1">
      <div className="relative h-1.5 rounded-full bg-surface-2" aria-hidden>
        {before !== null ? <span className="absolute inset-y-0 left-0 rounded-full bg-fg-muted/30" style={{ width: `${before}%` }} /> : null}
        <span className="absolute inset-y-0 left-0 rounded-full bg-fg transition-[width] duration-[var(--duration-slow)]" style={{ width: `${now}%` }} />
      </div>
      <p className="text-body-2 text-fg-2">
        Acierto real <span className="num font-medium text-fg">{results.accuracy_percent} %</span> en {results.samples} operaciones
        {results.net_pnl_usd ? (
          <>
            {" "}
            · <Signed value={results.net_pnl_usd} />
          </>
        ) : null}
        {diff !== null ? (
          <span className={cn("num", diff >= 0 ? "text-positive" : "text-negative")}>
            {" "}
            ({diff >= 0 ? "+" : ""}
            {diff.toFixed(1)} puntos frente a la versión anterior)
          </span>
        ) : null}
      </p>
    </div>
  );
}

function Timeline({ entries, onUndo }: { entries: VersionEntry[]; onUndo: () => void }) {
  const ordered = [...entries].reverse();
  return (
    <ol className="relative flex flex-col gap-6 border-l border-line pl-5">
      {ordered.map((entry, index) => {
        const previous = ordered[index + 1];
        return (
          <li key={`${entry.version}-${entry.active_from}`} className="relative flex flex-col gap-2">
            <span className={cn("absolute -left-[25px] top-1 size-2.5 rounded-full shadow-[0_0_0_3px_var(--surface-1)]", entry.active ? "bg-fg" : "bg-fg-muted")} aria-hidden />
            <div className="flex flex-wrap items-center gap-2">
              <span className="num text-body font-semibold text-fg">v{entry.version}</span>
              {entry.active ? <Badge tone="positive">En uso</Badge> : null}
              <span className="text-caption text-fg-muted">
                {dateUTC(entry.active_from, { seconds: false })}
                {entry.active_to ? ` → ${dateUTC(entry.active_to, { seconds: false })}` : " → hoy"}
              </span>
            </div>
            {entry.proposal ? (
              <dl className="grid grid-cols-[84px_1fr] gap-x-3 gap-y-1 text-body-2">
                <dt className="text-fg-muted">Qué cambió</dt>
                <dd className="text-fg">{entry.proposal.affected_rules.join(" · ")}</dd>
                <dt className="text-fg-muted">Por qué</dt>
                <dd className="text-fg-2">{entry.proposal.reason}</dd>
                <dt className="text-fg-muted">Decisión</dt>
                <dd className="text-fg-2">
                  {changeProposalStatus(entry.proposal.status)} · «{entry.reason}»
                </dd>
              </dl>
            ) : (
              <p className="text-body-2 text-fg-2">{entry.reason ?? "Versión inicial"}</p>
            )}
            {entry.params ? <p className="text-caption text-fg-muted">{paramsText(entry.params)}</p> : null}
            <ResultsLine entry={entry} previous={previous} />
            {entry.active && entry.proposal ? (
              <div>
                <Button size="sm" variant="secondary" icon={Undo2} onClick={onUndo}>
                  Deshacer este cambio
                </Button>
              </div>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function Spec({ agentId }: { agentId: string }) {
  const spec = useAgentSpec(agentId);
  if (spec.isLoading) return <SkeletonRows rows={4} />;
  if (spec.error) return <ErrorState title="No se pudieron leer las instrucciones" detail={errorText(spec.error)} onRetry={() => void spec.refetch()} />;
  return (
    <div className="selectable rounded-md bg-surface-2 p-4 text-body-2">
      <Markdown source={spec.data?.spec_markdown ?? ""} />
    </div>
  );
}

function ComponentSheet({ componentId, onClose }: { componentId: string | null; onClose: () => void }) {
  const history = useComponentHistory(componentId);
  const rollback = useRollbackComponent();
  const toast = useToast();
  const [undoing, setUndoing] = useState(false);
  const [showSpec, setShowSpec] = useState(false);
  const isAgent = componentId !== null && history.data?.[0]?.kind !== "strategy";
  const active = history.data?.find((entry) => entry.active);

  return (
    <Sheet
      open={componentId !== null}
      onOpenChange={(open) => {
        if (!open) {
          setShowSpec(false);
          onClose();
        }
      }}
      title={componentId ? `${componentName(componentId)}${active ? ` · v${active.version}` : ""}` : ""}
      subtitle={isAgent ? agentRole(componentId ?? "", "") : "Estrategia de trading"}
      actions={
        isAgent ? (
          <Button size="sm" variant="ghost" icon={FileText} onClick={() => setShowSpec((value) => !value)} aria-expanded={showSpec}>
            {showSpec ? "Ver historial" : "Ver instrucciones"}
          </Button>
        ) : null
      }
    >
      {componentId && showSpec ? (
        <Spec agentId={componentId} />
      ) : history.isLoading ? (
        <SkeletonRows rows={5} />
      ) : history.error ? (
        <ErrorState title="No se pudo cargar el historial" detail={errorText(history.error)} onRetry={() => void history.refetch()} />
      ) : history.data?.length ? (
        <div className="flex flex-col gap-4">
          <p className="text-body-2 text-fg-2">Cada versión con lo que cambió, por qué, quién lo aprobó y cómo le fue de verdad mientras estuvo en uso.</p>
          <Timeline entries={history.data} onUndo={() => setUndoing(true)} />
        </div>
      ) : (
        <EmptyState compact title="Sin versiones registradas" description="La primera versión se registra al arrancar el núcleo." />
      )}
      <ActionDialog
        open={undoing}
        onOpenChange={(open) => {
          setUndoing(open);
          if (!open) rollback.reset();
        }}
        title={`Deshacer · ${componentName(componentId ?? "")}`}
        description="Vuelve a la versión anterior en el próximo ciclo del motor. La versión actual queda en el historial."
        confirmLabel="Deshacer"
        destructive
        requireReason
        loading={rollback.isPending}
        error={rollback.error ? errorText(rollback.error) : null}
        onConfirm={(reason) =>
          componentId &&
          rollback.mutate(
            { componentId, reason },
            {
              onSuccess: (result) => {
                toast({ tone: "success", title: "Cambio deshecho", description: `${componentName(result.component_id)} vuelve a la versión ${result.active_version}.` });
                setUndoing(false);
              },
            },
          )
        }
      />
    </Sheet>
  );
}

/** Same mapping as core/agent_pipeline.role_for: who decides runs on the decision model. */
const DECISION_AGENTS = new Set(["strategy", "critic", "position_manager", "session_guardian"]);

export function Agentes({ snapshot, selected }: { snapshot: Snapshot; selected: string | null }) {
  const components = useComponents();
  const models = useAiModels().data;
  const modelLine = (agentId: string) => {
    const role = agentId === "optimizer" ? "improvement" : DECISION_AGENTS.has(agentId) ? "decision" : "analysis";
    const id = models?.models[role] ?? snapshot.config.ai_models[role] ?? "default";
    const label = models?.catalog[models.primary_provider]?.find((item) => item.id === id)?.label ?? id;
    return `${role === "decision" ? "Decide" : role === "improvement" ? "Mejora" : "Analiza"} con ${label}`;
  };
  const [, navigate] = useLocation();
  const open = (id: string | null) => navigate(id ? `/inteligencia/agentes/${encodeURIComponent(id)}` : "/inteligencia/agentes", { replace: true });
  const versions = useMemo(() => new Map((components.data ?? []).map((item) => [item.component_id, item])), [components.data]);
  const ai = snapshot.agents.filter((a) => !agentDeterministic(a.agent_id));
  const deterministic = snapshot.agents.filter((a) => agentDeterministic(a.agent_id));
  const strategies: ComponentVersion[] = snapshot.config.strategies.enabled.map(
    (id) => versions.get(id) ?? { component_id: id, kind: "strategy", version: "1.0.0", params: null, active_from: "", overridden: false },
  );
  const failing = ai.filter((a) => a.status === "ERROR").length;

  if (!snapshot.agents.length) {
    return <EmptyState icon={Bot} title="No hay agentes registrados" description="El catálogo se carga al iniciar el núcleo. Reinicia la app si no aparece." />;
  }

  return (
    <div className="flex flex-col gap-9">
      <p className="text-body-2 text-fg-2">
        Pulsa cualquiera para ver su historial de versiones: qué cambió, por qué y si mejoró.
        {failing ? <span className="text-negative"> {failing === 1 ? "Un agente tiene error." : `${failing} agentes tienen error.`}</span> : null}
      </p>
      <div className="stagger grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-10 max-xl:grid-cols-1">
        <Section title="Agentes de IA" description="Analizan el mercado y proponen. Nunca envían órdenes ni ven credenciales del broker.">
          <ul>
            {ai.map((agent) => (
              <Row
                key={agent.agent_id}
                title={agentName(agent.agent_id)}
                version={versions.get(agent.agent_id)?.version ?? agent.version}
                subtitle={`${agentRole(agent.agent_id, agent.role)} · ${modelLine(agent.agent_id)}`}
                error={agent.last_error}
                meta={<AgentMeta agent={agent} />}
                onOpen={() => open(agent.agent_id)}
              />
            ))}
          </ul>
        </Section>
        <div className="flex flex-col gap-9">
          <Section title="Estrategias" description="Las reglas que deciden cuándo proponer una compra. El optimizador busca mejorarlas.">
            <ul>
              {strategies.map((strategy) => (
                <Row
                  key={strategy.component_id}
                  title={strategyName(strategy.component_id)}
                  version={strategy.version}
                  subtitle={paramsText(strategy.params) || "Valores de fábrica"}
                  meta={strategy.active_from ? <When iso={strategy.active_from} className="text-caption text-fg-muted" /> : null}
                  onOpen={() => open(strategy.component_id)}
                />
              ))}
            </ul>
          </Section>
          <Section title="Reglas fijas" description="Funcionan aunque la IA no esté disponible.">
            <ul>
              {deterministic.map((agent) => (
                <Row
                  key={agent.agent_id}
                  title={agentName(agent.agent_id)}
                  version={versions.get(agent.agent_id)?.version ?? agent.version}
                  subtitle={agentRole(agent.agent_id, agent.role)}
                  meta={<Badge>Determinista</Badge>}
                  onOpen={() => open(agent.agent_id)}
                />
              ))}
            </ul>
          </Section>
        </div>
      </div>
      <ComponentSheet componentId={selected} onClose={() => open(null)} />
    </div>
  );
}
