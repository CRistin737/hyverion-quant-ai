import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "@/api/client";
import { apiError } from "@/i18n/labels";
import { dateUTC, relative } from "@/lib/format";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Field } from "@/ui/form";
import { Dialog, Tooltip } from "@/ui/overlay";

export const REASON_MIN = 3;
export const REASON_MAX = 200;

/** Human, Spanish message for a failed mutation or query. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.offline) return error.detail;
    return apiError(error.detail);
  }
  if (error instanceof Error) return apiError(error.message);
  return "Error desconocido.";
}

/** Relative time in the caption with the absolute UTC time in a tooltip. */
export function When({ iso, className }: { iso: string | null | undefined; className?: string }) {
  if (!iso) return <span className={cn("text-fg-muted", className)}>—</span>;
  return (
    <Tooltip content={dateUTC(iso)}>
      <span className={cn("whitespace-nowrap text-body-2 text-fg-2", className)}>{relative(iso)}</span>
    </Tooltip>
  );
}

/**
 * Confirmation dialog for an audited operator action. When `requireReason` is
 * set the reason is validated with the backend bounds (3–200 characters).
 */
export function ActionDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive = false,
  requireReason = false,
  reasonHint,
  defaultReason = "",
  loading,
  error,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  requireReason?: boolean;
  reasonHint?: string;
  /** Pre-filled, editable reason (e.g. "Aprobado tras revisar la prueba"). */
  defaultReason?: string;
  loading: boolean;
  error?: string | null;
  onConfirm: (reason: string) => void;
  children?: ReactNode;
}) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setReason(defaultReason);
      setTouched(false);
    }
  }, [open, defaultReason]);

  const trimmed = reason.trim();
  const invalid = requireReason && (trimmed.length < REASON_MIN || trimmed.length > REASON_MAX);
  const reasonError = requireReason && touched && invalid ? `Escribe un motivo de ${REASON_MIN} a ${REASON_MAX} caracteres.` : null;

  const submit = () => {
    setTouched(true);
    if (invalid || loading) return;
    onConfirm(trimmed);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !loading && onOpenChange(next)}
      title={title}
      description={description}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={loading}>
            Cancelar
          </Button>
          <Button variant={destructive ? "destructive" : "primary"} onClick={submit} loading={loading} disabled={requireReason && touched && invalid}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {children}
        {requireReason ? (
          <Field label="Motivo" hint={reasonHint ?? `Queda registrado en la auditoría. Entre ${REASON_MIN} y ${REASON_MAX} caracteres.`} error={reasonError}>
            {(id, describedBy) => (
              <textarea
                id={id}
                aria-describedby={describedBy}
                aria-invalid={Boolean(reasonError) || undefined}
                value={reason}
                maxLength={REASON_MAX}
                rows={3}
                autoFocus
                onChange={(event) => setReason(event.target.value)}
                onBlur={() => setTouched(true)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) submit();
                }}
                className={cn(
                  "w-full resize-none rounded-sm bg-surface-2 px-2.5 py-2 text-body text-fg placeholder:text-fg-muted",
                  "shadow-[inset_0_0_0_1px_var(--border-default)] hover:shadow-[inset_0_0_0_1px_var(--border-strong)]",
                  "focus-visible:outline-2 focus-visible:outline-offset-1",
                  reasonError && "shadow-[inset_0_0_0_1px_var(--danger)] hover:shadow-[inset_0_0_0_1px_var(--danger)]",
                )}
              />
            )}
          </Field>
        ) : null}
        {error ? (
          <p role="alert" className="text-body-2 text-negative">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

/** "¿Para qué sirve?" box: one plain-language paragraph at the top of each lab tab. */
export function Purpose({ children }: { children: ReactNode }) {
  return (
    <aside aria-label="Para qué sirve" className="rounded-md bg-surface-1 px-4 py-3 text-body-2 text-fg-2">
      <p className="mb-0.5 text-label font-medium text-fg">¿Para qué sirve?</p>
      {children}
    </aside>
  );
}
