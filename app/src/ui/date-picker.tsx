import { Popover as RPopover } from "radix-ui";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { cn } from "./cn";

/*
 * Date, month and year pickers drawn by the app.
 *
 * Native `<input type="date|month">` pickers misbehave inside the macOS
 * WKWebView (the window goes black and stops responding), so every date
 * choice goes through these popovers instead. Values are plain calendar
 * strings: "YYYY-MM-DD", "YYYY-MM" and "YYYY".
 */

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const MONTHS_LONG = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
const WEEKDAYS = ["L", "M", "X", "J", "V", "S", "D"];

const pad = (value: number) => String(value).padStart(2, "0");

function parts(value: string): { year: number; month: number; day: number } {
  const [year = 1970, month = 1, day = 1] = value.split("-").map(Number);
  return { year, month, day };
}

const TRIGGER =
  "inline-flex h-8 min-w-44 items-center justify-between gap-2 rounded-sm bg-surface-2 px-2.5 text-body text-fg shadow-[inset_0_0_0_1px_var(--border-default)] transition-colors hover:shadow-[inset_0_0_0_1px_var(--border-strong)] focus-visible:outline-2 focus-visible:outline-[var(--focus-ring)]";
const CELL = "flex h-8 items-center justify-center rounded-sm text-body-2 transition-colors";

function Shell({ label, text, open, onOpenChange, children }: { label: string; text: string; open: boolean; onOpenChange: (open: boolean) => void; children: ReactNode }) {
  return (
    <RPopover.Root open={open} onOpenChange={onOpenChange}>
      <RPopover.Trigger asChild>
        <button type="button" aria-label={`${label}: ${text}`} className={TRIGGER}>
          <span className="first-letter:uppercase">{text}</span>
          <CalendarDays size={14} className="text-fg-muted" aria-hidden />
        </button>
      </RPopover.Trigger>
      <RPopover.Portal>
        <RPopover.Content
          align="start"
          sideOffset={6}
          aria-label={label}
          className="z-[var(--z-popover)] w-64 rounded-md bg-surface-floating p-3 shadow-float data-[state=open]:animate-fade-in"
        >
          {children}
        </RPopover.Content>
      </RPopover.Portal>
    </RPopover.Root>
  );
}

function Header({ title, onPrev, onNext, nextDisabled }: { title: string; onPrev: () => void; onNext: () => void; nextDisabled: boolean }) {
  return (
    <div className="mb-2 flex items-center justify-between">
      <button type="button" aria-label="Anterior" onClick={onPrev} className="rounded-sm p-1 text-fg-2 hover:bg-surface-hover hover:text-fg">
        <ChevronLeft size={16} aria-hidden />
      </button>
      <span className="text-body font-medium text-fg first-letter:uppercase">{title}</span>
      <button type="button" aria-label="Siguiente" disabled={nextDisabled} onClick={onNext} className="rounded-sm p-1 text-fg-2 hover:bg-surface-hover hover:text-fg disabled:opacity-30">
        <ChevronRight size={16} aria-hidden />
      </button>
    </div>
  );
}

