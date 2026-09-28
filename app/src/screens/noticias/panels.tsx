import { CalendarClock, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { useIntelligence, useRefreshIntelligence } from "@/api/queries";
import type { BreadthView, Intelligence, MacroEventView } from "@/api/types";
import { apiError, riskReason } from "@/i18n/labels";
import { decimal, parseUTC, relative, sign, timeNewYork } from "@/lib/format";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Metric, Section } from "@/ui/data";
import { Badge, EmptyState, Skeleton } from "@/ui/feedback";
import { ExternalLink } from "@/ui/external-link";
import { Segmented } from "@/ui/nav";

/* Display-only helpers: the core computes everything with Decimal. */
const share = (value: string | null | undefined, dp = 0) => (value == null ? "—" : `${decimal(Number(value) * 100, { dp, trim: false })}%`);
const bps = (value: string | null | undefined) => (value == null ? "—" : `${decimal(value, { dp: 1, signed: true })} pb`);

const IMPORTANCE: Record<MacroEventView["importance"], { label: string; dots: number; tone: "negative" | "warning" | "neutral" }> = {
  HIGH: { label: "Alto", dots: 3, tone: "negative" },
  MEDIUM: { label: "Medio", dots: 2, tone: "warning" },
  LOW: { label: "Bajo", dots: 1, tone: "neutral" },
};

const EVENT_TYPE: Record<string, string> = {
  FOMC_DECISION: "FOMC · decisión de tipos",
  FOMC_PRESS_CONFERENCE: "FOMC · rueda de prensa",
  FOMC_MINUTES: "FOMC · actas",
  FED_SPEECH: "Fed · discurso",
  FED_TESTIMONY: "Fed · comparecencia",
  BEIGE_BOOK: "Beige Book",
  CPI: "Inflación (CPI)",
  PPI: "Precios productor (PPI)",
  NFP: "Empleo (NFP)",
  JOLTS: "Vacantes (JOLTS)",
  ECI: "Coste laboral (ECI)",
  PCE: "Ingresos y gasto (PCE)",
  GDP: "PIB",
  GDP_ADVANCE: "PIB (avance)",
};

const DIVERGENCE: Record<string, string> = {
  qqq_up_breadth_weak: "QQQ sube con pocas acciones en verde",
  megacap_only_rally: "La subida es solo de las megacaps",
  qqq_down_weighted_breadth_strong: "QQQ baja pero el peso en verde es mayoría",
  megacaps_holding_on_red_day: "Las megacaps aguantan en un día rojo",
  concentrated_leadership: "El movimiento lo explican pocas empresas",
};

