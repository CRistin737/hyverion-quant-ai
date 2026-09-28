import { ArrowRight, Check, History, Plus, Sparkles, Undo2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "wouter";

import { useApplyChange, useChanges, useOptimizer, useRollbackComponent, useTransitionChange } from "@/api/queries";
import type { ChangeItem, ReplayStats, Snapshot } from "@/api/types";
import { changeProposalStatus, changeProposalTone, componentName, isStrategy, optimizerOutcome } from "@/i18n/labels";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Section, Signed } from "@/ui/data";
import { Badge, EmptyState, ErrorState, InfoHint, SkeletonRows } from "@/ui/feedback";
import { useToast } from "@/ui/toast";

import { NewProposalDialog } from "./NewProposal";
import { ActionDialog, errorText, When } from "../shared";

const PENDING = new Set(["PROPOSED", "TESTING", "READY_FOR_REVIEW", "APPROVED"]);
const APPLICABLE_KINDS = new Set(["strategy_params", "agent_spec"]);
/** Next manual review step for changes that cannot be applied automatically. */
const NEXT_STEP: Record<string, { target: "TESTING" | "READY_FOR_REVIEW" | "APPROVED"; label: string }> = {
  PROPOSED: { target: "TESTING", label: "Pasar a prueba" },
  TESTING: { target: "READY_FOR_REVIEW", label: "Marcar lista" },
  READY_FOR_REVIEW: { target: "APPROVED", label: "Aprobar" },
};

export const canApply = (change: ChangeItem) =>
  APPLICABLE_KINDS.has(String(change.candidate_spec.kind ?? "")) && (change.status === "READY_FOR_REVIEW" || change.status === "APPROVED");

function winRate(stats: ReplayStats): string {
  return stats.trades ? `${Math.round((stats.wins * 100) / stats.trades)} %` : "—";
}

