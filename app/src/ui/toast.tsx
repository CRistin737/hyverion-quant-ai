import { CheckCircle2, Info, TriangleAlert, X } from "lucide-react";
import { Toast as RToast } from "radix-ui";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

import { cn } from "./cn";

type ToastTone = "success" | "info" | "error";

interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
}

const ToastContext = createContext<(toast: Omit<ToastItem, "id">) => void>(() => undefined);

const ICONS = { success: CheckCircle2, info: Info, error: TriangleAlert } as const;
const COLORS = { success: "text-positive", info: "text-info", error: "text-negative" } as const;

/** Toasts are for cross-context confirmations only (backup done, config applied). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const push = useCallback((toast: Omit<ToastItem, "id">) => {
    setItems((current) => [...current.slice(-2), { ...toast, id: Date.now() + Math.random() }]);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      <RToast.Provider duration={4000} swipeDirection="right">
        {children}
        {items.map((item) => {
          const Icon = ICONS[item.tone];
          return (
            <RToast.Root
              key={item.id}
              onOpenChange={(open) => !open && setItems((current) => current.filter((x) => x.id !== item.id))}
              className="flex w-[340px] items-start gap-3 rounded-md bg-surface-floating p-3 shadow-float data-[state=open]:animate-fade-in"
            >
              <Icon size={16} className={cn("mt-0.5 shrink-0", COLORS[item.tone])} aria-hidden />
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <RToast.Title className="text-body font-medium text-fg">{item.title}</RToast.Title>
                {item.description ? <RToast.Description className="text-body-2 text-fg-2">{item.description}</RToast.Description> : null}
              </div>
              <RToast.Close aria-label="Cerrar" className="rounded-xs p-0.5 text-fg-muted hover:text-fg">
                <X size={14} aria-hidden />
              </RToast.Close>
            </RToast.Root>
          );
        })}
        <RToast.Viewport className="fixed bottom-10 right-4 z-[var(--z-toast)] flex flex-col gap-2 outline-none" />
      </RToast.Provider>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