function countdown(minutes: number): string {
  if (minutes < 0) return `hace ${Math.abs(minutes)} min`;
  if (minutes < 60) return `en ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `en ${hours} h ${minutes % 60} min`;
  return `en ${Math.floor(hours / 24)} días`;
}

function dayNewYork(iso: string): string {
  const date = parseUTC(iso);
  return date ? new Intl.DateTimeFormat("es", { timeZone: "America/New_York", weekday: "short", day: "2-digit", month: "short" }).format(date) : "—";
}

function GateBanner({ intel }: { intel: Intelligence }) {
  const gate = intel.macro.gate;
  const next = intel.macro.upcoming.find((event) => event.importance === "HIGH" && event.minutes_to_event >= 0);
  return (
    <div role="status" className={cn("flex flex-wrap items-center justify-between gap-3 rounded-md px-4 py-3", gate ? "bg-negative-soft" : "bg-surface-1")}>
      <div className="flex items-center gap-2">
        {gate ? <ShieldAlert size={16} className="text-negative" aria-hidden /> : <ShieldCheck size={16} className="text-positive" aria-hidden />}
        <span className="text-body font-medium text-fg">{gate ? riskReason(gate) : "Sin eventos de alto impacto en este momento."}</span>
      </div>
      {next ? (
        <span className="flex items-center gap-1.5 text-body-2 text-fg-2">
          <CalendarClock size={14} aria-hidden /> Próximo alto impacto: <span className="text-fg">{EVENT_TYPE[next.event_type] ?? next.title}</span> · {dayNewYork(next.scheduled_at)} {timeNewYork(next.scheduled_at)} ET ·{" "}
          <span className="num text-fg">{countdown(next.minutes_to_event)}</span>
        </span>
      ) : null}
    </div>
  );
}

function Heatmap({ breadth }: { breadth: BreadthView }) {
  return (
    <ul className="grid grid-cols-5 gap-1.5 max-lg:grid-cols-4" aria-label="Mapa de las megacaps">
      {breadth.megacaps.map((row) => {
        const s = sign(row.change);
        const strength = Math.min(1, Math.abs(Number(row.change)) / 0.02);
        return (
          <li
            key={row.symbol}
            className={cn("flex flex-col gap-0.5 rounded-sm px-2.5 py-2", s > 0 ? "text-positive" : s < 0 ? "text-negative" : "text-fg-2")}
            style={{ backgroundColor: `color-mix(in oklab, var(--${s >= 0 ? "positive" : "negative"}) ${Math.round(8 + strength * 22)}%, transparent)` }}
          >
            <span className="text-body font-semibold text-fg">{row.symbol}</span>
            <span className="num text-body-2">{decimal(Number(row.change) * 100, { dp: 2, signed: true, suffix: "%" })}</span>
            <span className="num text-caption text-fg-muted">
              peso {share(row.weight, 1)} · {bps(row.contribution_bps)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function Breadth({ breadth }: { breadth: BreadthView | null }) {
  if (!breadth) {
    return <EmptyState compact title="Sin amplitud todavía" description="Hace falta el email de contacto (SEC) y las claves de Alpaca. Se calcula en cada ciclo con el mercado abierto." />;
  }
  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-5 gap-6 max-lg:grid-cols-3">
        <Metric label="En verde" value={share(breadth.pct_green)} context={`${breadth.advancers} suben · ${breadth.decliners} bajan`} />
        <Metric label="Peso en verde" value={share(breadth.weighted_breadth)} context="ponderado por peso en QQQ" />
        <Metric label="Sin las 10 mayores" value={share(breadth.ex_top10_breadth)} context="en verde" />
        <Metric label="Sobre su VWAP" value={share(breadth.pct_above_vwap)} context="de los componentes" />
        <Metric label="QQQ estimado" value={decimal(Number(breadth.estimated_index_change) * 100, { dp: 2, signed: true, suffix: "%" })} context={`las 10 mayores explican ${share(breadth.top10_contribution_share)}`} />
      </div>
      {breadth.divergences.length ? (
        <div className="flex flex-wrap gap-2">
          {breadth.divergences.map((item) => (
            <Badge key={item} tone="warning">
              {DIVERGENCE[item] ?? item}
            </Badge>
          ))}
        </div>
      ) : null}
      <Heatmap breadth={breadth} />
      <p className="text-caption text-fg-muted">Pesos estimados con la cartera oficial de QQQ (SEC N-PORT) y los precios de hoy · {relative(breadth.as_of)}</p>
    </div>
  );
}

function Calendar({ events }: { events: MacroEventView[] }) {
  const [filter, setFilter] = useState<"high" | "all">("high");
  const rows = useMemo(() => events.filter((event) => (filter === "all" ? true : event.importance !== "LOW")), [events, filter]);
  if (!events.length) return <EmptyState compact title="Sin calendario" description="Se lee de la Fed, el BLS y el BEA. Pulsa Actualizar o revisa Noticias › Fuentes." />;
  let lastDay = "";
  return (
    <div className="flex flex-col gap-3">
      <Segmented
        label="Importancia"
        value={filter}
        onChange={setFilter}
        items={[
          { value: "high", label: "Alto y medio" },
          { value: "all", label: "Todos" },
        ]}
      />
      <ol className="flex flex-col" aria-label="Calendario económico">
        {rows.map((event) => {
          const day = dayNewYork(event.scheduled_at);
          const header = day !== lastDay ? day : null;
          lastDay = day;
          const importance = IMPORTANCE[event.importance];
          return (
            <li key={event.event_id} className="flex flex-col">
              {header ? <span className="mt-3 border-b border-line pb-1 text-label capitalize text-fg-muted">{header}</span> : null}
              <div className="grid grid-cols-[64px_56px_1fr_auto] items-center gap-3 py-2">
                <span className="num text-body-2 text-fg">{event.time_known ? timeNewYork(event.scheduled_at) : "—"}</span>
                <span className="flex gap-0.5" aria-label={`Importancia ${importance.label}`}>
                  {[1, 2, 3].map((dot) => (
                    <span key={dot} className={cn("size-1.5 rounded-full", dot <= importance.dots ? (importance.tone === "negative" ? "bg-negative" : importance.tone === "warning" ? "bg-warning" : "bg-fg-muted") : "bg-surface-3")} />
                  ))}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-body text-fg">{EVENT_TYPE[event.event_type] ?? event.title}</span>
                  {event.event_type.startsWith("FED_") || !EVENT_TYPE[event.event_type] ? <span className="block truncate text-caption text-fg-muted">{event.title}</span> : null}
                </span>
                <span className="num text-caption text-fg-muted">{countdown(event.minutes_to_event)}</span>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="text-caption text-fg-muted">Hora de Nueva York. El consenso no se publica gratis de forma oficial, así que no se muestra ni se inventa.</p>
    </div>
  );
}

function RatesAndVolatility({ intel }: { intel: Intelligence }) {
  const series = intel.rates?.series ?? [];
  const vol = intel.volatility;
  return (
    <div className="grid grid-cols-2 gap-8 max-lg:grid-cols-1">
      <div className="flex flex-col gap-3">
        <span className="text-label text-fg-muted">Tipos y dólar (FRED, cierre diario)</span>
        {series.length ? (
          <div className="grid grid-cols-2 gap-4">
            {series.map((point) => (
              <Metric key={point.series_id} label={point.label} value={decimal(point.value, { dp: 2 })} context={point.change_1d ? `${decimal(point.change_1d, { dp: 2, signed: true })} en el día` : point.as_of} />
            ))}
          </div>
        ) : (
          <p className="text-body-2 text-fg-muted">{intel.rates?.status === "key_missing" ? "Falta la clave gratuita de FRED (Noticias › Fuentes)." : "Sin datos de tipos todavía."}</p>
        )}
      </div>
      <div className="flex flex-col gap-3">
        <span className="text-label text-fg-muted">Volatilidad (realizada frente a implícita)</span>
        {vol && vol.status !== "unavailable" ? (
          <div className="grid grid-cols-3 gap-4">
            <Metric label="Realizada" value={share(vol.realized_vol_annual, 1)} context="anual, barras de QQQ" />
            <Metric label="Implícita ATM" value={share(vol.atm_iv, 1)} context={vol.near_expiry ? `vence ${vol.near_expiry}` : "opciones"} />
            <Metric label="VIX" value={vol.vix_close ? decimal(vol.vix_close, { dp: 2 }) : "—"} context="cierre" />
            <Metric label="Skew 25Δ" value={vol.skew_25d ? decimal(Number(vol.skew_25d) * 100, { dp: 1, suffix: " pts" }) : "—"} context="put − call" />
            <Metric label="Movimiento esperado" value={share(vol.expected_move_pct, 2)} context="straddle ATM" />
            <Metric label="Estructura" value={vol.term_slope == null ? "—" : Number(vol.term_slope) < 0 ? "Invertida" : "Normal"} context="siguiente − próxima" />
          </div>
        ) : (
          <p className="text-body-2 text-fg-muted">Sin datos de opciones. Nunca se inventan: el sensor queda como no disponible.</p>
        )}
      </div>
    </div>
  );
}

function NewsList({ intel }: { intel: Intelligence }) {
  if (!intel.news.length) return <p className="text-body-2 text-fg-muted">Sin noticias recientes.</p>;
  return (
    <ul className="flex flex-col">
      {intel.news.slice(0, 30).map((item) => (
        <li key={item.cluster_id} className="flex items-start justify-between gap-3 border-b border-line py-2.5 last:border-b-0">
          <span className="min-w-0">
            <span className="block text-body text-fg">{item.headline}</span>
            <span className="text-caption text-fg-muted">
              {item.symbols.join(", ") || "QQQ"} · peso en QQQ {share(item.qqq_relevance, 1)} · {item.sources.join(", ") || "Alpaca News"}
              {item.copies > 1 ? ` · ${item.copies} copias` : ""} · {relative(item.first_published_at)}
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2">
            {item.sentiment > 0 ? <Badge tone="positive">Positiva</Badge> : item.sentiment < 0 ? <Badge tone="negative">Negativa</Badge> : null}
            <Badge tone={item.materiality === "HIGH" ? "warning" : "neutral"}>{item.materiality === "HIGH" ? "Material" : item.materiality === "MEDIUM" ? "Media" : "Baja"}</Badge>
          </span>
        </li>
      ))}
    </ul>
  );
}

function Earnings({ intel }: { intel: Intelligence }) {
  if (!intel.earnings.length) return <p className="text-body-2 text-fg-muted">Sin calendario de resultados. Falta la clave gratuita de Finnhub (Noticias › Fuentes).</p>;
  return (
    <ul className="grid grid-cols-2 gap-x-8 max-lg:grid-cols-1">
      {intel.earnings.slice(0, 20).map((event) => (
        <li key={`${event.symbol}-${event.date}`} className="flex justify-between border-b border-line py-2 text-body-2">
          <span className="font-medium text-fg">{event.symbol}</span>
          <span className="text-fg-2">
            {event.date} · {event.session === "before_open" ? "antes de abrir" : event.session === "after_close" ? "tras el cierre" : "durante la sesión"}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Filings({ intel }: { intel: Intelligence }) {
  if (!intel.filings.length) return <p className="text-body-2 text-fg-muted">Sin documentos recientes de las 20 mayores.</p>;
  return (
    <ul className="flex flex-col">
      {intel.filings.slice(0, 12).map((filing) => (
        <li key={filing.accession} className="flex justify-between gap-3 border-b border-line py-2 text-body-2 last:border-b-0">
          <ExternalLink href={filing.url} className="text-fg underline-offset-2 hover:underline">
            {filing.symbol} · {filing.form}
            {filing.is_earnings_release ? " · resultados" : ""}
          </ExternalLink>
          <span className="text-fg-muted">{relative(filing.filed_at)}</span>
        </li>
      ))}
    </ul>
  );
}

/** Where each block of data comes from, shown on every card. */
function SourceTag({ children }: { children: string }) {
  return <span className="text-caption text-fg-muted">Fuente: {children}</span>;
}

function useIntel() {
  const intel = useIntelligence();
  const refresh = useRefreshIntelligence();
  return { intel, refresh };
}

function Loading({ intel }: { intel: ReturnType<typeof useIntelligence> }) {
  if (intel.isLoading) return <Skeleton className="h-96" />;
  return <EmptyState title="Sin información" description={intel.error ? apiError(intel.error.message) : "El núcleo no respondió."} />;
}

function RefreshButton({ refresh }: { refresh: ReturnType<typeof useRefreshIntelligence> }) {
  return (
    <Button size="sm" icon={RefreshCw} loading={refresh.isPending} onClick={() => refresh.mutate()}>
      Actualizar
    </Button>
  );
}

/** What is coming: macro calendar (Myfxbook style) and company results. */
export function CalendarView() {
  const { intel, refresh } = useIntel();
  if (!intel.data) return <Loading intel={intel} />;
  const data = intel.data;
  return (
    <div className="stagger flex flex-col gap-10">
      <GateBanner intel={data} />
      <Section
        title="Calendario económico"
        description="Reuniones y ruedas de prensa de la Fed, inflación, empleo, PIB y discursos. La IA lo tiene en cuenta al decidir."
        actions={<RefreshButton refresh={refresh} />}
      >
        <Calendar events={data.macro.upcoming} />
        <SourceTag>Reserva Federal, BLS y BEA (calendarios oficiales)</SourceTag>
      </Section>
      <Section title="Resultados de empresas" description="Cuándo presentan resultados las empresas del Nasdaq-100.">
        <Earnings intel={data} />
        <SourceTag>Finnhub (plan gratuito)</SourceTag>
      </Section>
    </div>
  );
}

/** Everything reaching the system right now, each block with its source. */
export function NewsView() {
  const { intel, refresh } = useIntel();
  if (!intel.data) return <Loading intel={intel} />;
  const data = intel.data;
  const errors = Object.entries(refresh.data?.errors ?? data.errors ?? {});
  return (
    <div className="stagger flex flex-col gap-10">
      <Section title="Noticias" description="Agrupadas: 30 copias de la misma noticia cuentan como una." actions={<RefreshButton refresh={refresh} />}>
        <NewsList intel={data} />
        <SourceTag>Alpaca News (Benzinga)</SourceTag>
      </Section>
      <Section title="Documentos ante la SEC" description="8-K, 10-Q y 10-K de las mayores empresas del índice.">
        <Filings intel={data} />
        <SourceTag>SEC EDGAR</SourceTag>
      </Section>
      <Section title="Amplitud del Nasdaq-100" description="Si QQQ sube con todo el índice o solo con unas pocas empresas.">
        <Breadth breadth={data.breadth} />
        <SourceTag>Alpaca (precios) y SEC N-PORT (pesos de QQQ)</SourceTag>
      </Section>
      <Section title="Tipos y volatilidad">
        <RatesAndVolatility intel={data} />
        <SourceTag>FRED (tipos, dólar y VIX) y Alpaca Options (volatilidad implícita)</SourceTag>
      </Section>
      {errors.length ? (
        <p className="text-caption text-warning">
          Fuentes con problemas: {errors.map(([source, error]) => `${source} (${apiError(String(error))})`).join(" · ")}. Revísalas en Noticias › Fuentes.
        </p>
      ) : null}
    </div>
  );
}