/** Before → after with a bar per side sized by the net result. */
export function BeforeAfter({ before, after }: { before: ReplayStats; after: ReplayStats }) {
  const values = [Number(before.net_pnl_usd), Number(after.net_pnl_usd)];
  const scale = Math.max(1, ...values.map(Math.abs));
  const rows = [
    { label: "Ahora", stats: before, value: values[0]! },
    { label: "Con el cambio", stats: after, value: values[1]! },
  ];
  return (
    <div className="flex flex-col gap-2" aria-label="Resultado de la prueba antes y después del cambio">
      {rows.map((row) => (
        <div key={row.label} className="grid grid-cols-[104px_1fr_auto] items-center gap-3">
          <span className="text-body-2 text-fg-muted">{row.label}</span>
          <div className="relative h-2 rounded-full bg-surface-2">
            <span
              className={cn("absolute inset-y-0 left-0 rounded-full transition-[width] duration-[var(--duration-slow)]", row.value >= 0 ? "bg-positive" : "bg-negative")}
              style={{ width: `${Math.max(3, (Math.abs(row.value) / scale) * 100)}%` }}
            />
          </div>
          <span className="num whitespace-nowrap text-body-2">
            <Signed value={row.stats.net_pnl_usd} /> <span className="text-fg-muted">· {row.stats.trades} operaciones · acierto {winRate(row.stats)}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

const isFeature = (change: ChangeItem) => change.candidate_spec.kind === "feature_request";
const fromAi = (change: ChangeItem) => change.candidate_spec.origin === "ai_improver";
/** Deployed by the paper auto-promotion (after the forward shadow sessions). */
export const autoPromoted = (change: ChangeItem) => change.history.some((step) => (step.reason ?? "").startsWith("Autopromoción"));

function ChangeCard({ change, onApply, onReject, onStep }: { change: ChangeItem; onApply: () => void; onReject: () => void; onStep: () => void }) {
  const applicable = canApply(change);
  const step = NEXT_STEP[change.status];
  const feature = isFeature(change);
  const kind = feature ? "Herramienta nueva" : isStrategy(change.agent) ? "Estrategia" : "Instrucciones del agente";
  return (
    <li className="rounded-md bg-surface-1 p-4 shadow-[inset_0_0_0_1px_var(--border-subtle)]">
      <header className="flex flex-wrap items-center gap-2">
        <Badge>{kind}</Badge>
        {fromAi(change) ? <Badge tone="accent">Idea de la IA</Badge> : null}
        <span className="text-body font-semibold text-fg">{feature ? String(change.candidate_spec.title ?? "") : componentName(change.agent)}</span>
        {feature ? null : (
          <span className="num text-body-2 text-fg-2">
            v{change.current_version} <ArrowRight size={12} className="inline" aria-hidden /> v{change.candidate_version}
          </span>
        )}
        <Badge tone={changeProposalTone(change.status)}>{changeProposalStatus(change.status)}</Badge>
        <span className="flex-1" />
        <When iso={change.updated_at} className="text-caption text-fg-muted" />
      </header>
      <dl className="mt-3 grid grid-cols-[112px_1fr] gap-x-4 gap-y-2.5 text-body-2">
        <dt className="text-fg-muted">{feature ? "Qué pide" : "Qué cambia"}</dt>
        <dd className="text-fg">
          {feature ? (
            <p className="selectable">{String(change.candidate_spec.description ?? "")}</p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {change.affected_rules.map((rule) => (
                <li key={rule}>{rule}</li>
              ))}
            </ul>
          )}
        </dd>
        <dt className="text-fg-muted">Por qué</dt>
        <dd className="selectable text-fg-2">{change.reason}</dd>
        {change.experiment ? (
          <>
            <dt className="flex items-center gap-1 text-fg-muted">
              Prueba <InfoHint term="oos" label="fuera de muestra" />
            </dt>
            <dd className="flex flex-col gap-1.5">
              <BeforeAfter before={change.experiment.before} after={change.experiment.after} />
              <span className="text-caption text-fg-muted">
                {Math.round(change.experiment.minutes / 1440)} días de precios reales de {change.experiment.symbols.join(" y ")}; comparado con datos que no se usaron para elegir el ajuste.
              </span>
            </dd>
          </>
        ) : (
          <>
            <dt className="text-fg-muted">Mejora esperada</dt>
            <dd className="text-fg-2">{change.expected_improvement}</dd>
          </>
        )}
        <dt className="text-fg-muted">Riesgo</dt>
        <dd className="text-fg-2">{change.risk}</dd>
      </dl>
      <footer className="mt-4 flex flex-wrap items-center justify-end gap-2">
        {feature ? (
          <span className="mr-auto text-caption text-fg-muted">Nunca se aplica sola. Si la aceptas, se crea una ficha para desarrollarla y revisarla.</span>
        ) : !applicable && step ? (
          <span className="mr-auto text-caption text-fg-muted">Este cambio no se aplica solo; cuando lo apruebes, hay que hacerlo en el código.</span>
        ) : null}
        <Button size="sm" variant="ghost" icon={X} onClick={onReject}>
          Rechazar
        </Button>
        {applicable ? (
          <Button size="sm" variant="primary" icon={Check} onClick={onApply}>
            Aprobar y aplicar
          </Button>
        ) : step ? (
          <Button size="sm" variant={feature ? "primary" : "secondary"} onClick={onStep}>
            {feature && step.target === "APPROVED" ? "Aceptar" : step.label}
          </Button>
        ) : null}
      </footer>
    </li>
  );
}

function InUse({ changes, onUndo }: { changes: ChangeItem[]; onUndo: (change: ChangeItem) => void }) {
  if (!changes.length)
    return <EmptyState compact title="Todo usa su versión original" description="Cuando se aplique una mejora (tuya o de la IA tras probarse), aparecerá aquí con un botón para deshacerla." />;
  return (
    <ul className="flex flex-col divide-y divide-line">
      {changes.map((change) => (
        <li key={change.id} className="flex flex-wrap items-center gap-3 py-2.5">
          <span className="text-body font-medium text-fg">{componentName(change.agent)}</span>
          <Badge tone="positive" mono>
            v{change.candidate_version}
          </Badge>
          {autoPromoted(change) ? <Badge tone="accent">Aplicado solo tras probarse</Badge> : null}
          <span className="min-w-0 flex-1 truncate text-body-2 text-fg-2">{change.affected_rules.join(" · ")}</span>
          <span className="text-caption text-fg-muted">
            en uso desde <When iso={change.updated_at} className="text-caption" />
          </span>
          <Link href={`/inteligencia/agentes/${encodeURIComponent(change.agent)}`} className="text-body-2 text-fg-2 underline-offset-2 hover:text-fg hover:underline">
            Ver historial
          </Link>
          <Button size="sm" variant="secondary" icon={Undo2} onClick={() => onUndo(change)}>
            Deshacer
          </Button>
        </li>
      ))}
    </ul>
  );
}

function Optimizer() {
  const { status, run } = useOptimizer();
  const toast = useToast();
  const data = status.data;
  const running = Boolean(data?.running) || run.isPending;
  return (
    <Section
      title="Búsqueda automática de mejoras"
      description="Una vez al día el sistema prueba ajustes de cada estrategia con precios reales. Solo te propone uno si demuestra ser mejor con datos que no usó para elegirlo."
      actions={
        <Button
          size="sm"
          icon={Sparkles}
          loading={running}
          onClick={() =>
            run.mutate(undefined, {
              onSuccess: () => toast({ tone: "info", title: "Buscando mejoras", description: "Tarda alrededor de un minuto por estrategia." }),
              onError: (error) => toast({ tone: "error", title: "No se pudo buscar mejoras", description: errorText(error) }),
            })
          }
        >
          {running ? "Buscando…" : "Buscar mejoras"}
        </Button>
      }
    >
      {data?.error ? <p className="text-body-2 text-negative">{errorText(new Error(data.error))}</p> : null}
      {data?.last_run ? (
        <ul className="flex flex-col gap-3">
          {data.last_run.results.map((result) => (
            <li key={result.strategy_id} className="flex flex-col gap-1.5">
              <div className="flex flex-wrap items-center gap-2 text-body-2">
                <span className="font-medium text-fg">{componentName(result.strategy_id)}</span>
                <span className="text-fg-2">{optimizerOutcome(result.outcome)}</span>
              </div>
              {result.outcome === "not_proven" && result.before && result.after ? (
                <div className="flex flex-col gap-1.5 pl-3 shadow-[inset_2px_0_0_var(--border-default)]">
                  <span className="text-caption text-fg-muted">Mejor opción probada: {(result.changes ?? []).join(" · ") || "sin cambios"}</span>
                  <BeforeAfter before={result.before} after={result.after} />
                </div>
              ) : null}
            </li>
          ))}
          <li className="text-caption text-fg-muted">
            Última búsqueda <When iso={data.last_run.created_at} className="text-caption" /> con {data.last_run.symbols.join(" y ")}.
          </li>
        </ul>
      ) : status.isLoading ? (
        <SkeletonRows rows={2} />
      ) : (
        <p className="text-body-2 text-fg-muted">Todavía no se ha hecho ninguna búsqueda. La primera ocurre cuando el motor lleva un rato encendido, o ahora con «Buscar mejoras».</p>
      )}
    </Section>
  );
}

function HistoryList({ changes }: { changes: ChangeItem[] }) {
  const [open, setOpen] = useState(false);
  if (!changes.length) return null;
  return (
    <Section
      title="Historial de decisiones"
      description="Cambios ya decididos: rechazados, deshechos o reemplazados."
      actions={
        <Button size="sm" variant="ghost" icon={History} onClick={() => setOpen((value) => !value)} aria-expanded={open}>
          {open ? "Ocultar" : `Ver ${changes.length}`}
        </Button>
      }
    >
      {open ? (
        <ol className="flex flex-col divide-y divide-line">
          {changes.map((change) => {
            const last = change.history[change.history.length - 1];
            return (
              <li key={change.id} className="flex flex-wrap items-center gap-3 py-2 text-body-2">
                <Badge tone={changeProposalTone(change.status)}>{changeProposalStatus(change.status)}</Badge>
                <span className="font-medium text-fg">{componentName(change.agent)}</span>
                <span className="num text-fg-muted">
                  v{change.current_version} → v{change.candidate_version}
                </span>
                <span className="min-w-0 flex-1 truncate text-fg-2">{last?.reason ?? change.reason}</span>
                <When iso={change.updated_at} className="text-caption text-fg-muted" />
              </li>
            );
          })}
        </ol>
      ) : null}
    </Section>
  );
}

type Pending = { kind: "apply" | "reject" | "step" | "undo"; change: ChangeItem };

export function Cambios({ snapshot }: { snapshot: Snapshot }) {
  const query = useChanges();
  const apply = useApplyChange();
  const transition = useTransitionChange();
  const rollback = useRollbackComponent();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);

  const changes = query.data ?? [];
  const { inbox, inUse, decided } = useMemo(() => {
    const deployed = changes.filter((c) => c.status === "DEPLOYED");
    // Only the newest deployed change per component is the one in use.
    const current = new Map<string, ChangeItem>();
    for (const change of deployed) if (!current.has(change.agent)) current.set(change.agent, change);
    const live = [...current.values()];
    return {
      inbox: changes.filter((c) => PENDING.has(c.status)),
      inUse: live,
      decided: changes.filter((c) => !PENDING.has(c.status) && !live.includes(c)),
    };
  }, [changes]);

  const mutation = pending?.kind === "apply" ? apply : pending?.kind === "undo" ? rollback : transition;
  const confirm = (reason: string) => {
    if (!pending) return;
    const { change, kind } = pending;
    const done = (title: string, description?: string) => {
      toast({ tone: "success", title, description });
      setPending(null);
    };
    if (kind === "apply")
      apply.mutate({ id: change.id, reason }, { onSuccess: (r) => done("Cambio aplicado", `${componentName(r.component_id)} usa la versión ${r.version} desde el próximo ciclo.`) });
    else if (kind === "undo")
      rollback.mutate({ componentId: change.agent, reason }, { onSuccess: (r) => done("Cambio deshecho", `${componentName(r.component_id)} vuelve a la versión ${r.active_version}.`) });
    else if (kind === "reject") transition.mutate({ id: change.id, target: "REJECTED", reason }, { onSuccess: () => done("Cambio rechazado") });
    else {
      const step = NEXT_STEP[change.status];
      if (step)
        transition.mutate(
          { id: change.id, target: step.target, reason },
          {
            onSuccess: (result) => {
              const path = (result as unknown as { request_path?: unknown }).request_path;
              done(isFeature(change) && step.target === "APPROVED" ? "Herramienta aceptada" : "Paso registrado", typeof path === "string" ? `Ficha creada: ${path}` : undefined);
            },
          },
        );
    }
  };

  const dialog = pending
    ? {
        apply: {
          title: `Aprobar y aplicar · ${componentName(pending.change.agent)}`,
          description: `Pasa a la versión ${pending.change.candidate_version} en el próximo ciclo del motor. Puedes deshacerlo cuando quieras. Los límites de riesgo no cambian.`,
          confirm: "Aprobar y aplicar",
          reason: "Aprobado tras revisar la prueba",
        },
        reject: { title: `Rechazar · ${componentName(pending.change.agent)}`, description: "El cambio queda descartado y registrado con tu motivo.", confirm: "Rechazar", reason: "" },
        step: { title: `${NEXT_STEP[pending.change.status]?.label ?? "Avanzar"} · ${componentName(pending.change.agent)}`, description: "El paso queda registrado con tu motivo.", confirm: NEXT_STEP[pending.change.status]?.label ?? "Confirmar", reason: "" },
        undo: {
          title: `Deshacer · ${componentName(pending.change.agent)}`,
          description: `Vuelve a la versión ${pending.change.current_version} en el próximo ciclo. La versión ${pending.change.candidate_version} queda en el historial.`,
          confirm: "Deshacer",
          reason: "",
        },
      }[pending.kind]
    : null;

  let inboxBody;
  if (query.isLoading) inboxBody = <SkeletonRows rows={3} />;
  else if (query.error)
    inboxBody = (
      <ErrorState
        title="No se pudieron cargar los cambios"
        impact="Nada se aplica sin tu aprobación, así que no hay riesgo mientras tanto."
        detail={errorText(query.error)}
        onRetry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    );
  else if (!inbox.length)
    inboxBody = <EmptyState compact title="Nada por revisar" description="Cuando el sistema encuentre una mejora demostrada, aparecerá aquí para que decidas." />;
  else
    inboxBody = (
      <ul className="stagger flex flex-col gap-3">
        {inbox.map((change) => (
          <ChangeCard
            key={change.id}
            change={change}
            onApply={() => setPending({ kind: "apply", change })}
            onReject={() => setPending({ kind: "reject", change })}
            onStep={() => setPending({ kind: "step", change })}
          />
        ))}
      </ul>
    );

  return (
    <div className="flex flex-col gap-9">
      <Section
        title={inbox.length ? `Por revisar (${inbox.length})` : "Por revisar"}
        description="Las instrucciones de los agentes y las herramientas nuevas esperan tu visto bueno. Los ajustes de estrategia probados se aplican solos en paper tras 5 sesiones en sombra."
        actions={
          <Button size="sm" variant="ghost" icon={Plus} onClick={() => setCreating(true)}>
            Proponer un cambio
          </Button>
        }
      >
        {inboxBody}
      </Section>
      <Section title="En uso" description="Mejoras que el sistema ya está usando. Cualquiera se deshace con un clic.">
        <InUse changes={inUse} onUndo={(change) => setPending({ kind: "undo", change })} />
      </Section>
      <Optimizer />
      <HistoryList changes={decided} />
      <NewProposalDialog open={creating} onOpenChange={setCreating} agents={snapshot.agents} />
      <ActionDialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPending(null);
            mutation.reset();
          }
        }}
        title={dialog?.title ?? ""}
        description={dialog?.description}
        confirmLabel={dialog?.confirm ?? "Confirmar"}
        destructive={pending?.kind === "reject" || pending?.kind === "undo"}
        requireReason
        defaultReason={dialog?.reason}
        reasonHint="Queda registrado junto a la decisión."
        loading={mutation.isPending}
        error={mutation.error ? errorText(mutation.error) : null}
        onConfirm={confirm}
      />
    </div>
  );
}
