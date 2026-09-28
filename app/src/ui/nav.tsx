import { cn } from "./cn";

export interface TabItem<T extends string> {
  value: T;
  label: string;
  count?: number;
}

/** Underline tabs for sub-navigation inside a domain (lives in the topbar). */
export function Tabs<T extends string>({ items, value, onChange, label }: { items: TabItem<T>[]; value: T; onChange: (value: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex h-full items-stretch gap-4">
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => onChange(item.value)}
            className={cn(
              "relative flex items-center gap-1.5 text-[13px] font-medium transition-colors duration-[var(--duration-fast)]",
              active ? "text-fg" : "text-fg-muted hover:text-fg-2",
            )}
          >
            {item.label}
            {item.count ? <span className="rounded-xs bg-surface-3 px-1 font-mono text-[11px] text-fg-2">{item.count}</span> : null}
            <span className={cn("absolute inset-x-0 -bottom-px h-0.5 rounded-full", active ? "bg-fg" : "bg-transparent")} />
          </button>
        );
      })}
    </div>
  );
}

/** Segmented control for switching views inside a panel. */
export function Segmented<T extends string>({ items, value, onChange, label, size = "sm" }: { items: TabItem<T>[]; value: T; onChange: (value: T) => void; label: string; size?: "sm" | "md" }) {
  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex items-center rounded-sm bg-surface-2 p-0.5", size === "sm" ? "h-7" : "h-8")}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(item.value)}
            className={cn(
              "h-full rounded-xs px-2.5 text-[12.5px] font-medium transition-colors duration-[var(--duration-fast)]",
              active ? "bg-surface-3 text-fg shadow-[inset_0_0_0_1px_var(--border-default)]" : "text-fg-muted hover:text-fg-2",
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
