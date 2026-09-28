import type { ReactNode } from "react";

import { Tabs, type TabItem } from "@/ui/nav";

/**
 * Page header: sub-navigation tabs and page actions under the top toolbar.
 * The page title stays available to assistive tech; the active domain is
 * already shown in the toolbar, so it is not repeated visually.
 */
export function PageHeader<T extends string>({
  title,
  tabs,
  tab,
  onTab,
  actions,
}: {
  title: string;
  tabs?: TabItem<T>[];
  tab?: T;
  onTab?: (value: T) => void;
  actions?: ReactNode;
}) {
  const heading = <h1 className="sr-only">{title}</h1>;
  if (!(tabs && tab && onTab) && !actions) return heading;
  return (
    <header data-print-hide className="flex h-11 shrink-0 items-stretch gap-6 border-b border-line px-6">
      {heading}
      {tabs && tab && onTab ? <Tabs items={tabs} value={tab} onChange={onTab} label={`Secciones de ${title}`} /> : null}
      <div className="flex flex-1 items-center justify-end gap-2">{actions}</div>
    </header>
  );
}

/**
 * Scrollable content region; the only element that owns scroll in a page.
 * `intro` is the one-sentence purpose of the current tab, shown first.
 */
export function PageBody({ children, wide = false, intro }: { children: ReactNode; wide?: boolean; intro?: string }) {
  return (
    <main className="min-h-0 flex-1 overflow-y-auto">
      <div className={wide ? "px-6 py-5" : "mx-auto w-full max-w-[var(--content-max)] px-6 py-5 max-[1279px]:px-4"}>
        {intro ? (
          <p key={intro} className="rise mb-4 max-w-[72ch] text-body-2 text-fg-muted">
            {intro}
          </p>
        ) : null}
        {children}
      </div>
    </main>
  );
}
