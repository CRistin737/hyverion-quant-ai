import { LogIn, PlugZap, Repeat } from "lucide-react";
import { useEffect, useMemo } from "react";

import { useAiUsage, useChainProbe, useProviderAction, useProviderLogin, useSwitchAccount } from "@/api/queries";
import type { ConfigPatch, LoginSession, Provider, PublicConfig, Snapshot, SubscriptionUsage } from "@/api/types";
import { authState, authTone, billingMode, circuitReason, loginFailure, providerDetail } from "@/i18n/labels";
import { decimal, timeUTC } from "@/lib/format";
import { Button } from "@/ui/button";
import { Section } from "@/ui/data";
import { Badge, EmptyState } from "@/ui/feedback";
import { Checkbox, Select } from "@/ui/form";
import { useToast } from "@/ui/toast";

import { errorText } from "../shared";
import { ApplyBar, SettingRow, useDraft } from "../settings-kit";
import { ModelRoles, UsageBars } from "../ai-usage";

const PROVIDER_OPTIONS = [
  { value: "disabled", label: "Sin IA (solo reglas fijas)" },
  { value: "anthropic", label: "Claude (Anthropic)" },
  { value: "openai", label: "ChatGPT / Codex (OpenAI)" },
  { value: "gemini", label: "Gemini (Google)" },
  { value: "xai", label: "Grok (xAI)" },
];
const PROVIDER_IDS = ["anthropic", "openai", "gemini", "xai"] as const;

function count(value: number): string {
  return decimal(value, { dp: 0 });
}

/** Which subscription the agents use and which ones take over. */
function ChainSettings({ config }: { config: PublicConfig }) {
  const initial = useMemo<ConfigPatch>(
    () => ({
      primary_provider: config.ai_provider,
      primary_auth_mode: "subscription",
      fallback_providers: config.ai_fallback_providers,
    }),
    [config],
  );
  const { draft, set, changes, reset } = useDraft(initial);
  // Subscription is the only access mode in the app; an old API setting is migrated on save.
  const pending = config.ai_auth_mode === "api" ? { ...changes, primary_auth_mode: "subscription" as const } : changes;
  const primary = draft.primary_provider ?? "disabled";
  const fallbacks = draft.fallback_providers ?? [];
  return (
    <Section title="Qué IA usan los agentes" description="Una suscripción principal y otras de respaldo. Si la principal se queda sin cupo o se cae, entra la siguiente sin cortar el trabajo.">
      <div>
        <SettingRow label="Suscripción principal">
          <Select aria-label="Suscripción principal" value={primary} onValueChange={(value) => {
              set("primary_provider", value);
              // The primary can never also be a fallback, and "no AI" has no fallbacks.
              set("fallback_providers", value === "disabled" ? [] : fallbacks.filter((id) => id !== value));
            }} options={PROVIDER_OPTIONS} className="w-full" />
        </SettingRow>
        <SettingRow label="Respaldo" description="Se usan en el orden en que las marcas." align="start">
          <div className="flex w-full flex-col gap-2">
            {PROVIDER_IDS.filter((id) => id !== primary).map((id) => {
              const position = fallbacks.indexOf(id);
              return (
                <Checkbox
                  key={id}
                  disabled={primary === "disabled"}
                  label={
                    <span className="inline-flex items-center gap-2">
                      {PROVIDER_OPTIONS.find((p) => p.value === id)?.label ?? id}
                      {position >= 0 ? <Badge mono>{position + 1}.º</Badge> : null}
                    </span>
                  }
                  checked={position >= 0}
                  onCheckedChange={(checked) => set("fallback_providers", checked ? [...fallbacks, id] : fallbacks.filter((p) => p !== id))}
                />
              );
            })}
          </div>
        </SettingRow>
      </div>
      <ApplyBar changes={pending} onReset={reset} />
    </Section>
  );
}

