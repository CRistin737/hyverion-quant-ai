import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Check, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { useApplyConfig } from "@/api/queries";
import type { ConfigPatch } from "@/api/types";
import { apiError, configError } from "@/i18n/labels";
import { money } from "@/lib/format";
import { REGION_OPTIONS } from "@/lib/regions";
import { Button } from "@/ui/button";
import { cn } from "@/ui/cn";
import { KeyValue } from "@/ui/data";
import { Field, Select } from "@/ui/form";
import { BrokerConnect, type BrokerCheck } from "../BrokerConnect";
import { SecretRow, useSecretStatus } from "../SecretRow";


const STEPS = [
  { id: "broker", title: "Conecta tu cuenta paper", summary: "Alpaca Paper es tu capital de simulación" },
  { id: "ia", title: "Inteligencia artificial", summary: "Qué modelo analiza el mercado" },
  { id: "revision", title: "Perfil de riesgo", summary: "Cuánto arriesgar, en % de tu cuenta" },
] as const;

const PROVIDERS = [
  { value: "disabled", label: "Sin IA por ahora", description: "Los ciclos que dependen de IA se detienen de forma segura." },
  { value: "anthropic", label: "Claude (Anthropic)" },
  { value: "openai", label: "OpenAI / Codex" },
  { value: "gemini", label: "Gemini (Google)" },
  { value: "xai", label: "Grok (xAI)" },
];

/** Same values as config/risk_profiles.py (percent of the broker equity). */
const RISK_PROFILES = [
  { id: "conservador", label: "Conservador", trade: "0.25", day: "1", week: "2.5" },
  { id: "medio", label: "Medio (recomendado)", trade: "0.5", day: "2", week: "5" },
  { id: "alto", label: "Alto", trade: "1", day: "3", week: "8" },
] as const;

function usd(equity: string | null, percent: string): string {
  return equity ? money(((Number(equity) * Number(percent)) / 100).toFixed(0), { dp: 0 }) : "—";
}

/**
 * First-run setup inside the app (replaces the CLI requirement). Always PAPER,
 * QQQ only, long-only. Secrets go to the Keychain, the rest through
 * validate → apply.
 */
