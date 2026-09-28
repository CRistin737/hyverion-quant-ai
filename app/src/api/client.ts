import { invoke, isTauri } from "@tauri-apps/api/core";

import type { Session } from "./session";

/** Forward client-side failures to the desktop shell log (diagnostics only). */
export function reportClientError(message: string) {
  if (isTauri()) void invoke("client_log", { level: "error", message }).catch(() => undefined);
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly path: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }

  /** The core is not reachable (not started, crashed, or port closed). */
  get offline(): boolean {
    return this.status === 0;
  }
}

export interface ApiClient {
  get<T>(path: string): Promise<T>;
  post<T>(path: string, body?: unknown): Promise<T>;
}

function detailOf(payload: unknown, fallback: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    // Structured backend errors carry a stable machine code: { code: "..." }.
    if (detail && typeof detail === "object" && "code" in detail) return String((detail as { code: unknown }).code);
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : String(item)))
        .join("; ");
    }
  }
  return fallback;
}

function detailFrom(status: number, payload: unknown): string {
  return detailOf(payload, `Error ${status}`);
}

/** Desktop transport: the Rust shell performs the request and adds the token. */
export function createTauriClient(): ApiClient {
  async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    let response: { status: number; body: unknown };
    try {
      response = await invoke<{ status: number; body: unknown }>("api_request", { method, path, body: body ?? null });
    } catch (error) {
      const reason = String(error) === "timeout" ? "tiempo agotado" : "sin conexión";
      reportClientError(`api_request ${method} ${path} failed: ${String(error)}`);
      throw new ApiError(0, `No se pudo contactar al núcleo (${reason}).`, path);
    }
    if (response.status < 200 || response.status >= 300) {
      throw new ApiError(response.status, detailFrom(response.status, response.body), path);
    }
    return response.body as T;
  }
  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
  };
}

export function createHttpClient(session: Session, timeoutMs = 20_000): ApiClient {
  async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${session.apiBase}${path}`, {
        method,
        headers: {
          Authorization: `Bearer ${session.token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      reportClientError(`fetch ${method} ${path} failed: ${error instanceof Error ? `${error.name}: ${error.message}` : String(error)}`);
      const reason = error instanceof Error && error.name === "TimeoutError" ? "tiempo agotado" : "sin conexión";
      throw new ApiError(0, `No se pudo contactar al núcleo (${reason}).`, path);
    }
    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }
    if (!response.ok) {
      throw new ApiError(response.status, detailOf(payload, `Error ${response.status}`), path);
    }
    return payload as T;
  }

  return {
    get: (path) => request("GET", path),
    post: (path, body) => request("POST", path, body),
  };
}
