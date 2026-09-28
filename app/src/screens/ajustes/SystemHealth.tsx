import { CircleAlert, CircleCheck, CircleDashed, HeartPulse, TriangleAlert } from "lucide-react";
import { useLocation } from "wouter";

import { useSnapshot } from "@/api/provider";
import { useBackup, useBrokerStatus, useReadiness, useServiceStatus, useSetService, useSourcesStatus, useStartEngine } from "@/api/queries";
import type { Snapshot } from "@/api/types";
import { apiError } from "@/i18n/labels";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Dialog } from "@/ui/overlay";
import { useToast } from "@/ui/toast";

type Tone = "ok" | "warning" | "error" | "neutral";

interface HealthItem {
  id: string;
  label: string;
  tone: Tone;
  detail: string;
  fix?: { label: string; to?: string; run?: () => void; busy?: boolean };
}

const ICON = { ok: CircleCheck, warning: TriangleAlert, error: CircleAlert, neutral: CircleDashed } as const;
const TONE = { ok: "text-positive", warning: "text-warning", error: "text-negative", neutral: "text-fg-muted" } as const;

const PROVIDER_NAME: Record<string, string> = { anthropic: "Claude", openai: "Codex", xai: "Grok", gemini: "Gemini" };

function useHealthItems(snapshot: Snapshot | undefined): HealthItem[] {
  const broker = useBrokerStatus();
  const readiness = useReadiness();
  const sources = useSourcesStatus();
  const service = useServiceStatus();
  const setService = useSetService();
  const startEngine = useStartEngine();
  const backup = useBackup();
  const toast = useToast();
  if (!snapshot) return [];
  const { config, providers, engine } = snapshot;
  const check = (id: string) => readiness.data?.checks.find((item) => item.check_id === id);

  const primary = providers.find((item) => item.provider_id === config.ai_provider);
  const aiOk = primary?.auth_state === "CONNECTED";
  const brokerOk = Boolean(broker.data?.connected && broker.data.account);
  const database = check("database");
  const broken = (sources.data ?? []).filter((item) => item.state === "error" || item.state === "key_missing");
  const running = engine.state === "running" || engine.state === "external";

  return [
    {
      id: "ai",
      label: "Cuenta de IA",
      tone: config.ai_provider === "disabled" ? "error" : aiOk ? "ok" : "error",
      detail:
        config.ai_provider === "disabled"
          ? "No hay suscripción principal: la IA no analiza."
          : aiOk
            ? `${PROVIDER_NAME[config.ai_provider] ?? config.ai_provider} conectado.`
            : `${PROVIDER_NAME[config.ai_provider] ?? config.ai_provider} necesita iniciar sesión.`,
      fix: aiOk ? undefined : { label: "Conectar", to: "/inteligencia/modelos" },
    },
    {
      id: "fallback",
      label: "IA de respaldo",
      tone: config.ai_fallback_providers.length ? "ok" : "warning",
      detail: config.ai_fallback_providers.length
        ? `Si la principal se queda sin cupo entra ${config.ai_fallback_providers.map((id) => PROVIDER_NAME[id] ?? id).join(", ")}.`
        : "Sin respaldo: si la suscripción principal llega a su límite, la IA se detiene hasta que se reinicie.",
      fix: config.ai_fallback_providers.length ? undefined : { label: "Añadir respaldo", to: "/inteligencia/modelos" },
    },
    {
      id: "broker",
      label: "Broker (Alpaca Paper)",
      tone: broker.isLoading ? "neutral" : brokerOk ? "ok" : "error",
      detail: broker.isLoading ? "Comprobando…" : brokerOk ? `Conectado · cuenta ${broker.data?.account?.account_label}.` : apiError(broker.data?.detail ?? broker.error?.message ?? "broker_not_connected"),
      fix: brokerOk ? undefined : { label: "Poner las claves", to: "/ajustes" },
    },
    {
      id: "engine",
      label: "Motor",
      tone: running ? "ok" : "error",
      detail: running
        ? engine.state === "external"
          ? "Funcionando en segundo plano."
          : "Funcionando dentro de la app (se detiene al cerrarla)."
        : "Detenido: no se analiza ni se opera.",
      fix: running
        ? undefined
        : {
            label: "Arrancar",
            busy: startEngine.isPending,
            run: () => startEngine.mutate(60, { onError: (error) => toast({ tone: "error", title: "No se pudo arrancar el motor", description: apiError(error.message) }) }),
          },
    },
    {
      id: "service",
      label: "Motor en segundo plano",
      tone: service.data?.loaded ? "ok" : "warning",
      detail: service.data?.loaded ? "Sigue operando aunque cierres la app, y arranca solo al encender el Mac." : "Solo funciona con la app abierta.",
      fix: service.data?.loaded
        ? undefined
        : {
            label: "Activar",
            busy: setService.isPending,
            run: () =>
              setService.mutate(true, {
                onSuccess: (status) =>
                  toast(status.loaded ? { tone: "success", title: "Motor en segundo plano activado" } : { tone: "error", title: "No se pudo activar", description: status.detail || undefined }),
                onError: (error) => toast({ tone: "error", title: "No se pudo activar el motor en segundo plano", description: apiError(error.message) }),
              }),
          },
    },
    {
      id: "database",
      label: "Base de datos",
      tone: !database ? "neutral" : database.status === "PASS" ? "ok" : "error",
      detail: !database ? "Comprobando…" : database.status === "PASS" ? "Guardando todo correctamente." : database.detail,
    },
    {
      id: "sources",
      label: "Fuentes de datos",
      tone: sources.isLoading ? "neutral" : broken.length ? "warning" : "ok",
      detail: broken.length ? `Necesitan verificación: ${broken.map((item) => item.name).join(", ")}.` : "Todas funcionan.",
      fix: broken.length ? { label: "Arreglar", to: "/noticias/fuentes" } : undefined,
    },
    {
      id: "mode",
      label: "Modo",
      tone: config.live_trading ? "error" : "ok",
      detail: config.live_trading ? "LIVE configurado: esta versión lo bloquea." : "Simulación (paper). El dinero real está bloqueado.",
    },
    {
      id: "backup",
      label: "Copia de seguridad",
      tone: "neutral",
      detail: "Guarda una copia verificada de todo el historial cuando quieras.",
      fix: {
        label: "Hacer copia",
        busy: backup.isPending,
        run: () =>
          backup.mutate(undefined, {
            onSuccess: (result) => toast({ tone: "success", title: "Copia verificada", description: result.filename }),
            onError: (error) => toast({ tone: "error", title: "La copia falló", description: error.message }),
          }),
      },
    },
  ];
}

