import { Archive, ArrowUpRight, BookOpen } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useParams } from "wouter";

import { useBackup, useBrokerStatus, useServiceStatus, useSetService } from "@/api/queries";
import type { ConfigPatch, PublicConfig } from "@/api/types";
import { apiError } from "@/i18n/labels";
import { money } from "@/lib/format";
import { REGION_OPTIONS } from "@/lib/regions";
import { applyTheme } from "@/lib/theme";
import { PageBody, PageHeader } from "@/shell/PageHeader";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Section } from "@/ui/data";
import { Badge, Kbd } from "@/ui/feedback";
import { Select, Switch } from "@/ui/form";
import { Segmented } from "@/ui/nav";
import { useToast } from "@/ui/toast";

import { SnapshotGate } from "../common";
import { ApplyBar, SettingRow, useDraft } from "../settings-kit";
import { BrokerConnect } from "../BrokerConnect";
import { SystemHealthButton, SystemHealthDialog } from "./SystemHealth";

/* The things that live elsewhere now, so nobody looks for them here. */
const ELSEWHERE: Array<{ label: string; where: string; to: string }> = [
  { label: "Suscripciones de IA, límites y modelo de cada tarea", where: "Inteligencia › Modelos", to: "/inteligencia/modelos" },
  { label: "Fuentes de noticias, calendario y redes", where: "Noticias › Fuentes", to: "/noticias/fuentes" },
  { label: "Perfil de riesgo", where: "Riesgo › Límites", to: "/riesgo/limites" },
];

function BackgroundEngine() {
  const service = useServiceStatus();
  const setService = useSetService();
  const toast = useToast();
  const on = Boolean(service.data?.loaded);
  return (
    <SettingRow
      label="Motor en segundo plano"
      description="Sigue analizando y operando aunque cierres la app, y arranca solo al encender el Mac. Para un año de campaña, déjalo activado."
    >
      <Switch
        label="Motor en segundo plano"
        checked={on}
        disabled={service.isLoading || setService.isPending}
        onCheckedChange={(checked) =>
          setService.mutate(checked, {
            onSuccess: (status) =>
              toast({
                tone: status.loaded === checked ? "success" : "error",
                title: checked ? (status.loaded ? "Motor en segundo plano activado" : "No se pudo activar") : "Motor en segundo plano desactivado",
                description: status.detail || undefined,
              }),
            onError: (error) => toast({ tone: "error", title: "No se pudo cambiar el motor", description: apiError(error.message) }),
          })
        }
      />
    </SettingRow>
  );
}

function TradingAccount({ config }: { config: PublicConfig }) {
  const initial = useMemo<ConfigPatch>(() => ({ operating_region: config.operating_region ?? "US" }), [config]);
  const { draft, set, changes, reset } = useDraft(initial);
  const status = useBrokerStatus();
  const account = status.data?.account ?? null;
  const brokerState = status.data
    ? status.data.connected && account
      ? `Conectado · cuenta ${account.account_label} · patrimonio ${money(account.equity)} · poder de compra ${money(account.buying_power)}`
      : `Sin conexión: ${apiError(status.data.detail)}`
    : null;

  return (
    <Section title="Cuenta de trading" description="Tu cuenta Alpaca Paper es el capital: de su patrimonio salen el tamaño de cada operación y los límites.">
      <div>
        <SettingRow label="Instrumento" description="Hyverion solo compra y vende QQQ. El Nasdaq-100, SPY y la volatilidad son sensores: nunca se operan.">
          <Badge>QQQ · solo compras · horario regular</Badge>
        </SettingRow>
        <SettingRow label="Broker" description="Alpaca Paper: dinero ficticio en un broker real. El modo real está bloqueado. Otros brokers con API llegarán después.">
          <Badge>Alpaca Paper</Badge>
        </SettingRow>
        {brokerState ? (
          <SettingRow label="Estado de la cuenta" description="Solo lectura. La cuenta se muestra enmascarada; las claves nunca salen del Llavero.">
            <span role="status" className="text-body-2 text-fg-2">
              {brokerState}
            </span>
          </SettingRow>
        ) : null}
        <SettingRow label="Claves de Alpaca Paper" description="Se guardan en el Llavero y también dan acceso a los precios, noticias y opciones de QQQ." align="start">
          <div className="w-full">
            <BrokerConnect />
          </div>
        </SettingRow>
        <SettingRow label="País" description="Para comprobar que el broker da servicio donde vives antes de cualquier modo real.">
          <Select aria-label="País" value={draft.operating_region ?? "US"} onValueChange={(value) => set("operating_region", value)} options={REGION_OPTIONS} className="w-full" />
        </SettingRow>
      </div>
      <ApplyBar changes={changes} onReset={reset} />
    </Section>
  );
}

