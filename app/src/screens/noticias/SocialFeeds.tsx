import { useMemo, useState, type ReactNode } from "react";

import type { ConfigPatch, PublicConfig, Snapshot, Source } from "@/api/types";
import { errorCode, sourceName } from "@/i18n/labels";
import { dateUTC, decimal, duration } from "@/lib/format";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { Section } from "@/ui/data";
import { Badge, Status } from "@/ui/feedback";
import { Checkbox, Field, Input, Select, Switch } from "@/ui/form";
import { Segmented } from "@/ui/nav";
import { Dialog, Tooltip } from "@/ui/overlay";

import { SecretRow } from "../SecretRow";
import { ApplyBar, SettingRow, useDraft } from "../settings-kit";

const INTERVALS = [
  { value: "60", label: "Cada minuto" },
  { value: "300", label: "Cada 5 minutos" },
  { value: "900", label: "Cada 15 minutos" },
  { value: "3600", label: "Cada hora" },
];

function freshness(source: Source) {
  if (!source.enabled) return <span className="text-fg-muted">—</span>;
  return (
    <Tooltip content={source.last_success_at ? `Último dato: ${dateUTC(source.last_success_at)}` : "Todavía sin datos"}>
      <span className="num text-caption text-fg-2">
        {source.freshness_seconds === null || source.freshness_seconds === undefined ? "sin datos" : `hace ${duration(source.freshness_seconds)}`}
        {source.records_today ? ` · ${decimal(source.records_today, { dp: 0 })} hoy` : ""}
      </span>
    </Tooltip>
  );
}

function SourceLine({ source, control, detail }: { source: Source; control: ReactNode; detail?: ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line py-2.5 last:border-0">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className={cn("text-body", source.enabled ? "text-fg" : "text-fg-2")}>{sourceName(source.source_id)}</span>
        {detail}
        {source.enabled && source.last_error ? <span className="text-caption text-negative">{errorCode(source.last_error)}</span> : null}
      </div>
      {freshness(source)}
      <Status tone={source.enabled ? "positive" : "neutral"}>{source.enabled ? "Activa" : "Apagada"}</Status>
      {control}
    </li>
  );
}

/** Turning a news feed on needs its exact RSS address and an explicit review of its terms. */
function FeedDialog({ sourceId, onClose, onSave }: { sourceId: string | null; onClose: () => void; onSave: (url: string) => void }) {
  const [url, setUrl] = useState("");
  const [reviewed, setReviewed] = useState(false);
  const valid = /^https?:\/\/\S+$/.test(url.trim());
  const close = () => {
    setUrl("");
    setReviewed(false);
    onClose();
  };
  return (
    <Dialog
      open={sourceId !== null}
      onOpenChange={(open) => !open && close()}
      title={sourceId ? `Activar ${sourceName(sourceId)}` : ""}
      description="El sistema solo lee fuentes cuya dirección exacta y condiciones de uso revisaste tú. Su texto se trata como dato, nunca como instrucción."
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            disabled={!valid || !reviewed}
            onClick={() => {
              onSave(url.trim());
              close();
            }}
          >
            Activar
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Dirección RSS" hint="Por ejemplo https://…/feed.xml" error={url && !valid ? "Escribe una dirección que empiece por http:// o https://." : null}>
          {(id, describedBy) => <Input id={id} aria-describedby={describedBy} value={url} onChange={(event) => setUrl(event.target.value)} placeholder="https://" autoFocus />}
        </Field>
        <Checkbox checked={reviewed} onCheckedChange={setReviewed} label="Revisé que esta fuente permite leer su RSS de forma automatizada." />
      </div>
    </Dialog>
  );
}

