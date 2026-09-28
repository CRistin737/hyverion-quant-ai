import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ApiError, createHttpClient, createTauriClient, type ApiClient } from "./client";
import { createFixtureClient } from "./fixtures";
import { loadSession, type Session } from "./session";
import type { Snapshot } from "./types";

export type Connection = "connecting" | "live" | "polling" | "offline";

interface ApiContextValue {
  session: Session;
  client: ApiClient;
  connection: Connection;
  lastMessageAt: number | null;
}

const ApiContext = createContext<ApiContextValue | null>(null);

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
      staleTime: 5_000,
      refetchOnWindowFocus: false,
    },
  },
});

export const SNAPSHOT_KEY = ["snapshot"] as const;
const WS_PROTOCOL = "hyverion.v1";

/**
 * Keeps the snapshot fresh: WebSocket push first, HTTP polling as fallback.
 * The token travels as a WebSocket subprotocol because browsers cannot set an
 * Authorization header on the handshake.
 */
function useSnapshotStream(session: Session, onState: (c: Connection) => void, onMessage: () => void) {
  const client = useQueryClient();
  useEffect(() => {
    if (session.transport !== "http") {
      // Desktop and fixture modes poll through their client; no socket in the webview.
      onState(session.transport === "tauri" ? "live" : "polling");
      return;
    }
    let socket: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      onState(retry === 0 ? "connecting" : "polling");
      const url = `${session.apiBase.replace(/^http/, "ws")}/api/v1/stream`;
      socket = new WebSocket(url, [WS_PROTOCOL, `hyverion.bearer.${session.token}`]);
      socket.onopen = () => {
        retry = 0;
        onState("live");
      };
      socket.onmessage = (event) => {
        try {
          client.setQueryData(SNAPSHOT_KEY, JSON.parse(String(event.data)) as Snapshot);
          onMessage();
        } catch {
          // A malformed frame is ignored; the next frame or poll replaces it.
        }
      };
      socket.onclose = () => {
        if (closed) return;
        onState("polling");
        retry += 1;
        timer = setTimeout(connect, Math.min(15_000, 1_000 * 2 ** Math.min(retry, 4)));
      };
    };
    connect();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [client, session, onState, onMessage]);
}

function ApiBridge({ session, children }: { session: Session; children: ReactNode }) {
  const client = useMemo(
    () =>
      session.transport === "fixture"
        ? createFixtureClient(session.fixture ?? "demo")
        : session.transport === "tauri"
          ? createTauriClient()
          : createHttpClient(session),
    [session],
  );
  const [connection, setConnection] = useState<Connection>("connecting");
  const [lastMessageAt, setLastMessageAt] = useState<number | null>(null);
  const handlers = useRef({
    state: (c: Connection) => setConnection(c),
    message: () => setLastMessageAt(Date.now()),
  });
  useSnapshotStream(session, handlers.current.state, handlers.current.message);

  // Polling fallback whenever the push stream is not live.
  const snapshot = useQuery({
    queryKey: SNAPSHOT_KEY,
    queryFn: () => client.get<Snapshot>("/api/v1/snapshot"),
    refetchInterval:
      session.transport === "tauri" ? 2_000 : connection === "live" ? false : session.transport === "fixture" ? 5_000 : 3_000,
  });

  const failing = snapshot.error instanceof ApiError && snapshot.error.offline && snapshot.failureCount > 0;
  const effectiveConnection: Connection =
    failing && (connection !== "live" || session.transport === "tauri") ? "offline" : connection;

  useEffect(() => {
    if (snapshot.dataUpdatedAt) setLastMessageAt(snapshot.dataUpdatedAt);
  }, [snapshot.dataUpdatedAt]);

  const value = useMemo(
    () => ({ session, client, connection: effectiveConnection, lastMessageAt }),
    [session, client, effectiveConnection, lastMessageAt],
  );
  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}

type SessionState = { status: "loading" } | { status: "ready"; session: Session } | { status: "error"; message: string };

export function ApiProvider({ children, fallback }: { children: ReactNode; fallback: (state: SessionState) => ReactNode }) {
  const [state, setState] = useState<SessionState>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    loadSession()
      .then((session) => alive && setState({ status: "ready", session }))
      .catch((error: unknown) =>
        alive && setState({ status: "error", message: error instanceof Error ? error.message : String(error) }),
      );
    return () => {
      alive = false;
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      {state.status === "ready" ? <ApiBridge session={state.session}>{children}</ApiBridge> : fallback(state)}
    </QueryClientProvider>
  );
}

export function useApi(): ApiContextValue {
  const value = useContext(ApiContext);
  if (!value) throw new Error("useApi must be used inside <ApiProvider>");
  return value;
}

export function useSnapshot() {
  const { client } = useApi();
  return useQuery({
    queryKey: SNAPSHOT_KEY,
    queryFn: () => client.get<Snapshot>("/api/v1/snapshot"),
  });
}