export function HealthSummary({ items }: { items: HealthItem[] }) {
  const errors = items.filter((item) => item.tone === "error").length;
  const warnings = items.filter((item) => item.tone === "warning").length;
  return (
    <span className={cn("text-body-2", errors ? "text-negative" : warnings ? "text-warning" : "text-positive")}>
      {errors ? `${errors} cosa${errors > 1 ? "s" : ""} necesita${errors > 1 ? "n" : ""} arreglo.` : warnings ? `Todo funciona; ${warnings} aviso${warnings > 1 ? "s" : ""}.` : "Todo está funcionando."}
    </span>
  );
}

/** "Salud del sistema": what is working, what failed and a button to fix it. */
export function SystemHealthDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const snapshot = useSnapshot();
  const items = useHealthItems(snapshot.data);
  const [, navigate] = useLocation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange} width={640} title="Salud del sistema" description={<HealthSummary items={items} />}>
      <ul className="flex flex-col pb-2">
        {items.map((item) => {
          const Icon = ICON[item.tone];
          return (
            <li key={item.id} className="flex items-center gap-3 border-b border-line py-3 last:border-b-0">
              <Icon size={18} className={cn("shrink-0", TONE[item.tone])} aria-hidden />
              <div className="flex min-w-0 flex-1 flex-col">
                <span className="text-body font-medium text-fg">{item.label}</span>
                <span className="text-body-2 text-fg-2">{item.detail}</span>
              </div>
              {item.fix ? (
                <Button
                  size="sm"
                  variant={item.tone === "error" ? "primary" : "secondary"}
                  loading={item.fix.busy}
                  onClick={() => {
                    if (item.fix?.to) {
                      onOpenChange(false);
                      navigate(item.fix.to);
                    } else item.fix?.run?.();
                  }}
                >
                  {item.fix.label}
                </Button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}

export function SystemHealthButton({ onOpen }: { onOpen: () => void }) {
  return (
    <Button icon={HeartPulse} onClick={onOpen}>
      Salud del sistema
    </Button>
  );
}
