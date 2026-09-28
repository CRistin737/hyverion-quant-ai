import { ApiError, type ApiClient } from "../client";
import type { EngineStatus, Snapshot } from "../types";
import {
  demoAiModels,
  demoAiUsage,
  demoCandidates,
  demoConflicts,
  demoKnowledge,
  demoMemory,
  demoReadiness,
  demoSetup,
  demoSnapshot,
  demoVault,
  demoVaultNotes,
  firstRunSetup,
} from "./demo";
import { demoDailyReport, demoIntelligence, demoLiveReadiness, demoPeriodReport, demoSources } from "./intelligence";
import { createLearningDemo, demoCandles, demoMarketSession, demoOrders, demoRejected, demoTrades } from "./extra";
import type { ChartInterval } from "../types";

/** Fixture names: `demo` (live-looking account), `first-run`, `empty`, `offline`. */
export type FixtureName = "demo" | "first-run" | "empty" | "offline";

function emptySnapshot(): Snapshot {
  return {
    ...demoSnapshot,
    markets: demoSnapshot.markets.map((m) => ({ ...m, history: [], candles: [] })),
    positions: [],
    orders: [],
    fills: [],
    operations: [],
    unresolved_operations: 0,
    alerts: [],
    proposals: [],
    shadow_trades: [],
    experiments: [],
    decision_trace: [],
    pnl_history: [],
    protection_recovery: { ...demoSnapshot.protection_recovery, open_positions: 0, protected_positions: 0 },
    pnl: {
      ...demoSnapshot.pnl,
      equity: "10000",
      cash_equity: "10000",
      marked_equity: "10000",
      account_high_water_mark: "10000",
      realized_net_pnl: "0",
      unrealized_pnl: "0",
      peak_realized_pnl: "0",
      peak_total_pnl: "0",
      fees: "0",
    },
    learning_metrics: { ...demoSnapshot.learning_metrics, sample_size: 0, wins: 0, losses: 0 },
  };
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function createFixtureClient(name: string): ApiClient {
  const fixture = (["demo", "first-run", "empty", "offline"].includes(name) ? name : "demo") as FixtureName;
  const snapshot = fixture === "demo" ? demoSnapshot : emptySnapshot();
  // Engine state is mutable so the start/stop control is demonstrable offline.
  let engine: EngineStatus =
    fixture === "demo"
      ? demoSnapshot.engine
      : { state: "stopped", pid: null, started_at: null, stopped_at: null, exit_code: null, interval_seconds: null, mode: "PAPER" };

  const createdProposals: Array<Record<string, unknown>> = [];
  let service = { installed: false, loaded: false, pid: null as number | null, plist_path: "~/Library/LaunchAgents/com.hyverion.engine.plist", log_path: "~/Library/Application Support/Hyverion Quant AI/logs/engine.log", detail: "" };
  const learning = createLearningDemo(demoSnapshot.agents.map((agent) => agent.agent_id));
  const demo = fixture === "demo";
  const lastPrice = (symbol: string) => Number(snapshot.markets.find((m) => m.symbol === symbol)?.last ?? 100);
  // Demo login: waiting for ~4 s, then connected.
  let loginStarted: number | null = null;
  const loginSession = (providerId: string, cancelled = false) => {
    const elapsed = loginStarted === null ? null : Date.now() - loginStarted;
    const state = cancelled ? "failed" : elapsed === null ? "idle" : elapsed < 4000 ? "waiting_browser" : "connected";
    return {
      provider_id: providerId,
      state,
      url: state === "waiting_browser" ? "https://claude.ai/oauth/authorize?demo=1" : null,
      detail: cancelled ? "cancelled" : "",
      manual_command: null,
      started_at: new Date().toISOString(),
      finished_at: state === "connected" || cancelled ? new Date().toISOString() : null,
      timeout_seconds: 300,
    };
  };

  const routes: Record<string, () => unknown> = {
    "/api/v1/snapshot": () => ({ ...snapshot, engine, generated_at: new Date().toISOString() }),
    "/api/v1/engine": () => engine,
    "/api/v1/setup/status": () => (fixture === "first-run" ? firstRunSetup : demoSetup),
    "/api/v1/config/public": () => snapshot.config,
    "/api/v1/readiness": () => demoReadiness,
    "/api/v1/market/session": () => demoMarketSession,
    "/api/v1/intelligence": () => demoIntelligence,
    "/api/v1/sources/status": () => demoSources,
    "/api/v1/ai/models": () => demoAiModels,
    "/api/v1/ai/usage": () => demoAiUsage,
    "/api/v1/service": () => service,
    "/api/v1/reports/period": () => (demo ? demoPeriodReport : { ...demoPeriodReport, trades: 0, wins: 0, losses: 0, net_pnl_usd: "0", days: [], trade_rows: [], win_rate_percent: null, profit_factor: null, best_trade_usd: null, worst_trade_usd: null }),
    "/api/v1/reports/trades-export": () => ({ filename: "hyverion-operaciones-demo.csv", csv: "position_id,asset,net_pnl_usd\ndemo,QQQ,412.50\n" }),
    "/api/v1/reports/daily": () => demoDailyReport,
    "/api/v1/reports/live-readiness": () => demoLiveReadiness,
    "/api/v1/broker/status": () => ({
      provider: "alpaca",
      environment: "paper",
      connected: true,
      detail: "Alpaca Paper account active.",
      capabilities: null,
      account: { account_label: "PA…DEMO", currency: "USD", status: "ACTIVE", equity: "100123.37", cash: "98170.40", buying_power: "396400.00" },
      positions: [],
    }),
    "/api/v1/memory": () => demoMemory,
    "/api/v1/memory/knowledge": () => (fixture === "demo" ? demoKnowledge : []),
    "/api/v1/memory/candidates": () => (fixture === "demo" ? demoCandidates : []),
    "/api/v1/memory/conflicts": () => (fixture === "demo" ? demoConflicts : []),
    "/api/v1/learning/proposals": () => [...createdProposals].reverse(),
    "/api/v1/providers": () => snapshot.providers,
    "/api/v1/sources": () => snapshot.sources,
    "/api/v1/agents": () => snapshot.agents,
    "/api/v1/memory/vault": () => demoVault,
    "/api/v1/trades": () => (demo ? demoTrades(snapshot.markets[0]!.last) : []),
    "/api/v1/orders": () => (demo ? demoOrders : []),
    "/api/v1/orders/rejected": () => (demo ? demoRejected : []),
    "/api/v1/components": () => learning.components(),
    "/api/v1/learning/timeline": () => (demo ? [...createdProposals.map((p) => ({ ...p, history: [{ status: "PROPOSED", from_status: null, reason: null, at: p.created_at }], updated_at: p.created_at })).reverse(), ...learning.changes()] : []),
    "/api/v1/learning/optimizer": () => (demo ? learning.optimizer() : { running: false, error: null, last_run: null }),
  };

  return {
    async get<T>(path: string): Promise<T> {
      await wait(120);
      if (fixture === "offline") throw new ApiError(0, "No se pudo contactar al núcleo (sin conexión).", path);
      const eventsMatch = /^\/api\/v1\/operations\/([^/]+)\/events$/.exec(path);
      if (eventsMatch) {
        const op = demoSnapshot.operations[1]!;
        const event = (sequence: number, from: string | null, to: string, reason: string, at: string) => ({
          id: `ev-${sequence}`, operation_id: eventsMatch[1], sequence, from_state: from, to_state: to,
          requested_state: to, legal: true, reason, details: {}, occurred_at: at,
        });
        return [
          event(1, null, "PROPOSED", "proposal_created", op.created_at),
          event(2, "PROPOSED", "RISK_APPROVED", "risk_allow", op.created_at),
          event(3, "RISK_APPROVED", "SUBMITTED", "execution_submitted", op.created_at),
          event(4, "SUBMITTED", "RECOVERY_REQUIRED", "venue_ack_missing_after_restart", op.updated_at),
        ] as T;
      }
      if (path.startsWith("/api/v1/memory/vault/note?")) {
        const notePath = decodeURIComponent(path.split("path=")[1] ?? "");
        const note = demoVaultNotes[notePath];
        if (!note) throw new ApiError(404, "note not found", path);
        return note as T;
      }
      if (path.startsWith("/api/v1/market/candles?")) {
        const query = new URLSearchParams(path.split("?")[1]);
        const symbol = query.get("symbol") ?? "QQQ";
        return demoCandles(symbol, (query.get("interval") ?? "1m") as ChartInterval, Number(query.get("limit") ?? 120), lastPrice(symbol)) as T;
      }
      if (path.startsWith("/api/v1/trades?")) {
        const view = new URLSearchParams(path.split("?")[1]).get("view");
        const rows = demo ? demoTrades(snapshot.markets[0]!.last) : [];
        return (view ? rows.filter((row) => row.view === view) : rows) as T;
      }
      const historyMatch = /^\/api\/v1\/components\/([^/]+)\/history$/.exec(path);
      if (historyMatch) {
        const history = learning.history(decodeURIComponent(historyMatch[1]!));
        if (!history) throw new ApiError(404, "component_not_found", path);
        return history as T;
      }
      const specMatch = /^\/api\/v1\/agents\/([^/]+)\/spec$/.exec(path);
      if (specMatch) {
        const id = decodeURIComponent(specMatch[1]!);
        const agent = demoSnapshot.agents.find((item) => item.agent_id === id);
        if (!agent) throw new ApiError(404, "agent_not_found", path);
        return {
          agent_id: id,
          version: agent.version,
          spec_markdown: `# ${agent.role}\n\n## ROLE\n${agent.role}\n\n## OBJECTIVE\n${agent.objective ?? ""}\n\n## PROHIBITED ACTIONS\nNo trading, no credentials, no production mutation.\n\n## VERSION\n${agent.version}`,
        } as T;
      }
      const loginMatch = /^\/api\/v1\/providers\/([^/]+)\/login$/.exec(path);
      if (loginMatch) return loginSession(loginMatch[1]!) as T;
      const route = routes[path.split("?")[0] ?? path];
      if (!route) throw new ApiError(404, "Recurso no disponible en el modo demostración.", path);
      return route() as T;
    },
    async post<T>(path: string, body?: unknown): Promise<T> {
      await wait(450);
      if (fixture === "offline") throw new ApiError(0, "No se pudo contactar al núcleo (sin conexión).", path);
      if (path === "/api/v1/config/validate") {
        const patch = (body ?? {}) as Record<string, unknown>;
        const errors: string[] = [];
        return { valid: errors.length === 0, errors, changed_fields: Object.keys(patch).sort() } as T;
      }
      if (path === "/api/v1/config/apply") {
        return { applied: true, requires_restart: false, changed_fields: Object.keys((body ?? {}) as object), backup_path: null, detail: "applied" } as T;
      }
      if (path === "/api/v1/memory/vault/note/owner") {
        const input = body as { path: string; notes: string };
        const note = demoVaultNotes[input.path];
        if (!note) throw new ApiError(422, "note not found", path);
        demoVaultNotes[input.path] = { ...note, owner_notes: input.notes.trim() };
        return demoVaultNotes[input.path] as T;
      }
      const switchPost = /^\/api\/v1\/providers\/([^/]+)\/switch-account$/.exec(path);
      if (switchPost) {
        loginStarted = Date.now();
        return { signed_out: true, login: loginSession(switchPost[1]!) } as T;
      }
      const loginPost = /^\/api\/v1\/providers\/([^/]+)\/login(\/cancel)?$/.exec(path);
      if (loginPost) {
        loginStarted = loginPost[2] ? null : Date.now();
        return loginSession(loginPost[1]!, Boolean(loginPost[2])) as T;
      }
      const applyMatch = /^\/api\/v1\/learning\/proposals\/([^/]+)\/apply$/.exec(path);
      if (applyMatch) {
        const result = learning.apply(decodeURIComponent(applyMatch[1]!), (body as { reason: string }).reason);
        if (!result) throw new ApiError(422, "invalid_state", path);
        return result as T;
      }
      const transitionMatch = /^\/api\/v1\/learning\/proposals\/([^/]+)\/transition$/.exec(path);
      if (transitionMatch) {
        const input = body as { target: string; reason: string };
        const id = decodeURIComponent(transitionMatch[1]!);
        const created = createdProposals.find((item) => item.id === id);
        if (created) {
          created.status = input.target;
          return created as T;
        }
        if (input.target === "REJECTED") return learning.reject(id, input.reason) as T;
        throw new ApiError(422, "invalid_state", path);
      }
      const rollbackMatch = /^\/api\/v1\/learning\/components\/([^/]+)\/rollback$/.exec(path);
      if (rollbackMatch) {
        const result = learning.rollback(decodeURIComponent(rollbackMatch[1]!), (body as { reason: string }).reason);
        if (!result) throw new ApiError(422, "nothing_to_undo", path);
        return result as T;
      }
      if (path === "/api/v1/learning/optimizer/run") return { started: true } as T;
      if (path === "/api/v1/learning/proposals") {
        const proposal = { ...(body as Record<string, unknown>), id: `demo-${createdProposals.length + 1}`, status: "PROPOSED", created_at: new Date().toISOString() };
        createdProposals.push(proposal);
        return proposal as T;
      }
      if (path === "/api/v1/intelligence/refresh") return demoIntelligence as T;
      if (path.startsWith("/api/v1/sources/") && path.endsWith("/test")) {
        const id = path.split("/")[4];
        return (demoSources.find((item) => item.id === id) ?? demoSources[0]) as T;
      }
      if (path === "/api/v1/broker/reconcile") return { status: "OK", safe_mode: false, mismatches: [], account: snapshot.pnl.broker_account } as T;
      if (path === "/api/v1/engine/start") {
        if (fixture === "first-run") throw new ApiError(409, "broker_not_connected", path);
        const interval = (body as { interval_seconds?: number } | undefined)?.interval_seconds ?? 60;
        engine = { state: "running", pid: 4242, started_at: new Date().toISOString(), stopped_at: null, exit_code: null, interval_seconds: interval, mode: "PAPER" };
        return engine as T;
      }
      if (path === "/api/v1/engine/flatten" || path === "/api/v1/engine/flatten/cancel") {
        engine = { ...engine, flatten_pending: path === "/api/v1/engine/flatten" };
        return engine as T;
      }
      if (path === "/api/v1/engine/stop") {
        engine = { ...engine, state: "stopped", pid: null, stopped_at: new Date().toISOString() };
        return engine as T;
      }
      if (path === "/api/v1/service/install" || path === "/api/v1/service/uninstall") {
        const on = path.endsWith("/install");
        service = { ...service, installed: on, loaded: on, pid: on ? 5151 : null };
        return service as T;
      }
      if (path === "/api/v1/system/backup") {
        return { status: "BACKUP_VERIFIED", filename: "trading_bot-demo.db", size_bytes: 1_204_224, verified: true } as T;
      }
      return { status: "OK" } as T;
    },
  };
}
