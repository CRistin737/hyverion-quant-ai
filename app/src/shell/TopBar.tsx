import { Search, Settings2 } from "lucide-react";
import { Link, useLocation } from "wouter";

import { useSnapshot } from "@/api/provider";
import { cn } from "@/ui/cn";
import { Kbd } from "@/ui/feedback";
import { Tooltip } from "@/ui/overlay";

import { ModeBadge } from "./ModeBadge";
import { ModelSwitcher } from "./ModelSwitcher";
import { DOMAIN_LIST, domainFromPath } from "./routes";

export function Mark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className={className}>
      <g fill="currentColor">
        <rect x="10" y="10" width="10" height="44" rx="1" />
        <rect x="44" y="10" width="10" height="44" rx="1" />
        <rect x="30.75" y="17" width="2.5" height="30" />
        <rect x="26" y="25" width="12" height="14" rx="1" />
      </g>
    </svg>
  );
}

/**
 * Primary navigation as a top toolbar (trading-terminal style): brand, the five
 * work domains, search/actions, mode and settings. It is also the window drag
 * region under the macOS overlay title bar (traffic lights sit on the left).
 */
export function TopBar({ onOpenPalette }: { onOpenPalette: () => void }) {
  const [location] = useLocation();
  const active = domainFromPath(location);
  const { data } = useSnapshot();
  const attention: Partial<Record<string, number>> = { trading: data?.unresolved_operations ?? 0 };
  const domains = DOMAIN_LIST.filter((d) => d.id !== "ajustes");
  const settings = DOMAIN_LIST.find((d) => d.id === "ajustes")!;

  return (
    <header data-tauri-drag-region data-print-hide className="relative z-[var(--z-sidebar)] flex h-12 shrink-0 items-center gap-1 border-b border-line bg-surface-1 pl-3 pr-3 [html[data-shell=tauri]_&]:pl-[84px]">
      <Link href="/inicio" aria-label="Hyverion · Inicio" className="mr-3 flex items-center gap-2 rounded-sm px-1.5 py-1 text-fg">
        <Mark className="size-[18px]" />
        <span className="text-[13px] font-semibold tracking-[-0.015em] max-lg:hidden">Hyverion</span>
      </Link>

      <nav aria-label="Principal" className="flex items-center gap-0.5">
        {domains.map((domain) => {
          const isActive = domain.id === active;
          const count = attention[domain.id] ?? 0;
          const Icon = domain.icon;
          return (
            <Tooltip key={domain.id} content={`${domain.label}  ⌘${domain.shortcut}`} side="bottom">
              <Link
                href={domain.path}
                aria-current={isActive ? "page" : undefined}
                className={cn(
                  "relative flex h-8 items-center gap-2 rounded-sm px-3 text-[13px] font-medium transition-colors duration-[var(--duration-fast)]",
                  isActive ? "bg-surface-3 text-fg shadow-[inset_0_0_0_1px_var(--border-default)]" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
                )}
              >
                <Icon size={15} strokeWidth={1.6} aria-hidden />
                <span className="max-[1100px]:sr-only">{domain.label}</span>
                {count > 0 ? (
                  <span aria-label={`${count} requiere atención`} className="rounded-xs bg-negative-soft px-1 font-mono text-[10.5px] leading-4 text-negative">
                    {count}
                  </span>
                ) : null}
              </Link>
            </Tooltip>
          );
        })}
      </nav>

      <div data-tauri-drag-region className="flex-1" />

      <button
        type="button"
        onClick={onOpenPalette}
        className="flex h-8 w-60 items-center gap-2 rounded-sm bg-surface-2 px-2.5 text-body-2 text-fg-muted shadow-[inset_0_0_0_1px_var(--border-subtle)] transition-colors hover:text-fg-2 max-xl:w-auto"
      >
        <Search size={14} strokeWidth={1.5} aria-hidden />
        <span className="flex-1 text-left max-xl:sr-only">Buscar o ejecutar…</span>
        <Kbd>⌘K</Kbd>
      </button>
      <div className="mx-2 h-5 w-px bg-line" aria-hidden />
      <ModeBadge />
      <ModelSwitcher />
      <Tooltip content={`Ajustes  ⌘${settings.shortcut}`} side="bottom">
        <Link
          href={settings.path}
          aria-label="Ajustes"
          aria-current={active === "ajustes" ? "page" : undefined}
          className={cn(
            "ml-1 flex size-8 items-center justify-center rounded-sm transition-colors",
            active === "ajustes" ? "bg-surface-3 text-fg" : "text-fg-muted hover:bg-surface-hover hover:text-fg",
          )}
        >
          <Settings2 size={16} strokeWidth={1.5} aria-hidden />
        </Link>
      </Tooltip>
    </header>
  );
}
