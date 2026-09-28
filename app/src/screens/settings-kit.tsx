import { useEffect, useMemo, useState, type ReactNode } from "react";

import { useApplyConfig } from "@/api/queries";
import type { ConfigPatch } from "@/api/types";
import { apiError, configError } from "@/i18n/labels";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { useToast } from "@/ui/toast";

/*
 * Shared pieces for editing configuration where it is shown (Inteligencia,
 * Riesgo, Ajustes): a row layout, a draft that tracks changes, and the sticky
 * bar that always validates before applying.
 */

/** Label + description on the left, control on the right. */
export function SettingRow({ label, description, children, align = "center" }: { label: string; description?: ReactNode; children: ReactNode; align?: "center" | "start" }) {
  return (
    <div className={cn("grid grid-cols-[minmax(0,1fr)_minmax(240px,320px)] gap-8 border-b border-line py-4 last:border-0", align === "center" ? "items-center" : "items-start")}>
      <div className="flex flex-col gap-0.5">
        <span className="text-body font-medium text-fg">{label}</span>
        {description ? <span className="text-body-2 text-fg-muted">{description}</span> : null}
      </div>
      <div className="flex justify-end">{children}</div>
    </div>
  );
}

/** Draft state for a config form: tracks changes vs. the current public config. */
export function useDraft(initial: ConfigPatch) {
  const [draft, setDraft] = useState<ConfigPatch>(initial);
  const key = JSON.stringify(initial);
  useEffect(() => setDraft(JSON.parse(key) as ConfigPatch), [key]);
  const changes = useMemo(() => {
    const out: ConfigPatch = {};
    for (const [field, value] of Object.entries(draft) as Array<[keyof ConfigPatch, unknown]>) {
      if (JSON.stringify(value) !== JSON.stringify(initial[field])) (out as Record<string, unknown>)[field] = value;
    }
    return out;
  }, [draft, initial]);
  return { draft, set: <K extends keyof ConfigPatch>(field: K, value: ConfigPatch[K]) => setDraft((d) => ({ ...d, [field]: value })), changes, reset: () => setDraft(initial) };
}

/** Sticky apply bar: always validate → apply; server errors shown inline. */
export function ApplyBar({ changes, onReset }: { changes: ConfigPatch; onReset: () => void }) {
  const apply = useApplyConfig();
  const toast = useToast();
  const count = Object.keys(changes).length;
  const errors = apply.data?.validation.valid === false ? apply.data.validation.errors : [];
  if (!count && !errors.length) return null;
  return (
    <div className="sticky bottom-0 -mx-6 mt-6 flex items-center gap-4 border-t border-line bg-surface-1 px-6 py-3">
      <div className="flex min-w-0 flex-1 flex-col">
        {errors.length ? (
          errors.map((error) => (
            <span key={error} role="alert" className="text-body-2 text-danger">
              {configError(error)}
            </span>
          ))
        ) : apply.error ? (
          <span role="alert" className="text-body-2 text-danger">
            {apiError(apply.error.message)}
          </span>
        ) : (
          <span className="text-body-2 text-fg-2">
            {count === 1 ? "1 cambio sin aplicar" : `${count} cambios sin aplicar`} · se valida antes de guardar
          </span>
        )}
      </div>
      <Button
        variant="ghost"
        onClick={() => {
          apply.reset();
          onReset();
        }}
      >
        Descartar
      </Button>
      <Button
        variant="primary"
        loading={apply.isPending}
        disabled={!count}
        onClick={() =>
          apply.mutate(changes, {
            onSuccess: ({ result }) => {
              if (result?.applied) {
                toast({
                  tone: "success",
                  title: "Configuración aplicada",
                  description: result.requires_restart ? "Reinicia el motor para que tome los cambios." : undefined,
                });
              }
            },
          })
        }
      >
        Validar y aplicar
      </Button>
    </div>
  );
}
