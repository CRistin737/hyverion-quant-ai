/**
 * OS keychain access through the Tauri shell.
 *
 * Secrets go webview → Rust → Keychain directly. They are never sent to the
 * control API over HTTP and can never be read back — only their presence.
 * Names mirror `trading_bot.security.secrets.KeyringSecretStore` usage.
 */

import { invoke, isTauri } from "@tauri-apps/api/core";

export type SecretName =
  | "broker:alpaca_paper:key_id"
  | "broker:alpaca_paper:secret_key"
  | "source:news_api_key"
  | "source:x_bearer_token"
  | "source:reddit_client_id"
  | "source:reddit_client_secret"
  | "data:fred:api_key"
  | "data:finnhub:api_key"
  | "data:contact_email"
  | `provider:${"anthropic" | "openai" | "xai" | "gemini"}:api_key`;

export function keychainAvailable(): boolean {
  return isTauri();
}

export async function saveSecret(name: SecretName, value: string): Promise<void> {
  if (!isTauri()) throw new Error("El Llavero solo está disponible en la app de escritorio.");
  await invoke("secret_set", { name, value });
}

export async function deleteSecret(name: SecretName): Promise<void> {
  if (!isTauri()) throw new Error("El Llavero solo está disponible en la app de escritorio.");
  await invoke("secret_delete", { name });
}

export async function secretStatus(names: SecretName[]): Promise<Record<string, boolean>> {
  if (!isTauri()) return Object.fromEntries(names.map((name) => [name, false]));
  const rows = await invoke<Array<[string, boolean]>>("secret_status", { names });
  return Object.fromEntries(rows);
}
