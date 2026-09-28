import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, BadgeCheck, Search, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { SNAPSHOT_KEY, useApi } from "@/api/provider";
import { useKnowledge, useMemoryCandidates, useMemoryConflicts, useMemoryStatus } from "@/api/queries";
import type { KnowledgeItem, MemoryStatus } from "@/api/types";
import { candidateStatus, conflictType, health, healthTone, knowledgeStatus, knowledgeStatusTone, memoryComponent, memoryType, memoryDetail } from "@/i18n/labels";
import { dateUTC, decimal, money, pct, shortId } from "@/lib/format";
import { Button, IconButton } from "@/ui/button";
import { cn } from "@/ui/cn";
import { DataTable, KeyValue, Section, Signed } from "@/ui/data";
import { EmptyState, ErrorState, Kbd, SkeletonRows, Status } from "@/ui/feedback";
import { Input, Select } from "@/ui/form";
import { Sheet } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

import { Boveda } from "./Boveda";
import { ActionDialog, errorText, When } from "../shared";

/** Backend confirmation rules (`memory/operations.py`), mirrored to explain a disabled action. */
const CONFIRMABLE = new Set(["HYPOTHESIS", "PATTERN", "LESSON"]);
const MIN_USES_TO_CONFIRM = 5;
const MIN_RELIABILITY_TO_CONFIRM = 0.6;

type StatusFilter = "all" | "ACTIVE" | "NEEDS_REVALIDATION" | "RETIRED";
const STATUS_OPTIONS: Array<{ value: StatusFilter; label: string }> = [
  { value: "all", label: "Todos los estados" },
  { value: "ACTIVE", label: "Activo" },
  { value: "NEEDS_REVALIDATION", label: "Por revalidar" },
  { value: "RETIRED", label: "Retirado" },
];

/** Knowledge rows may carry extra meta-memory values (value_score, lift_usd…). */
type Knowledge = KnowledgeItem & Record<string, unknown>;

/** Display-only number: reliability arrives as a Decimal string or a number. */
function ratio(value: unknown): number | null {
  const n = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(n) ? n : null;
}

function percentOf(value: unknown): string {
  const n = ratio(value);
  return n === null ? "—" : pct((n * 100).toFixed(4), { dp: 0 });
}

function sumCounts(counts: Record<string, number> | undefined): number {
  return Object.values(counts ?? {}).reduce((sum, n) => sum + (Number.isFinite(n) ? n : 0), 0);
}

function countsText(counts: Record<string, number> | undefined, label: (code: string) => string): string {
  const entries = Object.entries(counts ?? {}).filter(([, n]) => n > 0);
  if (!entries.length) return "Ninguno";
  return entries.map(([code, n]) => `${n} ${label(code).toLowerCase()}`).join(" · ");
}

function useMemoryDecision() {
  const { client } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { id: string; action: "confirm" | "retire"; reason: string }) =>
      client.post<Record<string, unknown>>(`/api/v1/memory/knowledge/${encodeURIComponent(input.id)}/decision`, {
        action: input.action,
        reason: input.reason,
      }),
    onSuccess: (_result, input) =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
        queryClient.invalidateQueries({ queryKey: ["knowledge-detail", input.id] }),
        queryClient.invalidateQueries({ queryKey: ["memory"] }),
        queryClient.invalidateQueries({ queryKey: SNAPSHOT_KEY }),
      ]),
  });
}

interface KnowledgeDetail {
  memory?: Record<string, unknown>;
  content?: string;
  versions?: unknown[];
}

/** Full inspection record; optional (the list row is enough to render the sheet). */
function useKnowledgeDetail(id: string | null) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["knowledge-detail", id],
    queryFn: () => client.get<KnowledgeDetail>(`/api/v1/memory/knowledge/${encodeURIComponent(id ?? "")}`),
    enabled: Boolean(id),
    retry: false,
  });
}

function Overview({ status }: { status: MemoryStatus }) {
  const { overview } = status;
  return (
    <KeyValue
      items={[
        { label: "Conocimiento", value: `${sumCounts(overview.knowledge)} · ${countsText(overview.knowledge, knowledgeStatus)}` },
        { label: "Candidatos", value: countsText(overview.candidates, candidateStatus) },
        {
          label: "Conflictos",
          value: overview.conflicts ? <span className="text-warning">{overview.conflicts} sin resolver</span> : "Ninguno",
        },
        { label: "Creado (7 días)", value: <span className="num">{overview.created_last_7d}</span> },
        { label: "Creado (30 días)", value: <span className="num">{overview.created_last_30d}</span> },
        { label: "Latencia de recuperación", value: <span className="num">{decimal(overview.retrieval_latency_ms, { dp: 1 })} ms</span> },
        { label: "Último ciclo", value: overview.last_cycle_at ? <When iso={overview.last_cycle_at} className="text-body text-fg" /> : <span className="text-fg-muted">Nunca</span> },
      ]}
    />
  );
}

