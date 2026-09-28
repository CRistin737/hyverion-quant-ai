import { useEffect, useState } from "react";

import { useApi, useSnapshot } from "@/api/provider";
import { useFlatten, useMarketSession, useStartEngine, useStopEngine } from "@/api/queries";
import { engineErrorMessage, marketSession } from "@/i18n/labels";
import { timeNewYork, timeUTC } from "@/lib/format";
import { cn } from "@/ui/cn";
import { Status } from "@/ui/feedback";
import { useToast } from "@/ui/toast";
import { Button } from "@/ui/button";
import { Dialog, Tooltip } from "@/ui/overlay";

/** US market session in New York time: open/closed and the next bell. */
function SessionStatus() {
  const { data } = useMarketSession();
  if (!data) return null;
  const detail = data.is_open
    ? `cierra ${timeNewYork(data.close_at)} ET${data.early_close ? " (cierre temprano)" : ""}`
    : `abre ${timeNewYork(data.next_open, { weekday: true })} ET`;
  return (
    <Tooltip content="Horario de la bolsa de Nueva York. Hyverion solo abre operaciones en el horario regular (09:30–16:00 ET).">
      <span>
        <Status tone={data.is_open ? "positive" : "neutral"}>
          {marketSession(data.state)} · {detail}
        </Status>
      </span>
    </Tooltip>
  );
}