function LoginProgress({ session, onCancel }: { session: LoginSession; onCancel: () => void }) {
  const toast = useToast();
  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text).then(() => toast({ tone: "info", title: "Copiado" }));
  };
  if (session.state === "manual") {
    return (
      <div className="mt-2 rounded-md bg-surface-2 px-3 py-2 text-body-2 text-fg-2">
        Este proveedor necesita una terminal interactiva. Abre la Terminal, ejecuta{" "}
        <code className="rounded-xs bg-surface-3 px-1 font-mono text-fg">{session.manual_command}</code> y completa el acceso con Google.
        <Button size="sm" variant="ghost" className="ml-2" onClick={() => copy(session.manual_command ?? "")}>
          Copiar comando
        </Button>
      </div>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-3 rounded-md bg-surface-2 px-3 py-2 text-body-2 text-fg-2" role="status" aria-live="polite">
      <span>
        {session.state === "verifying"
          ? "Verificando la conexión…"
          : session.url
            ? "Se abrió el navegador. Inicia sesión y vuelve aquí; se conectará solo."
            : "Preparando el inicio de sesión oficial…"}
      </span>
      {session.url ? (
        <Button size="sm" variant="ghost" onClick={() => copy(session.url ?? "")}>
          Copiar enlace
        </Button>
      ) : null}
      <Button size="sm" variant="ghost" onClick={onCancel}>
        Cancelar
      </Button>
    </div>
  );
}

