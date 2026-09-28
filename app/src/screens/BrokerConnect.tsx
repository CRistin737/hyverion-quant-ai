import { useQueryClient } from "@tanstack/react-query";
import { PlugZap } from "lucide-react";

import { useReconcileBroker } from "@/api/queries";
import type { BrokerAccountSync } from "@/api/types";
import { apiError } from "@/i18n/labels";
import { money } from "@/lib/format";
import { Button } from "@/ui/button";

import { SecretRow, useSecretStatus } from "./SecretRow";

const KEY_NAMES = ["broker:alpaca_paper:key_id", "broker:alpaca_paper:secret_key"] as const;

export type BrokerCheck = { ok: boolean; account: BrokerAccountSync | null; detail: string | null };

/**
 * Alpaca PAPER keys + "Probar conexión". The paper account is the capital:
 * a successful check syncs its equity and reconciles it with the local ledger.
 * Used by onboarding (required) and Ajustes.
 */
export function BrokerConnect({ onChecked }: { onChecked?: (check: BrokerCheck) => void }) {
  const keys = useSecretStatus([...KEY_NAMES]);
  const bothStored = KEY_NAMES.every((name) => keys.data?.[name]);
  const reconcile = useReconcileBroker();
  const queryClient = useQueryClient();
  const result = reconcile.data;
  const ok = result?.status === "OK";
  const account = (result?.account as BrokerAccountSync | undefined) ?? null;

  const test = () =>
    reconcile.mutate(undefined, {
      onSuccess: async (data) => {
        await queryClient.invalidateQueries({ queryKey: ["broker-status"] });
        onChecked?.({ ok: data.status === "OK", account: (data.account as BrokerAccountSync | undefined) ?? null, detail: data.detail ?? null });
      },
      onError: (error) => onChecked?.({ ok: false, account: null, detail: error.message }),
    });

  return (
    <div className="flex flex-col gap-4">
      <ol className="flex flex-col gap-1 text-body-2 text-fg-2">
        <li>1. En app.alpaca.markets elige <span className="text-fg">Paper Trading</span> arriba a la izquierda.</li>
        <li>2. En <span className="text-fg">API Keys</span> pulsa Generate New Keys (el ID empieza por PK).</li>
        <li>3. Pega las dos claves aquí y pulsa Probar conexión.</li>
      </ol>
      <div className="flex flex-col">
        <SecretRow name="broker:alpaca_paper:key_id" label="API Key ID" compact />
        <SecretRow name="broker:alpaca_paper:secret_key" label="Secret Key" compact />
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button icon={PlugZap} disabled={!bothStored} loading={reconcile.isPending} onClick={test}>
          Probar conexión
        </Button>
        {!bothStored ? <span className="text-body-2 text-fg-muted">Guarda las dos claves para probar.</span> : null}
      </div>
      {result ? (
        <p role="status" className={ok ? "text-body-2 text-positive" : "text-body-2 text-warning"}>
          {ok && account
            ? `Conectado · cuenta ${account.account_label} · patrimonio ${money(account.equity)} · poder de compra ${money(account.buying_power)}`
            : `No se pudo verificar: ${apiError(result.detail ?? result.status)}`}
        </p>
      ) : null}
      {reconcile.error ? (
        <p role="alert" className="text-body-2 text-warning">
          {apiError(reconcile.error.message)}
        </p>
      ) : null}
    </div>
  );
}
