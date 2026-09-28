import { Cpu } from "lucide-react";
import { useState } from "react";

import { useAiModels } from "@/api/queries";
import { Dialog, Tooltip } from "@/ui/overlay";

import { ModelRoles, PrimaryUsage } from "../screens/ai-usage";

/** Short model name for the header ("Opus 5.5"), from the catalog. */
function useDecisionLabel(): string {
  const models = useAiModels().data;
  if (!models) return "Modelo";
  const catalog = models.catalog[models.primary_provider] ?? [];
  const id = models.models.decision;
  return catalog.find((item) => item.id === id)?.label.replace("Predeterminado de ", "") ?? id;
}

/** Header button: pick the model per role in two clicks, and see the plan usage. */
export function ModelSwitcher() {
  const [open, setOpen] = useState(false);
  const label = useDecisionLabel();
  return (
    <>
      <Tooltip content="Modelo de IA por tarea" side="bottom">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label={`Modelo de IA: ${label}`}
          className="ml-1 inline-flex h-7 items-center gap-1.5 rounded-sm px-2 text-caption text-fg-2 transition-colors hover:bg-surface-hover hover:text-fg"
        >
          <Cpu size={14} strokeWidth={1.5} aria-hidden />
          <span className="max-lg:sr-only">{label}</span>
        </button>
      </Tooltip>
      <Dialog open={open} onOpenChange={setOpen} width={640} title="Modelo de IA" description="Qué modelo usa cada tarea de tu suscripción principal. El cambio se aplica en el siguiente ciclo, sin reiniciar.">
        <div className="flex flex-col gap-6">
          <ModelRoles onSaved={() => setOpen(false)} />
          <div className="border-t border-line pt-4">
            <PrimaryUsage compact />
          </div>
        </div>
      </Dialog>
    </>
  );
}