function ProviderRow({ provider, role, usage }: { provider: Provider; role: "primary" | "fallback" | null; usage: SubscriptionUsage | undefined }) {
  const toast = useToast();
  const action = useProviderAction();
  const switchAccount = useSwitchAccount(provider.provider_id);
  const connected = provider.auth_state === "CONNECTED";
  const pending = action.isPending ? action.variables?.action : undefined;
  const canLogin = provider.official_login_available && provider.auth_state !== "CONNECTED";

  const login = useProviderLogin(provider.provider_id);
  const startLogin = () =>
    login.start.mutate(undefined, {
      onError: (error) => toast({ tone: "error", title: `No se pudo iniciar sesión en ${provider.display_name}`, description: error.message }),
    });
  // Announce the outcome once when a login settles.
  const settled = login.session?.state;
  const settledAt = login.session?.finished_at;
  useEffect(() => {
    if (!settledAt) return;
    if (settled === "connected") toast({ tone: "success", title: `${provider.display_name} conectado`, description: "Tu suscripción quedó lista para los agentes." });
    if (settled === "failed" && login.session?.detail !== "cancelled")
      toast({ tone: "error", title: `No se pudo conectar ${provider.display_name}`, description: loginFailure(login.session?.detail ?? "") });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, settledAt]);

  const run = (kind: "test") => {
    action.mutate(
      { providerId: provider.provider_id, action: kind },
      {
        onSuccess: (result) => {
          const detail = typeof result.detail === "string" ? providerDetail(result.detail) : undefined;
          toast({
            tone: "success",
            title: `${provider.display_name} respondió correctamente`,
            description: detail ?? "La conexión con el proveedor funciona.",
          });
        },
        onError: (error) => {
          toast({
            tone: "error",
            title: `La prueba de ${provider.display_name} falló`,
            description: `${error instanceof Error ? error.message : "Error desconocido"}. Si es el proveedor principal, el sistema usa el respaldo.`,
          });
        },
      },
    );
  };

  return (
    <li className="flex flex-col gap-3 border-b border-line py-4 first:pt-0 last:border-0">
     <div className="flex items-center gap-4">
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body font-medium text-fg">{provider.display_name}</span>
          <Badge tone={authTone(provider.auth_state)}>{authState(provider.auth_state)}</Badge>
          {role ? <Badge>{role === "primary" ? "Principal" : "Respaldo"}</Badge> : null}
        </div>
        <p className="truncate text-body-2 text-fg-2">
          {billingMode(provider.billing_mode)}
          {provider.context_window ? <span className="num"> · contexto {count(provider.context_window)} tokens</span> : null}
        </p>
        {login.session && (login.active || login.session.state === "manual") ? (
          <LoginProgress session={login.session} onCancel={() => login.cancel.mutate()} />
        ) : null}
        {provider.circuit ? (
          <p className="text-caption text-warning">
            En pausa: {circuitReason(provider.circuit.last_code)}. El sistema usa el siguiente proveedor y reintentará a las{" "}
            <span className="num">{timeUTC(provider.circuit.retry_at)}</span> UTC.
          </p>
        ) : null}
        {provider.detail ? <p className="truncate text-caption text-fg-muted">{providerDetail(provider.detail)}</p> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {canLogin ? (
          <Button size="sm" icon={LogIn} loading={login.start.isPending || login.active} disabled={action.isPending || login.active} onClick={startLogin}>
            Iniciar sesión
          </Button>
        ) : null}
        {connected && provider.official_login_available ? (
          <Button
            size="sm"
            variant="ghost"
            icon={Repeat}
            loading={switchAccount.isPending}
            disabled={login.active}
            onClick={() =>
              switchAccount.mutate(undefined, {
                onError: (error) => toast({ tone: "error", title: `No se pudo cambiar de cuenta en ${provider.display_name}`, description: errorText(error) }),
              })
            }
          >
            Cambiar de cuenta
          </Button>
        ) : null}
        {provider.billing_mode === "api" ? (
          <Button size="sm" variant="ghost" icon={PlugZap} loading={pending === "test"} disabled={action.isPending} onClick={() => run("test")}>
            Probar
          </Button>
        ) : null}
      </div>
     </div>
      {connected ? <UsageBars usage={usage} /> : null}
    </li>
  );
}

export function Modelos({ snapshot }: { snapshot: Snapshot }) {
  const probe = useChainProbe();
  const toast = useToast();
  const usage = useAiUsage();
  const { providers, config } = snapshot;
  const fallbacks = config.ai_fallback_providers ?? [];
  const roleOf = (id: string) => (id === config.ai_provider ? "primary" : fallbacks.includes(id) ? "fallback" : null);
  const ordered = [...providers].sort((a, b) => {
    const rank = (id: string) => (id === config.ai_provider ? 0 : fallbacks.includes(id) ? 1 + fallbacks.indexOf(id) : 99);
    return rank(a.provider_id) - rank(b.provider_id);
  });

  return (
    <div className="stagger flex flex-col gap-9">
      <Section title="Modelo de cada tarea" description="Sonnet lee mucha información a bajo coste; Opus razona mejor para decidir y mejorar. También lo cambias desde el botón Modelo de arriba.">
        <ModelRoles />
      </Section>
      <ChainSettings config={config} />
      <Section
        title="Conexión de cada suscripción"
        description="Inicia sesión como en la app oficial: se abre el navegador y vuelves aquí. Las barras muestran lo que queda de la sesión de 5 horas y lo usado de la semana."
        actions={
          <Button
            size="sm"
            icon={PlugZap}
            loading={probe.isPending}
            disabled={!config.ai_provider || config.ai_provider === "disabled"}
            onClick={() =>
              probe.mutate(undefined, {
                onSuccess: () => toast({ tone: "success", title: "La cadena respondió", description: "Prueba de conexión; no analiza mercado ni crea órdenes." }),
                onError: (error) => toast({ tone: "error", title: "La prueba de la cadena falló", description: errorText(error) }),
              })
            }
          >
            Probar la cadena
          </Button>
        }
      >
        {ordered.length ? (
          <ul className="flex flex-col">
            {ordered.map((provider) => (
              <ProviderRow key={provider.provider_id} provider={provider} role={roleOf(provider.provider_id)} usage={usage.data?.providers.find((item) => item.provider_id === provider.provider_id)} />
            ))}
          </ul>
        ) : (
          <EmptyState compact title="No hay proveedores de IA disponibles" description="Sin IA, los componentes deterministas y las protecciones siguen funcionando." />
        )}
      </Section>
    </div>
  );
}