/** Persistent system status: connection, engine data freshness, reconciliation, mode. */
export function StatusBar() {
  const { connection, session } = useApi();
  const { data } = useSnapshot();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);

  const lastTick = data?.markets[0]?.event_time;
  const tickAge = lastTick ? (now - new Date(lastTick.endsWith("Z") ? lastTick : `${lastTick}Z`).getTime()) / 1000 : null;
  const engine = data?.engine;
  const fresh = tickAge !== null && tickAge < 120;
  const engineTone =
    engine?.state === "exited" ? "negative" : engine?.state === "running" || engine?.state === "external" ? (fresh || tickAge === null ? "positive" : "warning") : "neutral";
  const engineLabel =
    engine?.state === "running"
      ? fresh ? "Motor activo" : "Motor activo · esperando datos"
      : engine?.state === "external"
        ? "Motor activo (externo)"
        : engine?.state === "exited"
          ? `Motor detenido con error${engine.exit_code !== null ? ` (${engine.exit_code})` : ""}`
          : "Motor detenido";
  const startEngine = useStartEngine();
  const stopEngine = useStopEngine();
  const toast = useToast();
  const flatten = useFlatten();
  const [confirmFlatten, setConfirmFlatten] = useState(false);
  const [confirmStop, setConfirmStop] = useState(false);
  const openPositions = data?.positions.length ?? 0;
  const busy = startEngine.isPending || stopEngine.isPending;
  const onStart = () =>
    startEngine.mutate(60, {
      onSuccess: () => toast({ tone: "success", title: "Motor iniciado", description: "Evalúa el mercado cada 60 s, solo en simulación." }),
      onError: (error) => toast({ tone: "error", title: "No se pudo iniciar el motor", description: engineErrorMessage(error.message) }),
    });
  const onStop = () =>
    stopEngine.mutate(undefined, {
      onSuccess: () => toast({ tone: "info", title: "Motor detenido", description: "No se evaluarán nuevas operaciones hasta que lo inicies." }),
      onError: (error) => toast({ tone: "error", title: "No se pudo detener el motor", description: error.message }),
    });

  const connectionTone = connection === "live" ? "positive" : connection === "offline" ? "negative" : "warning";
  const connectionLabel =
    session.fixture ? "Modo demostración" : connection === "live" ? "Núcleo conectado" : connection === "offline" ? "Núcleo sin conexión" : connection === "polling" ? "Reconectando…" : "Conectando…";

  const reconciliation = data?.reconciliation;
  const reconTone = !reconciliation || reconciliation.status === "NOT_RUN" ? "neutral" : reconciliation.safe_mode || reconciliation.mismatches.length ? "negative" : "positive";
  const reconLabel =
    !reconciliation || reconciliation.status === "NOT_RUN"
      ? "Conciliación sin ejecutar"
      : reconTone === "positive"
        ? "Conciliación correcta"
        : "Conciliación con diferencias";

  return (
    <footer
      data-print-hide
      aria-label="Estado del sistema"
      className={cn(
        "flex h-7 shrink-0 items-center gap-5 border-t border-line bg-surface-1 px-3 text-caption",
        connection === "offline" && "bg-negative-soft",
      )}
    >
      {/* Only connection/mode changes are announced; the clock is not. */}
      <span role="status" aria-live="polite">
        <Status tone={connectionTone} pulse={connection === "live"}>
          {connectionLabel}
        </Status>
      </span>
      <SessionStatus />
      <Status tone={engineTone} pulse={engine?.state === "running" && fresh}>
        {engineLabel}
      </Status>
      {session.fixture || connection === "live" ? (
        engine?.state === "running" ? (
          <button type="button" onClick={() => (openPositions > 0 ? setConfirmStop(true) : onStop())} disabled={busy} className="text-fg-2 underline-offset-2 hover:text-fg hover:underline disabled:opacity-50">
            {stopEngine.isPending ? "Deteniendo…" : "Detener"}
          </button>
        ) : engine && engine.state !== "external" ? (
          <button type="button" onClick={onStart} disabled={busy} className="font-medium text-fg underline-offset-2 hover:underline disabled:opacity-50">
            {startEngine.isPending ? "Iniciando…" : "Iniciar motor"}
          </button>
        ) : null
      ) : null}
      {engine?.state === "running" && engine.flatten_pending ? (
        <>
          <Status tone="warning">Cerrando posiciones…</Status>
          <button type="button" onClick={() => flatten.mutate(true)} className="text-fg-2 underline-offset-2 hover:text-fg hover:underline">
            Cancelar
          </button>
        </>
      ) : engine?.state === "running" && openPositions > 0 ? (
        <button type="button" onClick={() => setConfirmFlatten(true)} className="text-negative underline-offset-2 hover:underline">
          Cerrar posiciones
        </button>
      ) : null}
      <Dialog
        open={confirmStop}
        onOpenChange={setConfirmStop}
        title="¿Detener el motor con posiciones abiertas?"
        description={`Hay ${openPositions} posición${openPositions > 1 ? "es" : ""} abierta${openPositions > 1 ? "s" : ""}. Siguen protegidas por su stop en Alpaca (válido aunque el motor esté parado y de un día para otro), pero mientras esté detenido no se aplican el cierre por tiempo, el objetivo ni el cierre antes del final de la sesión.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmStop(false)}>
              Seguir operando
            </Button>
            <Button
              variant="secondary"
              onClick={() => {
                setConfirmStop(false);
                setConfirmFlatten(true);
              }}
            >
              Cerrar posiciones primero
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setConfirmStop(false);
                onStop();
              }}
            >
              Detener igualmente
            </Button>
          </>
        }
      />
      <Dialog
        open={confirmFlatten}
        onOpenChange={setConfirmFlatten}
        title="¿Cerrar todas las posiciones?"
        description={`El motor cerrará las ${openPositions} posiciones abiertas (simulación) en su próximo ciclo y no abrirá otras nuevas hasta que estén cerradas.`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmFlatten(false)}>
              Cancelar
            </Button>
            <Button
              variant="destructive"
              loading={flatten.isPending}
              onClick={() =>
                flatten.mutate(false, {
                  onSuccess: () => {
                    setConfirmFlatten(false);
                    toast({ tone: "info", title: "Cierre solicitado", description: "El motor cerrará las posiciones en su próximo ciclo." });
                  },
                  onError: (error) => {
                    setConfirmFlatten(false);
                    toast({ tone: "error", title: "No se pudo solicitar el cierre", description: engineErrorMessage(error.message) });
                  },
                })
              }
            >
              Cerrar posiciones
            </Button>
          </>
        }
      />
      <Tooltip content={reconciliation?.checked_at ? `Última verificación ${timeUTC(reconciliation.checked_at)} UTC` : "Se comprueba sola cada pocos minutos; también desde Ajustes › Salud del sistema"}>
        <span>
          <Status tone={reconTone}>{reconLabel}</Status>
        </span>
      </Tooltip>
      <span className="flex-1" />
      <span className="num text-fg-muted">Actualizado {data ? timeUTC(data.generated_at) : "—"} UTC</span>
    </footer>
  );
}
