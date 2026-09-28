import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp } from "lucide-react";
import { useState, type ReactNode } from "react";

import type { Dec } from "@/api/types";
import { money, pct, sign } from "@/lib/format";

import { cn } from "./cn";
import { SkeletonRows } from "./feedback";

/** Page section: title + optional description + actions, content on the page surface (no box). */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section aria-labelledby={title && id ? id : undefined} className={cn("flex min-w-0 flex-col gap-3", className)}>
      {title || actions || description ? (
        <header className="flex min-h-7 items-end justify-between gap-4">
          <div className="flex min-w-0 flex-col gap-0.5">
            {title ? (
              <h2 id={id} className="text-section font-semibold text-fg">
                {title}
              </h2>
            ) : null}
            {description ? <p className="text-body-2 text-fg-muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      {children}
    </section>
  );
}

/** Signed money with PnL colour and explicit sign. */
export function Signed({ value, kind = "money", dp, className }: { value: Dec | null | undefined; kind?: "money" | "pct"; dp?: number; className?: string }) {
  const s = sign(value);
  const text = kind === "money" ? money(value, { signed: true, dp }) : pct(value, { signed: true, dp });
  return <span className={cn("num", s > 0 ? "text-positive" : s < 0 ? "text-negative" : "text-fg-2", className)}>{text}</span>;
}

/** A metric only earns its place when it answers a question: value · label · context. */
export function Metric({ label, value, context, className }: { label: string; value: ReactNode; context?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-1", className)}>
      <span className="text-label text-fg-muted">{label}</span>
      <span className="num truncate text-kpi font-medium text-fg">{value}</span>
      {context ? <span className="truncate text-caption text-fg-muted">{context}</span> : null}
    </div>
  );
}

/** Key/value rows for detail views. */
export function KeyValue({ items, className }: { items: Array<{ label: string; value: ReactNode; mono?: boolean }>; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[minmax(120px,max-content)_1fr] gap-x-6 gap-y-2", className)}>
      {items.map((item) => (
        <div key={item.label} className="contents">
          <dt className="text-body-2 text-fg-muted">{item.label}</dt>
          <dd className={cn("min-w-0 text-body text-fg", item.mono && "num")}>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface DataTableProps<T> {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  loading?: boolean;
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
  selectedId?: string | null;
  getRowId?: (row: T) => string;
  maxHeight?: number;
  dense?: boolean;
  label: string;
}

declare module "@tanstack/react-table" {
  interface ColumnMeta<TData, TValue> {
    align?: "left" | "right";
    className?: string;
    hideBelow?: "lg" | "xl";
    _?: [TData, TValue];
  }
}

/**
 * Data table: sticky header, sortable columns, right-aligned numbers,
 * keyboard-activatable rows, skeleton and empty states.
 */
export function DataTable<T>({ columns, data, loading, empty, onRowClick, selectedId, getRowId, maxHeight, dense = false, label }: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
  });

  if (loading) return <SkeletonRows rows={5} />;
  if (!data.length && empty) return <>{empty}</>;

  const hide = (meta?: { hideBelow?: "lg" | "xl" }) =>
    meta?.hideBelow === "lg" ? "max-lg:hidden" : meta?.hideBelow === "xl" ? "max-xl:hidden" : "";

  return (
    <div className="min-w-0 overflow-auto" style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full border-collapse text-body" aria-label={label}>
        <thead className="sticky top-0 z-[var(--z-sticky)] bg-bg">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id}>
              {group.headers.map((header) => {
                const meta = header.column.columnDef.meta;
                const sorted = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                    className={cn(
                      "h-8 whitespace-nowrap border-b border-line px-3 text-caption font-medium tracking-[0.02em] text-fg-muted first:pl-0 last:pr-0",
                      meta?.align === "right" ? "text-right" : "text-left",
                      hide(meta),
                      meta?.className,
                    )}
                  >
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cn("inline-flex items-center gap-1 hover:text-fg-2", meta?.align === "right" && "flex-row-reverse")}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {sorted === "asc" ? <ArrowUp size={12} aria-hidden /> : sorted === "desc" ? <ArrowDown size={12} aria-hidden /> : null}
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => {
            const selected = selectedId !== undefined && selectedId !== null && row.id === selectedId;
            return (
              <tr
                key={row.id}
                tabIndex={onRowClick ? 0 : undefined}
                aria-selected={onRowClick ? selected : undefined}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          onRowClick(row.original);
                        }
                      }
                    : undefined
                }
                className={cn(
                  "border-b border-line transition-colors duration-[var(--duration-fast)]",
                  onRowClick && "hover:bg-surface-hover focus-visible:bg-surface-hover focus-visible:outline-none",
                  selected && "bg-surface-3 hover:bg-surface-3",
                )}
              >
                {row.getVisibleCells().map((cell) => {
                  const meta = cell.column.columnDef.meta;
                  return (
                    <td
                      key={cell.id}
                      className={cn(
                        "px-3 first:pl-0 last:pr-0",
                        dense ? "h-7" : "h-8",
                        meta?.align === "right" ? "text-right" : "text-left",
                        hide(meta),
                        meta?.className,
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