export default function Onboarding() {
  const [step, setStep] = useState(0);
  const [check, setCheck] = useState<BrokerCheck | null>(null);
  const [region, setRegion] = useState("US");
  const [provider, setProvider] = useState("disabled");
  const [profile, setProfile] = useState<(typeof RISK_PROFILES)[number]["id"]>("medio");
  const apply = useApplyConfig();
  const queryClient = useQueryClient();

  const contact = useSecretStatus(["data:contact_email"]);
  // SEC and BLS only serve clients that identify themselves; without the BLS
  // calendar the macro gate fails closed and nothing trades.
  const contactOk = Boolean(contact.data?.["data:contact_email"]);
  const brokerOk = Boolean(check?.ok && check.account) && contactOk;
  const equity = check?.account?.equity ?? null;
  const stepValid = [brokerOk, true, true][step] ?? true;

  const patch: ConfigPatch = useMemo(
    () => ({
      broker_provider: "alpaca",
      operating_region: region,
      primary_provider: provider,
      primary_auth_mode: "subscription",
      risk_profile: profile,
    }),
    [region, provider, profile],
  );

  const serverErrors = apply.data?.validation.valid === false ? apply.data.validation.errors : [];

  const finish = () =>
    apply.mutate(patch, {
      onSuccess: async ({ result }) => {
        if (result?.applied) await queryClient.invalidateQueries({ queryKey: ["setup"] });
      },
    });

  return (
    <div className="flex h-full bg-bg">
      <aside data-tauri-drag-region className="flex w-[380px] shrink-0 flex-col justify-between bg-surface-1 px-10 pb-10 pt-16 max-lg:w-[300px] max-lg:px-8">
        <div className="flex flex-col gap-10">
          <svg viewBox="0 0 64 64" aria-hidden className="size-7 text-fg">
            <g fill="currentColor">
              <rect x="10" y="10" width="10" height="44" rx="1" />
              <rect x="44" y="10" width="10" height="44" rx="1" />
              <rect x="30.75" y="17" width="2.5" height="30" />
              <rect x="26" y="25" width="12" height="14" rx="1" />
            </g>
          </svg>
          <div className="flex flex-col gap-3">
            <h1 className="text-display font-semibold text-fg">Configura Hyverion</h1>
            <p className="text-body text-fg-2">
              Tres pasos y el sistema empieza a operar QQQ en tu cuenta paper de Alpaca: dinero ficticio en un broker real. Puedes cambiar todo después en Ajustes.
            </p>
          </div>
          <ol className="flex flex-col gap-1" aria-label="Pasos">
            {STEPS.map((item, index) => {
              const done = index < step;
              const current = index === step;
              return (
                <li key={item.id} aria-current={current ? "step" : undefined} className="flex items-start gap-3 py-2">
                  <span
                    className={cn(
                      "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold",
                      done ? "bg-accent text-on-accent" : current ? "text-fg shadow-[inset_0_0_0_1.5px_var(--text-primary)]" : "text-fg-muted shadow-[inset_0_0_0_1px_var(--border-strong)]",
                    )}
                  >
                    {done ? <Check size={12} strokeWidth={2.5} aria-hidden /> : index + 1}
                  </span>
                  <span className="flex flex-col">
                    <span className={cn("text-body font-medium", current || done ? "text-fg" : "text-fg-muted")}>{item.title}</span>
                    <span className="text-caption text-fg-muted">{item.summary}</span>
                  </span>
                </li>
              );
            })}
          </ol>
        </div>
        <p className="flex items-start gap-2 text-caption text-fg-muted">
          <ShieldCheck size={14} strokeWidth={1.5} className="mt-0.5 shrink-0" aria-hidden />
          Modo simulación, solo compras de QQQ en horario regular. El modo real permanece bloqueado hasta completar las auditorías de preparación.
        </p>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <div data-tauri-drag-region className="h-12 shrink-0" />
        <div className="flex flex-1 justify-center overflow-y-auto px-10 pb-10">
          <div className="flex w-full max-w-[520px] flex-col gap-8 pt-10">
            <header className="flex flex-col gap-1">
              <span className="text-caption text-fg-muted">
                Paso {step + 1} de {STEPS.length}
              </span>
              <h2 className="text-[22px] font-semibold leading-7 tracking-[-0.015em] text-fg">{STEPS[step]?.title}</h2>
            </header>

            {step === 0 ? (
              <div className="flex flex-col gap-6">
                <p className="text-body-2 text-fg-2">
                  Hyverion no usa un capital inventado: el patrimonio de tu cuenta <span className="text-fg">Alpaca Paper</span> es el capital. De él salen el tamaño de cada
                  operación y los límites, y los topes en dólares mandan siempre.
                </p>
                <BrokerConnect onChecked={setCheck} />
                <div className="flex flex-col gap-1">
                  <SecretRow name="data:contact_email" label="Tu email de contacto" />
                  <p className="text-caption text-fg-muted">
                    La Reserva Federal, el BLS y la SEC publican gratis el calendario económico y los informes de las empresas. El BLS y la SEC piden que los programas
                    se identifiquen con un email. Se guarda en el Llavero y solo se envía a esas fuentes oficiales.
                  </p>
                </div>
                <KeyValue
                  items={[
                    { label: "Instrumento", value: "QQQ (ETF del Nasdaq-100)" },
                    { label: "Operaciones", value: "Solo compras, en horario regular de EE. UU." },
                    { label: "Sensores", value: "Nasdaq-100, SPY, volatilidad, macro y noticias; nunca se operan" },
                  ]}
                />
                <Field label="País desde el que operas" hint="Sirve para comprobar que el broker da servicio en tu país antes de cualquier modo real.">
                  {(id, describedBy) => <Select id={id} describedBy={describedBy} value={region} onValueChange={setRegion} options={REGION_OPTIONS} />}
                </Field>
              </div>
            ) : null}

            {step === 1 ? (
              <div className="flex flex-col gap-6">
                <p className="text-body-2 text-fg-2">
                  Los agentes de IA analizan y proponen; nunca ejecutan. Cada propuesta pasa por el crítico y por el motor de riesgo determinista antes de cualquier orden.
                </p>
                <Field label="Proveedor principal">
                  {(id, describedBy) => <Select id={id} describedBy={describedBy} value={provider} onValueChange={setProvider} options={PROVIDERS} />}
                </Field>
                {provider !== "disabled" ? (
                  <>
                    <p className="text-body-2 text-fg-2">
                      Se usa tu suscripción: al terminar podrás iniciar sesión con el enlace oficial del proveedor en Inteligencia › Modelos. No necesitas API keys.
                    </p>
                  </>
                ) : null}
              </div>
            ) : null}

            {step === 2 ? (
              <div className="flex flex-col gap-6">
                <p className="text-body-2 text-fg-2">
                  Como un trader profesional: se arriesga un porcentaje de la cuenta, no una cifra fija. El control de riesgo lo aplica siempre y puedes cambiarlo después en Riesgo.
                </p>
                <div role="radiogroup" aria-label="Perfil de riesgo" className="grid grid-cols-3 gap-3">
                  {RISK_PROFILES.map((item) => (
                    <button
                      key={item.id}
                      type="button"
                      role="radio"
                      aria-checked={profile === item.id}
                      onClick={() => setProfile(item.id)}
                      className={cn("flex flex-col gap-2 rounded-lg border p-3 text-left", profile === item.id ? "border-accent bg-accent-soft" : "border-line hover:border-line-strong")}
                    >
                      <span className="text-body font-medium text-fg">{item.label}</span>
                      <span className="num text-caption text-fg-2">
                        {item.trade}% por operación · {usd(equity, item.trade)}
                      </span>
                      <span className="num text-caption text-fg-muted">
                        {item.day}% al día · {item.week}% a la semana
                      </span>
                    </button>
                  ))}
                </div>
                <KeyValue
                  items={[
                    { label: "Patrimonio (Alpaca Paper)", value: <span className="num">{equity ? money(equity) : "—"}</span> },
                    { label: "Instrumento", value: "QQQ" },
                    { label: "Broker", value: check?.account ? `Alpaca Paper · ${check.account.account_label}` : "Alpaca Paper" },
                    { label: "IA", value: PROVIDERS.find((p) => p.value === provider)?.label ?? provider },
                    { label: "Modo", value: "Simulación (paper)" },
                  ]}
                />
                {serverErrors.length || apply.error ? (
                  <div role="alert" className="flex flex-col gap-1 rounded-md bg-negative-soft px-4 py-3">
                    <p className="text-body font-medium text-fg">No se pudo guardar la configuración</p>
                    {serverErrors.map((error) => (
                      <p key={error} className="text-body-2 text-fg-2">
                        {configError(error)}
                      </p>
                    ))}
                    {apply.error ? <p className="text-body-2 text-fg-2">{apiError(apply.error.message)}</p> : null}
                  </div>
                ) : null}
                {apply.data?.result && !apply.data.result.applied ? <p className="text-body-2 text-warning">{apiError(apply.data.result.detail)}</p> : null}
              </div>
            ) : null}

            <footer className="flex items-center justify-between border-t border-line pt-6">
              {step > 0 ? (
                <Button variant="ghost" icon={ArrowLeft} onClick={() => setStep((s) => s - 1)}>
                  Atrás
                </Button>
              ) : (
                <span />
              )}
              {step < STEPS.length - 1 ? (
                <Button variant="primary" size="lg" iconRight={ArrowRight} disabled={!stepValid} onClick={() => setStep((s) => s + 1)}>
                  Continuar
                </Button>
              ) : (
                <Button variant="primary" size="lg" disabled={!stepValid} loading={apply.isPending} onClick={finish}>
                  Iniciar en modo simulación
                </Button>
              )}
            </footer>
          </div>
        </div>
      </main>
    </div>
  );
}
