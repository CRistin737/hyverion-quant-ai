import { Command } from "cmdk";
import { Archive, CandlestickChart, ListChecks, Play, Sparkles, Square, type LucideIcon } from "lucide-react";
import { Dialog as RDialog } from "radix-ui";
import type { ReactNode } from "react";
import { useLocation } from "wouter";

import { useSnapshot } from "@/api/provider";
import { useBackup, useOptimizer, useStartEngine, useStopEngine } from "@/api/queries";
import { apiError, engineErrorMessage } from "@/i18n/labels";
import { Kbd } from "@/ui/feedback";
import { useToast } from "@/ui/toast";

import { DOMAIN_LIST } from "./routes";

function Item({ icon: Icon, children, hint, onSelect, value }: { icon: LucideIcon; children: ReactNode; hint?: ReactNode; onSelect: () => void; value: string }) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="flex h-8 cursor-default items-center gap-2.5 rounded-sm px-2.5 text-body text-fg-2 data-[selected=true]:bg-surface-hover data-[selected=true]:text-fg"
    >
      <Icon size={15} strokeWidth={1.5} aria-hidden className="text-fg-muted" />
      <span className="flex-1 truncate">{children}</span>
      {hint}
    </Command.Item>
  );
}

const GROUP =
  "[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:text-caption [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-fg-muted";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [, navigate] = useLocation();
  const { data } = useSnapshot();
  const toast = useToast();
  const optimizer = useOptimizer();
  const backup = useBackup();
  const startEngine = useStartEngine();
  const stopEngine = useStopEngine();

  const run = (action: () => void) => {
    onOpenChange(false);
    action();
  };

  return (
    <RDialog.Root open={open} onOpenChange={onOpenChange}>
      <RDialog.Portal>
        <RDialog.Overlay className="fixed inset-0 z-[var(--z-palette)] bg-[rgb(0_0_0/0.4)]" />
        <RDialog.Content className="fixed left-1/2 top-[14%] z-[var(--z-palette)] w-[560px] max-w-[calc(100vw-32px)] -translate-x-1/2 overflow-hidden rounded-lg bg-surface-floating shadow-float data-[state=open]:animate-fade-in">
          <RDialog.Title className="sr-only">Buscar o ejecutar</RDialog.Title>
          <RDialog.Description className="sr-only">Ir a una sección, ejecutar una acción o abrir un símbolo.</RDialog.Description>
          <Command label="Paleta de comandos" loop>
            <Command.Input
              autoFocus
              placeholder="Ir a, ejecutar o buscar un símbolo…"
              className="h-12 w-full border-b border-line bg-transparent px-4 text-[14px] text-fg outline-none placeholder:text-fg-muted"
            />
            <Command.List className="max-h-[360px] overflow-y-auto p-1.5">
              <Command.Empty className="px-3 py-6 text-body-2 text-fg-muted">Sin resultados.</Command.Empty>
              <Command.Group heading="Ir a" className={GROUP}>
                {DOMAIN_LIST.flatMap((domain) => [
                  <Item key={domain.id} value={`ir ${domain.label}`} icon={domain.icon} hint={<Kbd>⌘{domain.shortcut}</Kbd>} onSelect={() => run(() => navigate(domain.path))}>
                    {domain.label}
                  </Item>,
                  ...(domain.tabs ?? []).map((tab) => (
                    <Item key={`${domain.id}-${tab.value}`} value={`ir ${domain.label} ${tab.label}`} icon={domain.icon} onSelect={() => run(() => navigate(`${domain.path}/${tab.value}`))}>
                      <span className="text-fg-muted">{domain.label} ›</span> {tab.label}
                    </Item>
                  )),
                ])}
              </Command.Group>
              <Command.Group heading="Acciones" className={GROUP}>
                <Item
                  value="buscar mejoras optimizador"
                  icon={Sparkles}
                  onSelect={() =>
                    run(() =>
                      optimizer.run.mutate(undefined, {
                        onSuccess: () => {
                          toast({ tone: "info", title: "Buscando mejoras", description: "Tarda un minuto por estrategia. El resultado aparece en Inteligencia › Aprendizaje." });
                          navigate("/inteligencia/aprendizaje");
                        },
                        onError: (error) => toast({ tone: "error", title: "No se pudo buscar mejoras", description: apiError(error.message) }),
                      }),
                    )
                  }
                >
                  Buscar mejoras
                </Item>
                {data?.engine.state === "running" ? (
                  <Item
                    value="detener motor de simulacion"
                    icon={Square}
                    onSelect={() =>
                      run(() =>
                        stopEngine.mutate(undefined, {
                          onSuccess: () => toast({ tone: "info", title: "Motor detenido" }),
                          onError: (error) => toast({ tone: "error", title: "No se pudo detener el motor", description: error.message }),
                        }),
                      )
                    }
                  >
                    Detener motor de simulación
                  </Item>
                ) : data?.engine.state !== "external" ? (
                  <Item
                    value="iniciar motor de simulacion"
                    icon={Play}
                    onSelect={() =>
                      run(() =>
                        startEngine.mutate(60, {
                          onSuccess: () => toast({ tone: "success", title: "Motor iniciado", description: "Solo en simulación." }),
                          onError: (error) => toast({ tone: "error", title: "No se pudo iniciar el motor", description: engineErrorMessage(error.message) }),
                        }),
                      )
                    }
                  >
                    Iniciar motor de simulación
                  </Item>
                ) : null}
                <Item
                  value="crear copia de seguridad verificada"
                  icon={Archive}
                  onSelect={() =>
                    run(() =>
                      backup.mutate(undefined, {
                        onSuccess: (result) => toast({ tone: "success", title: "Copia de seguridad verificada", description: result.filename }),
                        onError: (error) => toast({ tone: "error", title: "La copia de seguridad falló", description: error.message }),
                      }),
                    )
                  }
                >
                  Crear copia de seguridad verificada
                </Item>
                <Item value="revisar salud del sistema preparacion auditoria" icon={ListChecks} onSelect={() => run(() => navigate("/ajustes/salud"))}>
                  Revisar salud del sistema
                </Item>
              </Command.Group>
              {data?.markets.length ? (
                <Command.Group heading="Símbolos" className={GROUP}>
                  {data.markets.map((market) => (
                    <Item
                      key={market.symbol}
                      value={`simbolo ${market.symbol}`}
                      icon={CandlestickChart}
                      onSelect={() => run(() => navigate(`/trading/mercado/${market.symbol.replace("/", "-")}`))}
                    >
                      <span className="num">{market.symbol}</span>
                    </Item>
                  ))}
                </Command.Group>
              ) : null}
            </Command.List>
          </Command>
        </RDialog.Content>
      </RDialog.Portal>
    </RDialog.Root>
  );
}
