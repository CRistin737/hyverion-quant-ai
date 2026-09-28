import { AlertTriangle, ChevronDown, CircleHelp, RotateCw, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { GLOSSARY, type GlossaryTerm, type Tone } from "@/i18n/labels";

import { Button } from "./button";
import { cn } from "./cn";
import { Tooltip } from "./overlay";

const TONE_TEXT: Record<Tone, string> = {
  neutral: "text-fg-2",
  positive: "text-positive",
  negative: "text-negative",
  warning: "text-warning",
  info: "text-info",
  accent: "text-accent-text",
};

const TONE_DOT: Record<Tone, string> = {
  neutral: "bg-fg-muted",
  positive: "bg-positive",
  negative: "bg-negative",
  warning: "bg-warning",
  info: "bg-info",
  accent: "bg-accent",
};

const TONE_BADGE: Record<Tone, string> = {
  neutral: "text-fg-2 shadow-[inset_0_0_0_1px_var(--border-default)]",
  positive: "text-positive bg-positive-soft",
  negative: "text-negative bg-negative-soft",
  warning: "text-warning bg-warning-soft",
  info: "text-info bg-info-soft",
  accent: "text-accent-text bg-accent-soft",
};

export function toneText(tone: Tone): string {
  return TONE_TEXT[tone];
}

/** Status dot + label. Colour is never the only signal. */
export function Status({ tone, children, pulse = false, className }: { tone: Tone; children: ReactNode; pulse?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-body-2", className)}>
      <span className="relative inline-flex size-1.5">
        {pulse ? <span className={cn("absolute inset-0 animate-ping rounded-full opacity-60 motion-reduce:hidden", TONE_DOT[tone])} /> : null}
        <span className={cn("relative size-1.5 rounded-full", TONE_DOT[tone])} />
      </span>
      <span className="text-fg-2">{children}</span>
    </span>
  );
}

export function Badge({ tone = "neutral", children, className, mono = false }: { tone?: Tone; children: ReactNode; className?: string; mono?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center whitespace-nowrap rounded-xs px-1.5 text-caption font-medium",
        mono && "font-mono text-[11px]",
        TONE_BADGE[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <span aria-hidden className={cn("block animate-pulse rounded-xs bg-surface-2 motion-reduce:animate-none", className)} />;
}

export function SkeletonRows({ rows = 5, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col", className)} aria-busy="true" aria-label="Cargando">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="flex h-8 items-center gap-4 border-b border-line">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 flex-1" />
          <Skeleton className="h-3 w-16" />
        </div>
      ))}
    </div>
  );
}

/** What is empty · why it matters · what to do · primary action. */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  compact = false,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-start gap-3", compact ? "py-6" : "py-12", className)}>
      {Icon ? <Icon size={20} strokeWidth={1.5} className="text-fg-muted" aria-hidden /> : null}
      <div className="flex max-w-[52ch] flex-col gap-1">
        <p className="text-section font-semibold text-fg">{title}</p>
        {description ? <p className="text-body-2 text-fg-2">{description}</p> : null}
      </div>
      {action}
    </div>
  );
}

/** What failed · what is affected · safe next step · retry · optional detail. */
export function ErrorState({
  title,
  impact,
  detail,
  onRetry,
  retrying = false,
  className,
}: {
  title: string;
  impact?: ReactNode;
  detail?: string;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div role="alert" className={cn("flex flex-col items-start gap-3 py-8", className)}>
      <AlertTriangle size={20} strokeWidth={1.5} className="text-warning" aria-hidden />
      <div className="flex max-w-[60ch] flex-col gap-1">
        <p className="text-section font-semibold text-fg">{title}</p>
        {impact ? <p className="text-body-2 text-fg-2">{impact}</p> : null}
      </div>
      <div className="flex items-center gap-2">
        {onRetry ? (
          <Button size="sm" icon={RotateCw} onClick={onRetry} loading={retrying}>
            Reintentar
          </Button>
        ) : null}
        {detail ? (
          <Button size="sm" variant="ghost" iconRight={ChevronDown} onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            Detalle técnico
          </Button>
        ) : null}
      </div>
      {open && detail ? <pre className="selectable max-w-full overflow-auto rounded-sm bg-surface-2 p-3 font-mono text-caption text-fg-2">{detail}</pre> : null}
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-xs px-1 font-sans text-[11px] text-fg-muted shadow-[inset_0_0_0_1px_var(--border-default)]">
      {children}
    </kbd>
  );
}

/** Small "?" next to a technical word; hovering or focusing it explains the word. */
export function InfoHint({ term, label }: { term: GlossaryTerm; label?: string }) {
  return (
    <Tooltip content={<span className="block max-w-[260px]">{GLOSSARY[term]}</span>}>
      <button
        type="button"
        aria-label={`Qué significa ${label ?? term}`}
        className="inline-flex size-4 shrink-0 items-center justify-center rounded-full align-middle text-fg-muted transition-colors hover:text-fg focus-visible:outline-2"
      >
        <CircleHelp size={12} strokeWidth={1.75} aria-hidden />
      </button>
    </Tooltip>
  );
}