function HealthSection() {
  const query = useMemoryStatus();

  // The memory cycle runs by itself every day after the close (16:15 New York).
  const action = <span className="text-caption text-fg-muted">Se actualiza sola cada día al cierre</span>;

  if (query.isLoading) {
    return (
      <Section title="Estado de la memoria" actions={action}>
        <SkeletonRows rows={6} />
      </Section>
    );
  }
  if (query.error || !query.data) {
    return (
      <Section title="Estado de la memoria" actions={action}>
        <ErrorState
          title="No se pudo leer el estado de la memoria"
          impact="Los agentes siguen operando con su contexto actual; la memoria nunca modifica límites de riesgo."
          detail={query.error ? errorText(query.error) : undefined}
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
        />
      </Section>
    );
  }
  const status = query.data;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_340px] gap-10 max-xl:grid-cols-1">
      <Section
        title="Estado de la memoria"
        description={
          <span className="inline-flex items-center gap-2">
            Salud general <Status tone={healthTone(status.health.overall)}>{health(status.health.overall)}</Status>
          </span>
        }
        actions={action}
      >
        {status.health.components.length ? (
          <ul className="flex flex-col">
            {status.health.components.map((component) => (
              <li key={component.component} className="flex items-center gap-4 border-b border-line py-2 last:border-0">
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="text-body text-fg">{memoryComponent(component.component)}</span>
                  <span className="truncate text-caption text-fg-muted">{memoryDetail(component.detail)}</span>
                </div>
                <Status tone={healthTone(component.status)}>{health(component.status)}</Status>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState compact title="Sin componentes reportados" description="El núcleo no informó capas de memoria." />
        )}
      </Section>
      <Section title="Resumen">
        <Overview status={status} />
      </Section>
    </div>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return debounced;
}

function KnowledgeSheet({ item, onClose }: { item: Knowledge | null; onClose: () => void }) {
  const detail = useKnowledgeDetail(item?.id ?? null);
  const decision = useMemoryDecision();
  const toast = useToast();
  const [action, setAction] = useState<"confirm" | "retire" | null>(null);

  if (!item) return null;

  const memory = detail.data?.memory;
  const summary = (typeof memory?.summary === "string" && memory.summary) || item.summary || null;
  const content = typeof detail.data?.content === "string" && detail.data.content.trim() ? detail.data.content : null;

  const uses = (item.successful_uses ?? 0) + (item.failed_uses ?? 0);
  const reliability = ratio(item.reliability);
  const confirmBlock =
    item.status !== "ACTIVE"
      ? "Solo se puede confirmar conocimiento activo."
      : !CONFIRMABLE.has(item.category)
        ? "Solo hipótesis, patrones y lecciones se pueden confirmar."
        : uses < MIN_USES_TO_CONFIRM || reliability === null || reliability < MIN_RELIABILITY_TO_CONFIRM
          ? `Requiere al menos ${MIN_USES_TO_CONFIRM} usos evaluados y fiabilidad ≥ 60% (tiene ${uses} y ${percentOf(item.reliability)}).`
          : (item.failed_uses ?? 0) >= (item.successful_uses ?? 0)
            ? "Tiene tantos fallos como aciertos o más."
            : null;
  const retireBlock = item.status === "RETIRED" ? "Ya está retirado." : null;

  const optional = (label: string, value: unknown, render: (v: NonNullable<unknown>) => ReactNode = (v) => String(v)) =>
    value === null || value === undefined || value === "" ? [] : [{ label, value: render(value) }];

  const fields = [
    { label: "Identificador", value: <span className="num selectable">{item.knowledge_id ?? item.id}</span> },
    { label: "Categoría", value: memoryType(item.category) },
    { label: "Estado", value: <Status tone={knowledgeStatusTone(item.status)}>{knowledgeStatus(item.status)}</Status> },
    { label: "Símbolo", value: item.symbol ? <span className="num">{item.symbol}</span> : "Todos" },
    ...optional("Estrategia", item.strategy),
    ...optional("Régimen", item.market_regime),
    { label: "Fiabilidad", value: <span className="num">{percentOf(item.reliability)}</span> },
    ...optional("Confianza", item.confidence, (v) => <span className="num">{percentOf(v)}</span>),
    ...optional("Importancia", item.importance, (v) => <span className="num">{percentOf(v)}</span>),
    {
      label: "Usos",
      value: (
        <span className="num">
          <span className="text-positive">{item.successful_uses ?? 0} correctos</span> · <span className="text-negative">{item.failed_uses ?? 0} fallidos</span>
        </span>
      ),
    },
    ...optional("Validaciones", item.validation_count, (v) => <span className="num">{String(v)}</span>),
    ...optional("Tasa de acierto", item.hit_rate, (v) => <span className="num">{percentOf(v)}</span>),
    ...optional("Valor", item.value_score, (v) => <span className="num">{decimal(String(v), { dp: 2 })}</span>),
    ...optional("Mejora atribuida", item.lift_usd, (v) => <Signed value={String(v)} />),
    ...optional("Pérdida evitada", item.avoided_loss_usd, (v) => <span className="num">{money(String(v))}</span>),
    ...optional("Ganancia no capturada", item.forgone_profit_usd, (v) => <span className="num">{money(String(v))}</span>),
    ...optional("Válido desde", item.valid_from, (v) => dateUTC(String(v))),
    ...optional("Válido hasta", item.valid_until, (v) => dateUTC(String(v))),
    { label: "Última validación", value: item.last_validated_at ? dateUTC(item.last_validated_at) : "Nunca" },
    ...optional("Creado", item.created_at, (v) => dateUTC(String(v))),
    ...optional("Actualizado", item.updated_at, (v) => dateUTC(String(v))),
    ...optional("Versiones", detail.data?.versions?.length, (v) => <span className="num">{String(v)}</span>),
  ];

  const submit = (reason: string) => {
    if (!action) return;
    decision.mutate(
      { id: item.id, action, reason },
      {
        onSuccess: () => {
          toast({
            tone: "success",
            title: action === "confirm" ? "Conocimiento confirmado" : "Conocimiento retirado",
            description: "La decisión quedó versionada y auditada. Nada se borró.",
          });
          setAction(null);
        },
      },
    );
  };

  return (
    <Sheet
      open
      onOpenChange={(open) => !open && onClose()}
      title={item.title || "Conocimiento sin título"}
      subtitle={`${memoryType(item.category)} · ${knowledgeStatus(item.status)}`}
    >
      <div className="flex flex-col gap-6">
        {summary ? <p className="selectable text-body text-fg">{summary}</p> : null}
        {content && content !== summary ? (
          <pre className="selectable max-h-60 overflow-auto whitespace-pre-wrap rounded-sm bg-surface-2 p-3 font-sans text-body-2 text-fg-2">{content}</pre>
        ) : null}
        <KeyValue items={fields} />
        <Section title="Registrar decisión" description="Confirmar o retirar crea una versión nueva y queda auditado. Ninguna memoria se elimina.">
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary" size="sm" icon={BadgeCheck} disabled={confirmBlock !== null} onClick={() => setAction("confirm")}>
                Confirmar conocimiento
              </Button>
              <Button variant="destructive" size="sm" icon={Archive} disabled={retireBlock !== null} onClick={() => setAction("retire")}>
                Retirar
              </Button>
            </div>
            {confirmBlock ? <p className="text-caption text-fg-muted">No se puede confirmar: {confirmBlock}</p> : null}
            {retireBlock ? <p className="text-caption text-fg-muted">{retireBlock}</p> : null}
          </div>
        </Section>
      </div>
      <ActionDialog
        open={action !== null}
        onOpenChange={(open) => {
          if (!open) {
            setAction(null);
            decision.reset();
          }
        }}
        title={action === "confirm" ? "Confirmar conocimiento" : "Retirar conocimiento"}
        description={
          action === "confirm"
            ? "Pasa a conocimiento confirmado y los agentes lo recibirán con mayor peso. Explica qué evidencia lo respalda."
            : "Deja de entregarse a los agentes. Se conserva en el historial para auditoría."
        }
        confirmLabel="Registrar decisión"
        destructive={action === "retire"}
        requireReason
        loading={decision.isPending}
        error={decision.error ? errorText(decision.error) : null}
        onConfirm={submit}
      />
    </Sheet>
  );
}

function KnowledgeSection() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [selected, setSelected] = useState<Knowledge | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const debouncedText = useDebounced(text.trim(), 250);
  const query = useKnowledge({ text: debouncedText || undefined, status: status === "all" ? undefined : status });
  const rows = (Array.isArray(query.data) ? query.data : []) as Knowledge[];
  const filtered = Boolean(text.trim()) || status !== "all";

  // Keep the open sheet in sync with the refreshed row after a decision.
  const current = selected ? (rows.find((row) => row.id === selected.id) ?? selected) : null;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== "/" || target?.closest("input, textarea, [contenteditable='true']")) return;
      event.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const clear = () => {
    setText("");
    setStatus("all");
  };

  let body;
  if (query.isLoading) body = <SkeletonRows rows={6} />;
  else if (query.error)
    body = (
      <ErrorState
        title="No se pudo cargar el conocimiento"
        impact="La búsqueda no está disponible; los agentes conservan su memoria actual."
        detail={errorText(query.error)}
        onRetry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    );
  else
    body = (
      <DataTable<Knowledge>
        label="Conocimiento de la memoria"
        data={rows}
        getRowId={(row) => row.id}
        selectedId={current?.id ?? null}
        onRowClick={(row) => setSelected(row)}
        empty={
          filtered ? (
            <EmptyState
              compact
              title="Ningún conocimiento coincide con los filtros"
              description="Prueba con otro texto o estado."
              action={
                <Button size="sm" onClick={clear}>
                  Limpiar filtros
                </Button>
              }
            />
          ) : (
            <EmptyState
              compact
              title="La memoria aún no tiene conocimiento"
              description="El ciclo de memoria promueve candidatos con evidencia suficiente a conocimiento activo."
            />
          )
        }
        columns={[
          {
            header: "Título",
            id: "title",
            cell: ({ row }) => <span className="block max-w-[44ch] truncate text-fg">{row.original.title || "Sin título"}</span>,
          },
          { header: "Categoría", id: "category", cell: ({ row }) => <span className="text-fg-2">{memoryType(row.original.category)}</span> },
          {
            header: "Símbolo",
            id: "symbol",
            meta: { hideBelow: "lg" },
            cell: ({ row }) => (row.original.symbol ? <span className="num text-fg-2">{row.original.symbol}</span> : <span className="text-fg-muted">Todos</span>),
          },
          {
            header: "Estado",
            id: "status",
            cell: ({ row }) => <Status tone={knowledgeStatusTone(row.original.status)}>{knowledgeStatus(row.original.status)}</Status>,
          },
          {
            header: "Fiabilidad",
            id: "reliability",
            meta: { align: "right" },
            cell: ({ row }) => <span className="num">{percentOf(row.original.reliability)}</span>,
          },
          {
            header: "Usos (ok / fallo)",
            id: "uses",
            meta: { align: "right" },
            cell: ({ row }) => (
              <span className="num">
                <span className="text-positive">{row.original.successful_uses ?? 0}</span>
                <span className="text-fg-muted"> / </span>
                <span className={cn((row.original.failed_uses ?? 0) > 0 ? "text-negative" : "text-fg-2")}>{row.original.failed_uses ?? 0}</span>
              </span>
            ),
          },
        ]}
      />
    );

  return (
    <Section title="Qué ha aprendido" description="Lecciones sacadas de operaciones ya cerradas. Pulsa una para ver su evidencia y confirmarla o retirarla.">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-72">
          <Search size={14} strokeWidth={1.5} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-muted" aria-hidden />
          <Input
            ref={searchRef}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") setText("");
            }}
            placeholder="Buscar conocimiento"
            aria-label="Buscar conocimiento"
            className="h-7 pl-7 pr-12"
          />
          <span className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center">
            {text ? (
              <IconButton icon={X} label="Borrar búsqueda" size="sm" className="size-6" onClick={() => setText("")} />
            ) : (
              <span className="pr-1.5">
                <Kbd>/</Kbd>
              </span>
            )}
          </span>
        </div>
        <Select
          aria-label="Filtrar por estado"
          value={status}
          onValueChange={(value) => setStatus(value as StatusFilter)}
          options={STATUS_OPTIONS}
          className="h-7 w-48"
        />
        {filtered ? (
          <Button size="sm" variant="ghost" onClick={clear}>
            Limpiar filtros
          </Button>
        ) : null}
        {query.isFetching && !query.isLoading ? <span className="text-caption text-fg-muted">Actualizando…</span> : null}
      </div>
      {body}
      <KnowledgeSheet item={current} onClose={() => setSelected(null)} />
    </Section>
  );
}

