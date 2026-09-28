import { LoaderCircle, type LucideIcon } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cn } from "./cn";

type Variant = "primary" | "secondary" | "ghost" | "destructive";
type Size = "sm" | "md" | "lg";

const VARIANTS: Record<Variant, string> = {
  primary:
    "bg-accent text-on-accent hover:bg-accent-hover active:bg-accent-pressed disabled:bg-surface-3 disabled:text-fg-disabled",
  secondary:
    "bg-surface-2 text-fg shadow-[inset_0_0_0_1px_var(--border-default)] hover:bg-surface-hover active:bg-surface-active disabled:text-fg-disabled",
  ghost: "text-fg-2 hover:bg-surface-hover hover:text-fg active:bg-surface-active disabled:text-fg-disabled",
  destructive:
    "text-danger shadow-[inset_0_0_0_1px_var(--border-default)] hover:bg-negative-soft active:bg-surface-active disabled:text-fg-disabled",
};

const SIZES: Record<Size, string> = {
  sm: "h-7 px-2.5 gap-1.5 text-[12.5px]",
  md: "h-8 px-3 gap-2 text-body",
  lg: "h-10 px-4 gap-2 text-[14px]",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  icon?: LucideIcon;
  iconRight?: LucideIcon;
  loading?: boolean;
  children?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon: Icon, iconRight: IconRight, loading = false, className, children, disabled, type = "button", ...rest },
  ref,
) {
  const iconSize = size === "lg" ? 16 : 14;
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap rounded-sm font-medium",
        "transition-colors duration-[var(--duration-fast)] disabled:pointer-events-none",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <LoaderCircle size={iconSize} className="animate-spin" aria-hidden />
      ) : Icon ? (
        <Icon size={iconSize} strokeWidth={1.75} aria-hidden />
      ) : null}
      {children}
      {IconRight && !loading ? <IconRight size={iconSize} strokeWidth={1.75} aria-hidden /> : null}
    </button>
  );
});

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: LucideIcon;
  label: string;
  size?: "sm" | "md";
  active?: boolean;
}

/** Icon-only button: always carries an accessible label (and pair it with a Tooltip). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon: Icon, label, size = "md", active = false, className, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-sm text-fg-muted",
        "transition-colors duration-[var(--duration-fast)] hover:bg-surface-hover hover:text-fg",
        "disabled:pointer-events-none disabled:text-fg-disabled",
        active && "bg-surface-3 text-fg",
        size === "sm" ? "size-7" : "size-8",
        className,
      )}
      {...rest}
    >
      <Icon size={16} strokeWidth={1.5} aria-hidden />
    </button>
  );
});
