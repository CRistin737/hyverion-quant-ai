import { Check, ChevronDown } from "lucide-react";
import { Checkbox as RCheckbox, Select as RSelect, Switch as RSwitch } from "radix-ui";
import { forwardRef, useId, type InputHTMLAttributes, type ReactNode } from "react";

import { cn } from "./cn";

const CONTROL =
  "h-8 rounded-sm bg-surface-2 px-2.5 text-body text-fg placeholder:text-fg-muted " +
  "shadow-[inset_0_0_0_1px_var(--border-default)] transition-shadow duration-[var(--duration-fast)] " +
  "hover:shadow-[inset_0_0_0_1px_var(--border-strong)] focus-visible:outline-2 focus-visible:outline-offset-1 " +
  "disabled:text-fg-disabled disabled:shadow-[inset_0_0_0_1px_var(--border-subtle)]";

const INVALID = "shadow-[inset_0_0_0_1px_var(--danger)] hover:shadow-[inset_0_0_0_1px_var(--danger)]";

interface FieldProps {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: (id: string, describedBy: string | undefined) => ReactNode;
  className?: string;
}

/** Label + control + hint/error, wired for accessibility. */
export function Field({ label, hint, error, children, className }: FieldProps) {
  const id = useId();
  const hintId = hint || error ? `${id}-desc` : undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-label font-medium text-fg-2">
        {label}
      </label>
      {children(id, hintId)}
      {error ? (
        <p id={hintId} className="text-caption text-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-caption text-fg-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean;
  suffix?: string;
  prefix?: string;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input({ invalid, suffix, prefix, className, ...rest }, ref) {
  if (!suffix && !prefix) {
    return <input ref={ref} aria-invalid={invalid || undefined} className={cn(CONTROL, "w-full", invalid && INVALID, className)} {...rest} />;
  }
  return (
    <div className="relative flex w-full items-center">
      {prefix ? <span className="pointer-events-none absolute left-2.5 text-body text-fg-muted">{prefix}</span> : null}
      <input
        ref={ref}
        aria-invalid={invalid || undefined}
        className={cn(CONTROL, "w-full", invalid && INVALID, prefix && "pl-6", suffix && "pr-12", className)}
        {...rest}
      />
      {suffix ? <span className="pointer-events-none absolute right-2.5 text-caption text-fg-muted">{suffix}</span> : null}
    </div>
  );
});

/** Decimal input: keeps the value as a string (Decimal on the backend), right-aligned mono. */
export const DecimalInput = forwardRef<HTMLInputElement, Omit<InputProps, "onChange" | "value"> & { value: string; onValueChange: (value: string) => void }>(
  function DecimalInput({ value, onValueChange, className, ...rest }, ref) {
    return (
      <Input
        ref={ref}
        inputMode="decimal"
        autoComplete="off"
        spellCheck={false}
        value={value}
        onChange={(event) => {
          const next = event.target.value.replace(",", ".");
          if (next === "" || /^\d*\.?\d*$/.test(next)) onValueChange(next);
        }}
        className={cn("num text-right", className)}
        {...rest}
      />
    );
  },
);

export interface SelectOption {
  value: string;
  label: string;
  description?: string;
  disabled?: boolean;
}

export function Select({
  id,
  value,
  onValueChange,
  options,
  placeholder = "Selecciona…",
  invalid,
  disabled,
  describedBy,
  className,
  "aria-label": ariaLabel,
}: {
  "aria-label"?: string;
  id?: string;
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  invalid?: boolean;
  disabled?: boolean;
  describedBy?: string;
  className?: string;
}) {
  return (
    <RSelect.Root value={value} onValueChange={onValueChange} disabled={disabled}>
      <RSelect.Trigger
        id={id}
        aria-label={ariaLabel}
        aria-describedby={describedBy}
        aria-invalid={invalid || undefined}
        className={cn(CONTROL, "flex items-center justify-between gap-2 text-left", invalid && INVALID, className ?? "w-full")}
      >
        <RSelect.Value placeholder={placeholder} />
        <RSelect.Icon>
          <ChevronDown size={14} className="text-fg-muted" aria-hidden />
        </RSelect.Icon>
      </RSelect.Trigger>
      <RSelect.Portal>
        <RSelect.Content position="popper" sideOffset={4} className="z-[var(--z-dropdown)] min-w-[var(--radix-select-trigger-width)] rounded-md bg-surface-floating p-1 shadow-float">
          <RSelect.Viewport>
            {options.map((option) => (
              <RSelect.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                className="relative flex cursor-default select-none flex-col rounded-sm py-1.5 pl-7 pr-2 text-body text-fg outline-none data-[disabled]:text-fg-disabled data-[highlighted]:bg-surface-hover"
              >
                <RSelect.ItemIndicator className="absolute left-2 top-2">
                  <Check size={14} className="text-accent-text" aria-hidden />
                </RSelect.ItemIndicator>
                <RSelect.ItemText>{option.label}</RSelect.ItemText>
                {option.description ? <span className="text-caption text-fg-muted">{option.description}</span> : null}
              </RSelect.Item>
            ))}
          </RSelect.Viewport>
        </RSelect.Content>
      </RSelect.Portal>
    </RSelect.Root>
  );
}

export function Checkbox({ checked, onCheckedChange, label, description, disabled }: { checked: boolean; onCheckedChange: (checked: boolean) => void; label: ReactNode; description?: ReactNode; disabled?: boolean }) {
  const id = useId();
  return (
    <div className="flex items-start gap-2.5">
      <RCheckbox.Root
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(value) => onCheckedChange(value === true)}
        className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-xs bg-surface-2 shadow-[inset_0_0_0_1px_var(--border-strong)] data-[state=checked]:bg-accent data-[state=checked]:shadow-none disabled:opacity-50"
      >
        <RCheckbox.Indicator>
          <Check size={12} strokeWidth={2.5} className="text-on-accent" aria-hidden />
        </RCheckbox.Indicator>
      </RCheckbox.Root>
      <label htmlFor={id} className="flex flex-col gap-0.5">
        <span className="text-body text-fg">{label}</span>
        {description ? <span className="text-caption text-fg-muted">{description}</span> : null}
      </label>
    </div>
  );
}

export function Switch({ checked, onCheckedChange, label, disabled }: { checked: boolean; onCheckedChange: (checked: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <RSwitch.Root
      checked={checked}
      onCheckedChange={onCheckedChange}
      disabled={disabled}
      aria-label={label}
      className="relative h-4 w-7 shrink-0 rounded-full bg-surface-3 shadow-[inset_0_0_0_1px_var(--border-strong)] transition-colors data-[state=checked]:bg-accent data-[state=checked]:shadow-none disabled:opacity-50"
    >
      <RSwitch.Thumb className="block size-3 translate-x-0.5 rounded-full bg-fg transition-transform duration-[var(--duration-fast)] data-[state=checked]:translate-x-[14px] data-[state=checked]:bg-on-accent" />
    </RSwitch.Root>
  );
}
