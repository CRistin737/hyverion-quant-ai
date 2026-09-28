/**
 * Session discovery: where the control API lives and which bearer token to use.
 *
 * - Inside Tauri: the Rust shell started the core and returns the session.
 * - In a plain browser (vite dev / Playwright): VITE_API_BASE + VITE_API_TOKEN.
 * - Fixture mode (`?fixture=demo` or VITE_FIXTURE): no network at all.
 */

import { invoke, isTauri } from "@tauri-apps/api/core";

import { reportClientError } from "./client";

/**
 * `tauri`: every call goes through the Rust proxy; the token never reaches JS.
 * `http`: plain fetch + WebSocket (browser development only).
 * `fixture`: in-memory demo data.
 */
export type Transport = "tauri" | "http" | "fixture";

export interface Session {
  transport: Transport;
  apiBase: string;
  token: string;
  managedCore: boolean;
  fixture: string | null;
}

function fixtureName(): string | null {
  const fromQuery = new URLSearchParams(window.location.search).get("fixture");
  return fromQuery ?? (import.meta.env.VITE_FIXTURE as string | undefined) ?? null;
}

export async function loadSession(): Promise<Session> {
  const fixture = fixtureName();
  if (fixture) return { transport: "fixture", apiBase: "", token: "", managedCore: false, fixture };
  if (isTauri()) {
    trace("get_session");
    const session = await invoke<{ managedCore: boolean }>("get_session");
    trace("health gate");
    await waitForCore();
    trace("core ready");
    return { transport: "tauri", apiBase: "", token: "", managedCore: session.managedCore, fixture: null };
  }
  const apiBase = import.meta.env.VITE_API_BASE as string | undefined;
  const token = import.meta.env.VITE_API_TOKEN as string | undefined;
  if (!apiBase || !token) {
    throw new Error(
      "Sin sesión: abre la app de escritorio o define VITE_API_BASE y VITE_API_TOKEN.",
    );
  }
  return { transport: "http", apiBase, token, managedCore: false, fixture: null };
}

/**
 * Health gate: the shell starts the Python core in parallel with the webview,
 * so wait until it reports ready before any authenticated request is made.
 */
export async function waitForCore(timeoutMs = 90_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let delay = 250;
  let reported = false;
  while (Date.now() < deadline) {
    try {
      const response = await invoke<{ status: number; body: { status?: string } }>("api_request", {
        method: "GET",
        path: "/health/ready",
        body: null,
      });
      if (response.status === 200 && response.body.status === "ready") return;
      if (response.status === 200) {
        throw new Error("El núcleo arrancó en modo seguro: la base de datos de control no está disponible.");
      }
    } catch (error) {
      if (error instanceof Error && error.message.startsWith("El núcleo arrancó")) throw error;
      // Core not listening yet; retry with backoff (report the first failure only).
      if (!reported) {
        reported = true;
        reportClientError(`health gate: ${error instanceof Error ? error.message : String(error)}`);
      }
    }
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(delay * 1.6, 2_000);
  }
  throw new Error("El núcleo local no respondió a tiempo (90 s).");
}

function trace(step: string) {
  if (import.meta.env.DEV) void invoke("client_log", { level: "debug", message: `boot: ${step}` }).catch(() => undefined);
}