function Appearance() {
  const backup = useBackup();
  const toast = useToast();
  const [theme, setTheme] = useState<"dark" | "light">(() => (document.documentElement.dataset.theme === "light" ? "light" : "dark"));
  return (
    <Section title="Apariencia y datos">
      <div>
        <SettingRow label="Tema" description="El oscuro cansa menos en sesiones largas.">
          <Segmented
            label="Tema"
            size="md"
            value={theme}
            onChange={(value) => {
              setTheme(value);
              applyTheme(value);
            }}
            items={[
              { value: "dark", label: "Oscuro" },
              { value: "light", label: "Claro" },
            ]}
          />
        </SettingRow>
        <SettingRow label="Copia de seguridad" description="Guarda una copia verificada de todo el historial: decisiones, operaciones y auditoría.">
          <Button
            icon={Archive}
            loading={backup.isPending}
            onClick={() =>
              backup.mutate(undefined, {
                onSuccess: (result) => toast({ tone: "success", title: "Copia de seguridad verificada", description: result.filename }),
                onError: (error) => toast({ tone: "error", title: "La copia de seguridad falló", description: error.message }),
              })
            }
          >
            Crear copia
          </Button>
        </SettingRow>
      </div>
    </Section>
  );
}

const FLOW = ["Datos", "Agentes", "Propuesta", "Crítico", "Riesgo", "Ejecución"];

const SHORTCUTS: Array<[string, string]> = [
  ["⌘K", "Buscar o ejecutar cualquier cosa"],
  ["⌘1 – ⌘6", "Ir a cada área"],
  ["Esc", "Cerrar diálogos y paneles"],
];

function Help() {
  return (
    <Section title="Ayuda" description="Cómo decide el sistema y cómo moverte rápido.">
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <ol className="flex flex-wrap items-center gap-1.5" aria-label="Cadena de decisión">
            {FLOW.map((step, index) => (
              <li key={step} className="flex items-center gap-1.5">
                <span className={cn("rounded-xs px-2 py-1 text-body-2", step === "Riesgo" ? "bg-accent-soft text-accent-text" : "bg-surface-2 text-fg")}>{step}</span>
                {index < FLOW.length - 1 ? <span className="text-fg-muted" aria-hidden>→</span> : null}
              </li>
            ))}
          </ol>
          <p className="max-w-[72ch] text-body-2 text-fg-2">
            La IA solo analiza y propone. El crítico intenta refutar cada propuesta, el control de riesgo decide con la última palabra y solo el motor de
            ejecución puede comprar o vender. Cada posición tiene un stop que no depende de la IA. Simulación es el modo por defecto; el modo real está bloqueado.
          </p>
        </div>
        <dl className="grid grid-cols-[120px_1fr] gap-y-2">
          {SHORTCUTS.map(([keys, description]) => (
            <div key={keys} className="contents">
              <dt>
                <Kbd>{keys}</Kbd>
              </dt>
              <dd className="text-body-2 text-fg-2">{description}</dd>
            </div>
          ))}
        </dl>
        <p className="flex items-center gap-2 text-body-2 text-fg-2">
          <BookOpen size={14} strokeWidth={1.5} aria-hidden /> Guías completas en la carpeta <span className="mono">docs/</span> del proyecto.
        </p>
      </div>
    </Section>
  );
}

function Elsewhere() {
  return (
    <aside aria-label="Dónde está cada ajuste" className="flex flex-col gap-2 rounded-md bg-surface-1 p-4">
      <p className="text-body-2 font-medium text-fg">Cada cosa se configura donde se ve</p>
      <ul className="flex flex-col gap-1.5">
        {ELSEWHERE.map((item) => (
          <li key={item.to} className="flex flex-wrap items-center justify-between gap-2 text-body-2">
            <span className="text-fg-2">{item.label}</span>
            <Link href={item.to} className="inline-flex items-center gap-1 text-fg underline-offset-2 hover:underline">
              {item.where} <ArrowUpRight size={13} aria-hidden />
            </Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}

/** Ajustes: one short page — trading account, engine, appearance, help and the health check. */
export default function Ajustes() {
  const params = useParams<{ panel?: string }>();
  const [, navigate] = useLocation();
  const [health, setHealth] = useState(params.panel === "salud");
  useEffect(() => {
    if (params.panel === "salud") setHealth(true);
  }, [params.panel]);
  return (
    <>
      <PageHeader title="Ajustes" actions={<SystemHealthButton onOpen={() => setHealth(true)} />} />
      <PageBody>
        <div className="stagger mx-auto flex w-full max-w-[880px] flex-col gap-10">
          <p className="text-body-2 text-fg-2">Tu cuenta de trading, el motor, la apariencia y la ayuda. Lo demás se ajusta en su propia pantalla.</p>
          <Elsewhere />
          <SnapshotGate>{(snapshot) => <TradingAccount config={snapshot.config} />}</SnapshotGate>
          <Section title="Motor">
            <BackgroundEngine />
          </Section>
          <Appearance />
          <Help />
        </div>
      </PageBody>
      <SystemHealthDialog
        open={health}
        onOpenChange={(open) => {
          setHealth(open);
          if (!open && params.panel === "salud") navigate("/ajustes", { replace: true });
        }}
      />
    </>
  );
}
