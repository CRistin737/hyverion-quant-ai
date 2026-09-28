import { useSnapshot } from "@/api/provider";
import { Tooltip } from "@/ui/overlay";

/**
 * The trading mode is always visible and never ambiguous. This release can
 * only run PAPER; a LIVE flag in config is still shown as blocked.
 */
export function ModeBadge() {
  const { data } = useSnapshot();
  const live = Boolean(data?.config.live_trading);
  if (live) {
    return (
      <Tooltip content="El modo real está configurado pero bloqueado: faltan las auditorías de preparación.">
        <span className="inline-flex h-6 items-center gap-1.5 rounded-xs px-2 font-mono text-[11px] font-medium tracking-[0.04em] text-danger shadow-[inset_0_0_0_1px_var(--danger)]">
          REAL · BLOQUEADO
        </span>
      </Tooltip>
    );
  }
  return (
    <Tooltip content="Simulación: el sistema opera con dinero ficticio. Ninguna orden llega a una cuenta real.">
      <span className="inline-flex h-6 items-center gap-1.5 rounded-xs px-2 font-mono text-[11px] font-medium tracking-[0.04em] text-accent-text shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--accent-text)_45%,transparent)]">
        <span className="size-1.5 rounded-full bg-accent" aria-hidden />
        SIMULACIÓN
      </span>
    </Tooltip>
  );
}