/** One day ("YYYY-MM-DD"). Days after ``max`` cannot be chosen. */
export function DatePicker({ value, onChange, max, label = "Día" }: { value: string; onChange: (value: string) => void; max?: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const selected = parts(value);
  const [view, setView] = useState({ year: selected.year, month: selected.month });
  const limit = max ?? "9999-12-31";
  const cells = useMemo(() => {
    const first = new Date(Date.UTC(view.year, view.month - 1, 1));
    const offset = (first.getUTCDay() + 6) % 7; // Monday first
    const days = new Date(Date.UTC(view.year, view.month, 0)).getUTCDate();
    return [...Array<null>(offset).fill(null), ...Array.from({ length: days }, (_, index) => index + 1)];
  }, [view]);
  const shift = (delta: number) =>
    setView(({ year, month }) => {
      const total = year * 12 + (month - 1) + delta;
      return { year: Math.floor(total / 12), month: (total % 12) + 1 };
    });
  const text = `${selected.day} de ${MONTHS_LONG[selected.month - 1]} de ${selected.year}`;
  return (
    <Shell
      label={label}
      text={text}
      open={open}
      onOpenChange={(next) => {
        if (next) setView({ year: selected.year, month: selected.month });
        setOpen(next);
      }}
    >
      <Header
        title={`${MONTHS_LONG[view.month - 1]} ${view.year}`}
        onPrev={() => shift(-1)}
        onNext={() => shift(1)}
        nextDisabled={`${view.year}-${pad(view.month)}` >= limit.slice(0, 7)}
      />
      <div className="grid grid-cols-7 gap-0.5 text-center">
        {WEEKDAYS.map((day) => (
          <span key={day} className="py-1 text-caption text-fg-muted">
            {day}
          </span>
        ))}
        {cells.map((day, index) => {
          if (day === null) return <span key={`blank-${index}`} />;
          const iso = `${view.year}-${pad(view.month)}-${pad(day)}`;
          const disabled = iso > limit;
          const active = iso === value;
          return (
            <button
              key={iso}
              type="button"
              disabled={disabled}
              aria-pressed={active}
              onClick={() => {
                onChange(iso);
                setOpen(false);
              }}
              className={cn(CELL, "num", active ? "bg-accent text-on-accent" : "text-fg hover:bg-surface-hover", disabled && "pointer-events-none opacity-30")}
            >
              {day}
            </button>
          );
        })}
      </div>
    </Shell>
  );
}

/** One month ("YYYY-MM"). Months after ``max`` ("YYYY-MM") cannot be chosen. */
export function MonthPicker({ value, onChange, max, label = "Mes" }: { value: string; onChange: (value: string) => void; max?: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const selected = parts(value);
  const [year, setYear] = useState(selected.year);
  const limit = max ?? "9999-12";
  return (
    <Shell
      label={label}
      text={`${MONTHS_LONG[selected.month - 1]} de ${selected.year}`}
      open={open}
      onOpenChange={(next) => {
        if (next) setYear(selected.year);
        setOpen(next);
      }}
    >
      <Header title={String(year)} onPrev={() => setYear((y) => y - 1)} onNext={() => setYear((y) => y + 1)} nextDisabled={String(year + 1) > limit.slice(0, 4)} />
      <div className="grid grid-cols-3 gap-1">
        {MONTHS.map((name, index) => {
          const iso = `${year}-${pad(index + 1)}`;
          const disabled = iso > limit;
          const active = iso === value;
          return (
            <button
              key={iso}
              type="button"
              disabled={disabled}
              aria-pressed={active}
              onClick={() => {
                onChange(iso);
                setOpen(false);
              }}
              className={cn(CELL, "capitalize", active ? "bg-accent text-on-accent" : "text-fg hover:bg-surface-hover", disabled && "pointer-events-none opacity-30")}
            >
              {name}
            </button>
          );
        })}
      </div>
    </Shell>
  );
}

/** One year ("YYYY"), from ``min`` to ``max``. */
export function YearPicker({ value, onChange, min, max, label = "Año" }: { value: string; onChange: (value: string) => void; min: number; max: number; label?: string }) {
  const [open, setOpen] = useState(false);
  const years = Array.from({ length: Math.max(1, max - min + 1) }, (_, index) => max - index);
  return (
    <Shell label={label} text={value} open={open} onOpenChange={setOpen}>
      <div className="grid max-h-56 grid-cols-3 gap-1 overflow-y-auto">
        {years.map((year) => (
          <button
            key={year}
            type="button"
            aria-pressed={String(year) === value}
            onClick={() => {
              onChange(String(year));
              setOpen(false);
            }}
            className={cn(CELL, "num", String(year) === value ? "bg-accent text-on-accent" : "text-fg hover:bg-surface-hover")}
          >
            {year}
          </button>
        ))}
      </div>
    </Shell>
  );
}
