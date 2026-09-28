import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { useAiModels, useAiUsage, useRefreshAiUsage, useSaveAiModels } from "@/api/queries";
import type { AiRole, SubscriptionUsage } from "@/api/types";
import { Button, IconButton } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Select } from "@/ui/form";
import { ErrorState } from "@/ui/feedback";
import { Tooltip } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

import { errorText } from "./shared";

/*
 * Subscription usage (5-hour window and weekly limit) and the model per role.
 * Shared by Inteligencia › Modelos, the header model switcher and Inicio.
 */

export const PROVIDER_NAMES: Record<string, string> = {
  anthropic: "Claude",
  openai: "Codex",
  xai: "Grok",
  gemini: "Gemini",
};

const ROLE_HELP: Record<AiRole, string> = {
  analysis: "Los agentes que leen y resumen: mercado, noticias, macro, amplitud… Es la mayoría de llamadas.",
  decision: "Estrategia y crítico: deciden si se opera. Conviene el modelo que mejor razona.",
  improvement: "La revisión nocturna que propone mejoras de la estrategia y de los agentes.",
};

function clamp(value: number): number {
  return Math.min(100, Math.max(0, value));
}

/** "se reinicia en 2 h 10 min" for ISO times; the provider's own label otherwise. */
export function resetText(value: string | null): string {
  if (!value) return "";
  const at = Date.parse(value);
  if (Number.isNaN(at) || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return `se reinicia ${value.replace(/\s*\([^)]*\)\s*$/, "")}`;
  const minutes = Math.max(0, Math.round((at - Date.now()) / 60_000));
  if (minutes < 60) return `se reinicia en ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `se reinicia en ${hours} h ${minutes % 60} min`;
  return `se reinicia en ${Math.round(hours / 24)} días`;
}

function Bar({ label, percent, caption, tone }: { label: string; percent: number; caption: string; tone: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-caption text-fg-muted">{label}</span>
        <span className="num text-body-2 font-medium text-fg">{Math.round(percent)}%</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-3" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)}>
        <div className={cn("h-full rounded-full transition-[width] duration-[var(--duration-slow)]", tone)} style={{ width: `${clamp(percent)}%` }} />
      </div>
      {caption ? <span className="truncate text-caption text-fg-muted">{caption}</span> : null}
    </div>
  );
}

/**
 * Two bars: what is left of the 5-hour window (100 % → 0 %) and how much of the
 * weekly limit is used, like Claude shows it.
 */
export function UsageBars({ usage, compact = false }: { usage: SubscriptionUsage | null | undefined; compact?: boolean }) {
  if (!usage) return <p className="text-caption text-fg-muted">Sin datos de uso todavía.</p>;
  if (!usage.available) {
    return <p className="text-caption text-fg-muted">{usage.detail === "provider_publishes_no_limits" ? "Este proveedor no publica sus límites; solo se muestra la conexión." : "No se pudieron leer los límites ahora mismo."}</p>;
  }
  const sessionLeft = usage.session_used_percent === null ? null : clamp(100 - Number(usage.session_used_percent));
  const weeklyUsed = usage.weekly_used_percent === null ? null : clamp(Number(usage.weekly_used_percent));
  return (
    <div className={cn("grid gap-4", compact ? "grid-cols-2" : "grid-cols-2 max-md:grid-cols-1")}>
      {sessionLeft !== null ? (
        <Bar
          label="Sesión de 5 h · disponible"
          percent={sessionLeft}
          caption={resetText(usage.session_resets_at)}
          tone={sessionLeft <= 10 ? "bg-negative" : sessionLeft <= 30 ? "bg-warning" : "bg-positive"}
        />
      ) : null}
      {weeklyUsed !== null ? (
        <Bar
          label="Semana · usado"
          percent={weeklyUsed}
          caption={resetText(usage.weekly_resets_at)}
          tone={weeklyUsed >= 90 ? "bg-negative" : weeklyUsed >= 70 ? "bg-warning" : "bg-fg-muted"}
        />
      ) : null}
    </div>
  );
}

/** Usage of the primary subscription, with a manual refresh. */
export function PrimaryUsage({ compact = false }: { compact?: boolean }) {
  const usage = useAiUsage();
  const refresh = useRefreshAiUsage();
  const primary = usage.data?.primary;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-label text-fg-muted">{primary ? `Uso de ${PROVIDER_NAMES[primary.provider_id] ?? primary.provider_id}` : "Uso de la suscripción"}</span>
        <Tooltip content="Actualizar ahora">
          <IconButton size="sm" icon={RefreshCw} label="Actualizar uso" disabled={refresh.isPending} className={refresh.isPending ? "animate-spin" : undefined} onClick={() => refresh.mutate()} />
        </Tooltip>
      </div>
      <UsageBars usage={primary} compact={compact} />
    </div>
  );
}

/** Model per role (analysis / decision / improvement) for the primary subscription. */
export function ModelRoles({ onSaved }: { onSaved?: () => void }) {
  const models = useAiModels();
  const save = useSaveAiModels();
  const toast = useToast();
  const [draft, setDraft] = useState<Record<AiRole, string> | null>(null);
  useEffect(() => {
    if (models.data) setDraft(models.data.models);
  }, [models.data]);
  if (models.error) return <ErrorState title="No se pudieron leer los modelos" detail={errorText(models.error)} onRetry={() => void models.refetch()} retrying={models.isFetching} />;
  if (!models.data || !draft) return <p className="text-body-2 text-fg-muted">Cargando modelos…</p>;
  const catalog = models.data.catalog[models.data.primary_provider] ?? [{ id: "default", label: "Predeterminado" }];
  const options = (current: string) => {
    const base = catalog.map((item) => ({ value: item.id, label: item.label }));
    return base.some((item) => item.value === current) ? base : [...base, { value: current, label: current }];
  };
  const changed = (Object.keys(draft) as AiRole[]).filter((role) => draft[role] !== models.data?.models[role]);
  return (
    <div className="flex flex-col gap-4">
      {models.data.primary_provider === "disabled" ? <p className="text-body-2 text-warning">Elige primero una suscripción principal.</p> : null}
      {models.data.roles.map(({ role, label }) => (
        <div key={role} className="grid grid-cols-[minmax(0,1fr)_220px] items-center gap-4 max-md:grid-cols-1">
          <div className="flex flex-col gap-0.5">
            <span className="text-body font-medium text-fg">{label}</span>
            <span className="text-caption text-fg-muted">{ROLE_HELP[role]}</span>
          </div>
          <Select aria-label={`Modelo de ${label}`} value={draft[role]} onValueChange={(value) => setDraft({ ...draft, [role]: value })} options={options(draft[role])} />
        </div>
      ))}
      <div className="flex items-center justify-end gap-2">
        <span className="mr-auto text-caption text-fg-muted">Los respaldos usan el modelo por defecto de su propia app.</span>
        <Button variant="ghost" disabled={!changed.length} onClick={() => setDraft(models.data!.models)}>
          Descartar
        </Button>
        <Button
          variant="primary"
          disabled={!changed.length}
          loading={save.isPending}
          onClick={() =>
            save.mutate(Object.fromEntries(changed.map((role) => [role, draft[role]])), {
              onSuccess: () => {
                toast({ tone: "success", title: "Modelo guardado", description: "El motor lo usa desde su siguiente ciclo, sin reiniciar." });
                onSaved?.();
              },
              onError: (error) => toast({ tone: "error", title: "No se pudo guardar el modelo", description: errorText(error) }),
            })
          }
        >
          Guardar
        </Button>
      </div>
    </div>
  );
}