function ReviewQueue() {
  const candidates = useMemoryCandidates();
  const conflicts = useMemoryConflicts();
  const open = (conflicts.data ?? []).filter((conflict) => !conflict.resolution);
  return (
    <Section
      title="Lecciones nuevas por confirmar"
      description="Lo que el ciclo de memoria aún no convierte en conocimiento. Los candidatos se validan con evidencia; los conflictos marcan memorias que se contradicen."
    >
      <div className="grid grid-cols-2 gap-6 max-xl:grid-cols-1">
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-label font-medium text-fg-2">Candidatos ({candidates.data?.length ?? 0})</h3>
          {candidates.error ? (
            <ErrorState title="No se pudieron cargar los candidatos" impact="El conocimiento activo no se ve afectado." detail={errorText(candidates.error)} onRetry={() => void candidates.refetch()} retrying={candidates.isFetching} />
          ) : (
            <DataTable
              dense
              label="Candidatos de memoria"
              loading={candidates.isLoading}
              data={candidates.data ?? []}
              getRowId={(row) => row.id}
              maxHeight={320}
              empty={<EmptyState compact title="Sin candidatos pendientes" description="El ciclo diario genera candidatos a partir de operaciones evaluadas." />}
              columns={[
                { header: "Título", id: "title", cell: ({ row }) => <span className="block max-w-[32ch] truncate text-fg">{row.original.title || "Sin título"}</span> },
                { header: "Tipo", id: "type", cell: ({ row }) => <span className="text-fg-2">{memoryType(row.original.memory_type)}</span> },
                {
                  header: "Símbolo",
                  id: "symbol",
                  meta: { hideBelow: "lg" },
                  cell: ({ row }) => (row.original.symbol ? <span className="num text-fg-2">{row.original.symbol}</span> : <span className="text-fg-muted">Todos</span>),
                },
                { header: "Confianza", id: "confidence", meta: { align: "right" }, cell: ({ row }) => <span className="num">{percentOf(row.original.confidence)}</span> },
                { header: "Estado", id: "status", cell: ({ row }) => <span className="text-fg-2">{candidateStatus(row.original.status)}</span> },
              ]}
            />
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-label font-medium text-fg-2">Conflictos sin resolver ({open.length})</h3>
          {conflicts.error ? (
            <ErrorState title="No se pudieron cargar los conflictos" impact="Las memorias en conflicto siguen fuera de la recuperación." detail={errorText(conflicts.error)} onRetry={() => void conflicts.refetch()} retrying={conflicts.isFetching} />
          ) : (
            <DataTable
              dense
              label="Conflictos de memoria"
              loading={conflicts.isLoading}
              data={open}
              getRowId={(row) => row.id}
              maxHeight={320}
              empty={<EmptyState compact title="Sin conflictos" description="Ninguna memoria activa contradice a otra." />}
              columns={[
                { header: "Tipo", id: "type", cell: ({ row }) => <span className="text-warning">{conflictType(row.original.conflict_type)}</span> },
                {
                  header: "Memorias",
                  id: "pair",
                  cell: ({ row }) => (
                    <span className="num text-fg-2">
                      {shortId(row.original.memory_a_id)} ↔ {shortId(row.original.memory_b_id)}
                    </span>
                  ),
                },
                { header: "Detectado", id: "created", cell: ({ row }) => <When iso={row.original.created_at} className="text-fg-2" /> },
              ]}
            />
          )}
        </div>
      </div>
    </Section>
  );
}


/**
 * Memory in one place: what the system learned (with the review queue) and
 * the same knowledge as linked notes (the former Bóveda), one toggle apart.
 */
export function Memoria() {
  return (
    <div className="stagger flex flex-col gap-8">
      <p className="max-w-[70ch] text-body-2 text-fg-2">
        Después de cada día el sistema repasa sus operaciones y guarda lo que aprendió. Los agentes lo consultan como pista al decidir, nunca como orden, y
        nunca cambia los límites de riesgo. Una lección pasa a «confirmada» cuando la evidencia se repite o cuando tú la apruebas.
      </p>
      <KnowledgeSection />
      <ReviewQueue />
      <details className="flex flex-col gap-6">
        <summary className="cursor-pointer text-body-2 text-fg-2 hover:text-fg">Detalles técnicos: estado de la memoria, notas y grafo</summary>
        <div className="mt-6 flex flex-col gap-8">
          <HealthSection />
          <Boveda />
        </div>
      </details>
    </div>
  );
}