function SourcesForm({ snapshot, config }: { snapshot: Snapshot; config: PublicConfig }) {
  const external = config.external_data;
  const initial = useMemo<ConfigPatch>(
    () => ({
      news_provider: external.news,
      news_feeds: external.news_feeds,
      news_reviewed_sources: external.news_reviewed_sources,
      social_provider: external.social,
      collection_interval_seconds: external.collection_interval_seconds,
      collection_max_backoff_seconds: external.collection_max_backoff_seconds,
    }),
    [external],
  );
  const { draft, set, changes, reset } = useDraft(initial);
  const [adding, setAdding] = useState<string | null>(null);
  const feeds = draft.news_feeds ?? {};
  const reviewed = draft.news_reviewed_sources ?? [];
  const bySource = (category: string) => snapshot.sources.filter((source) => source.category === category);
  const newsOn = (draft.news_provider ?? "disabled") === "rss";
  const social = draft.social_provider ?? "disabled";

  const setFeed = (id: string, url: string | null) => {
    const nextFeeds = { ...feeds };
    const nextReviewed = reviewed.filter((item) => item !== id);
    if (url) {
      nextFeeds[id] = url;
      nextReviewed.push(id);
    } else delete nextFeeds[id];
    set("news_feeds", nextFeeds);
    set("news_reviewed_sources", nextReviewed);
    if (url && !newsOn) set("news_provider", "rss");
  };

  return (
    <div className="flex flex-col gap-9">
      <Section title="Mercado" description={`Precios de QQQ (${config.market_data_provider === "fixture" ? "datos sintéticos de prueba" : `Alpaca, feed ${(config.market_data_feed ?? "iex").toUpperCase()}`}). Siempre activa: sin ella el motor no opera.`}>
        <ul>
          {bySource("market").map((source) => (
            <SourceLine key={source.source_id} source={source} control={<Badge>Siempre activa</Badge>} />
          ))}
        </ul>
      </Section>

      <Section
        title="Noticias"
        description="Titulares de fuentes que revisaste. Los agentes los usan como contexto; nunca como órdenes."
        actions={
          <span className="flex items-center gap-2 text-body-2 text-fg-2">
            {newsOn ? "Noticias activadas" : "Noticias apagadas"}
            <Switch label="Usar noticias" checked={newsOn} onCheckedChange={(checked) => set("news_provider", checked ? "rss" : "disabled")} />
          </span>
        }
      >
        <ul className={cn("grid grid-cols-2 gap-x-10 max-xl:grid-cols-1", !newsOn && "opacity-60")}>
          {bySource("news").map((source) => {
            const on = Boolean(feeds[source.source_id]) && reviewed.includes(source.source_id);
            return (
              <SourceLine
                key={source.source_id}
                source={{ ...source, enabled: newsOn && on && source.enabled }}
                detail={on ? <span className="truncate text-caption text-fg-muted">{feeds[source.source_id]}</span> : null}
                control={
                  <Switch
                    label={`Usar ${sourceName(source.source_id)}`}
                    checked={on}
                    disabled={!newsOn}
                    onCheckedChange={(checked) => (checked ? setAdding(source.source_id) : setFeed(source.source_id, null))}
                  />
                }
              />
            );
          })}
        </ul>
      </Section>

      <Section title="Redes sociales" description="Solo por la API oficial de cada red, con tu propia clave.">
        <div>
          <SettingRow label="Red social">
            <Segmented
              label="Red social"
              value={social}
              onChange={(value) => set("social_provider", value)}
              items={[
                { value: "disabled", label: "Ninguna" },
                { value: "x", label: "X" },
                { value: "reddit", label: "Reddit" },
              ]}
            />
          </SettingRow>
          {social === "x" ? (
            <SettingRow label="Clave de X" description="Token de la API oficial de X.">
              <SecretRow name="source:x_bearer_token" label="Token de X" compact />
            </SettingRow>
          ) : null}
          {social === "reddit" ? (
            <SettingRow label="Claves de Reddit" description="Credenciales de una app de la API oficial de Reddit." align="start">
              <div className="flex w-full flex-col">
                <SecretRow name="source:reddit_client_id" label="Client ID" compact />
                <SecretRow name="source:reddit_client_secret" label="Client secret" compact />
              </div>
            </SettingRow>
          ) : null}
          <ul>
            {bySource("social").map((source) => (
              <SourceLine key={source.source_id} source={{ ...source, enabled: social !== "disabled" && source.enabled }} control={null} />
            ))}
          </ul>
        </div>
      </Section>

      <Section title="Cada cuánto se consulta" description="Aplica a noticias y redes. Si una fuente falla, se espera más entre intentos.">
        <div>
          <SettingRow label="Frecuencia">
            <Select
              aria-label="Frecuencia"
              value={String(draft.collection_interval_seconds ?? 300)}
              onValueChange={(value) => {
                const seconds = Number(value);
                set("collection_interval_seconds", seconds);
                set("collection_max_backoff_seconds", Math.max(seconds * 4, draft.collection_max_backoff_seconds ?? 0));
              }}
              options={INTERVALS}
              className="w-full"
            />
          </SettingRow>
        </div>
      </Section>

      <ApplyBar changes={changes} onReset={reset} />
      <FeedDialog sourceId={adding} onClose={() => setAdding(null)} onSave={(url) => adding && setFeed(adding, url)} />
    </div>
  );
}

export function Fuentes({ snapshot }: { snapshot: Snapshot }) {
  const active = snapshot.sources.filter((source) => source.enabled).length;
  return (
    <div className="flex flex-col gap-6">
      <p className="text-body-2 text-fg-2">
        {active} de {snapshot.sources.length} fuentes activas. El texto de noticias y redes se limpia y se trata como dato no confiable.
      </p>
      <SourcesForm snapshot={snapshot} config={snapshot.config} />
    </div>
  );
}
