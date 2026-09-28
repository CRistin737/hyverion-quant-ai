import { X } from "lucide-react";
import { Dialog as RDialog, Popover as RPopover, Tooltip as RTooltip } from "radix-ui";
import type { ReactNode } from "react";

import { IconButton } from "./button";
import { cn } from "./cn";

export function TooltipProvider({ children }: { children: ReactNode }) {
  return (
    <RTooltip.Provider delayDuration={400} skipDelayDuration={150}>
      {children}
    </RTooltip.Provider>
  );
}

export function Tooltip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "right" | "bottom" | "left" }) {
  if (!content) return <>{children}</>;
  return (
    <RTooltip.Root>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content
          side={side}
          sideOffset={6}
          className="z-[var(--z-popover)] max-w-72 rounded-sm bg-surface-floating px-2 py-1 text-[12px] leading-4 text-fg shadow-float data-[state=delayed-open]:animate-fade-in"
        >
          {content}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

export function Popover({ trigger, children, align = "start", className }: { trigger: ReactNode; children: ReactNode; align?: "start" | "center" | "end"; className?: string }) {
  return (
    <RPopover.Root>
      <RPopover.Trigger asChild>{trigger}</RPopover.Trigger>
      <RPopover.Portal>
        <RPopover.Content
          align={align}
          sideOffset={6}
          className={cn("z-[var(--z-popover)] rounded-md bg-surface-floating p-3 shadow-float data-[state=open]:animate-fade-in", className)}
        >
          {children}
        </RPopover.Content>
      </RPopover.Portal>
    </RPopover.Root>
  );
}

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: 440 | 560 | 640;
}

/** Short, focused tasks only (confirmations, a reason for an audited action). */
export function Dialog({ open, onOpenChange, title, description, children, footer, width = 440 }: DialogProps) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-[var(--z-modal)] bg-[rgb(0_0_0/0.5)] data-[state=open]:animate-fade-in" />
        <RDialog.Content
          style={{ width }}
          className="fixed left-1/2 top-[12%] z-[var(--z-modal)] flex max-h-[80vh] max-w-[calc(100vw-32px)] -translate-x-1/2 flex-col rounded-lg bg-surface-floating shadow-float data-[state=open]:animate-fade-in"
        >
          <RDialog.Close asChild>
            <IconButton icon={X} label="Cerrar" size="sm" className="absolute right-3 top-3" />
          </RDialog.Close>
          <div className="flex flex-col gap-1 px-5 pr-12 pt-5">
            <RDialog.Title className="text-section font-semibold text-fg">{title}</RDialog.Title>
            {description ? <RDialog.Description className="text-body-2 text-fg-2">{description}</RDialog.Description> : <RDialog.Description className="sr-only">{title}</RDialog.Description>}
          </div>
          {children ? <div className="min-h-0 overflow-y-auto px-5 pt-4">{children}</div> : null}
          {footer ? <div className="flex justify-end gap-2 px-5 pb-5 pt-5">{footer}</div> : <div className="pb-5" />}
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}

interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

/** Right-side inspector for contextual detail (lifecycle, trace, knowledge item). */
export function Sheet({ open, onOpenChange, title, subtitle, actions, children }: SheetProps) {
  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-[var(--z-sheet)] bg-[rgb(0_0_0/0.28)] data-[state=open]:animate-fade-in" />
        <RDialog.Content className="fixed bottom-0 right-0 top-0 z-[var(--z-sheet)] flex w-[480px] max-w-[92vw] flex-col bg-surface-1 shadow-float outline-none data-[state=open]:animate-[sheet-in_var(--duration-slow)_var(--motion-ease)]">
          <div className="flex items-start gap-3 border-b border-line px-5 py-4">
            <div className="flex min-w-0 flex-1 flex-col gap-0.5">
              <RDialog.Title className="truncate text-section font-semibold text-fg">{title}</RDialog.Title>
              <RDialog.Description className={subtitle ? "text-body-2 text-fg-2" : "sr-only"}>{subtitle ?? "Detalle"}</RDialog.Description>
            </div>
            {actions}
            <RDialog.Close asChild>
              <IconButton icon={X} label="Cerrar" size="sm" />
            </RDialog.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
