import { ArrowUpRight, RefreshCw } from "lucide-react";

import { useSourcesStatus, useTestSource } from "@/api/queries";
import type { SecretName } from "@/api/secrets";
import type { DataSourceStatus } from "@/api/types";
import { apiError, type Tone } from "@/i18n/labels";
import { relative } from "@/lib/format";
import { Button } from "@/ui/button";
import { Badge, EmptyState, Skeleton } from "@/ui/feedback";

import { ExternalLink } from "@/ui/external-link";

import { SecretRow } from "./SecretRow";

const STATE: Record<DataSourceStatus["state"], { label: string; tone: Tone }> = {
  ok: { label: "Funcionando", tone: "positive" },
  error: { label: "Necesita verificación", tone: "negative" },
  never_run: { label: "Sin leer todavía", tone: "neutral" },
  key_missing: { label: "Necesita verificación: falta la clave", tone: "warning" },
};

/** Where to get each free key, in plain Spanish. */
const KEY_HELP: Record<string, { label: string; how: string; url?: string }> = {
  "data:contact_email": {
    label: "Tu email de contacto",
    how: "La SEC y el BLS solo atienden programas que se identifican con un email. No es una contraseña y no se comparte con nadie más.",
  },
  "data:fred:api_key": {
    label: "Clave de FRED",
    how: "Gratis: crea una cuenta en fred.stlouisfed.org › My Account › API Keys › Request API Key.",
    url: "https://fredaccount.stlouisfed.org/apikeys",
  },
  "data:finnhub:api_key": {
    label: "Clave de Finnhub",
    how: "Gratis: regístrate en finnhub.io; la clave aparece en tu panel (plan Free).",
    url: "https://finnhub.io/register",
  },
};

const CATEGORY: Record<string, string> = {
  OFFICIAL_REGULATOR: "Oficial",
  OFFICIAL_COMPANY: "Empresa",
  LICENSED_WIRE: "Agencia",
  AGGREGATOR: "Agregador",
};

function SourceRow({ source }: { source: DataSourceStatus }) {
  const test = useTestSource();
  // The contact e-mail is not a key: say what is actually missing.
  const state =
    source.state === "key_missing" && source.key === "data:contact_email"
      ? { label: "Necesita verificación: falta tu email", tone: STATE.key_missing.tone }
      : STATE[source.state];
  const help = source.key ? KEY_HELP[source.key] : undefined;
  const tested = test.data;
  return (
    <li className="flex flex-col gap-3 border-b border-line py-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-body font-medium text-fg">{source.name}</span>
            <Badge>{CATEGORY[source.category] ?? source.category}</Badge>
            <Badge tone={state.tone}>{state.label}</Badge>
          </div>
          <p className="max-w-[64ch] text-body-2 text-fg-2">{source.purpose}</p>
          <p className="text-caption text-fg-muted">
            {source.last_run_at ? `Última lectura ${relative(source.last_run_at)}` : "Aún no se ha leído"}
            {source.last_error ? ` · ${apiError(source.last_error)}` : ""}
          </p>
        </div>
        <Button size="sm" icon={RefreshCw} loading={test.isPending} disabled={!source.key_present} onClick={() => test.mutate(source.id)}>
          Probar
        </Button>
      </div>
      {help && !source.key?.startsWith("broker:") ? (
        <div className="flex flex-col gap-1 rounded-md bg-surface-2 px-3 py-2">
          <SecretRow name={source.key as SecretName} label={help.label} compact />
          <p className="text-caption text-fg-muted">
            {help.how}{" "}
            {help.url ? (
              <ExternalLink href={help.url} className="inline-flex items-center gap-0.5 text-fg underline-offset-2 hover:underline">
                Conseguirla <ArrowUpRight size={12} aria-hidden />
              </ExternalLink>
            ) : null}
          </p>
        </div>
      ) : null}
      {source.key?.startsWith("broker:") ? <p className="text-caption text-fg-muted">Usa las claves de Alpaca Paper de la cuenta de trading.</p> : null}
      {tested && tested.id === source.id ? (
        <p role="status" className="text-caption text-fg-2">
          {tested.state === "ok" ? "Lectura correcta." : `Resultado: ${STATE[tested.state].label}${tested.last_error ? ` (${apiError(tested.last_error)})` : ""}.`}
        </p>
      ) : null}
      {test.error ? <p role="alert" className="text-caption text-warning">{apiError(test.error.message)}</p> : null}
    </li>
  );
}

/**
 * Free data sources: official calendars, SEC, FRED, news and options.
 * Same pattern as the Alpaca keys — paste, "Probar", done. Keys go to the Keychain.
 */
export function DataSources() {
  const sources = useSourcesStatus();
  if (sources.isLoading) return <Skeleton className="h-40" />;
  if (!sources.data?.length) return <EmptyState compact title="Sin fuentes" description="El núcleo no devolvió el registro de fuentes." />;
  return <ul className="flex flex-col">{sources.data.map((source) => <SourceRow key={source.id} source={source} />)}</ul>;
}
