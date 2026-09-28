import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { SNAPSHOT_KEY, useApi } from "./provider";
import type { AgentSpec, AiModels, AiUsage, PeriodReport, ServiceStatus, BrokerAccountSync, BrokerStatus, Candle, ChangeItem, ChartInterval, ComponentVersion, ConfigApplyResult, ConfigPatch, ConfigValidation, DailyReport, DataSourceStatus, EngineStatus, Intelligence, KnowledgeItem, LiveReadiness, LoginSession, MarketSession, MemoryCandidate, MemoryConflict, MemoryStatus, OperationEvent, OptimizerStatus, OrderRow, Readiness, RejectedEntry, ResolutionTarget, SetupStatus, Trade, TradeView, VaultNote, VaultOverview, VersionEntry } from "./types";

export function useSetupStatus() {
  const { client } = useApi();
  return useQuery({ queryKey: ["setup"], queryFn: () => client.get<SetupStatus>("/api/v1/setup/status") });
}

export function useReadiness() {
  const { client } = useApi();
  return useQuery({ queryKey: ["readiness"], queryFn: () => client.get<Readiness>("/api/v1/readiness"), staleTime: 30_000 });
}

export function useMemoryStatus() {
  const { client } = useApi();
  return useQuery({ queryKey: ["memory"], queryFn: () => client.get<MemoryStatus>("/api/v1/memory"), staleTime: 30_000 });
}

export function useKnowledge(filters: { status?: string; symbol?: string; text?: string }) {
  const { client } = useApi();
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.symbol) params.set("symbol", filters.symbol);
  if (filters.text) params.set("text", filters.text);
  const qs = params.toString();
  return useQuery({
    queryKey: ["knowledge", qs],
    queryFn: () => client.get<KnowledgeItem[]>(`/api/v1/memory/knowledge${qs ? `?${qs}` : ""}`),
  });
}

export function useMemoryCandidates() {
  const { client } = useApi();
  return useQuery({ queryKey: ["memory-candidates"], queryFn: () => client.get<MemoryCandidate[]>("/api/v1/memory/candidates"), staleTime: 30_000 });
}

export function useMemoryConflicts() {
  const { client } = useApi();
  return useQuery({ queryKey: ["memory-conflicts"], queryFn: () => client.get<MemoryConflict[]>("/api/v1/memory/conflicts"), staleTime: 30_000 });
}

export function useOperationEvents(operationId: string | null) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["operation-events", operationId],
    queryFn: () => client.get<OperationEvent[]>(`/api/v1/operations/${encodeURIComponent(operationId ?? "")}/events`),
    enabled: Boolean(operationId),
  });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return (...keys: string[]) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: SNAPSHOT_KEY }),
      ...keys.map((key) => queryClient.invalidateQueries({ queryKey: [key] })),
    ]);
}

export function useValidateConfig() {
  const { client } = useApi();
  return useMutation({
    mutationFn: (patch: ConfigPatch) => client.post<ConfigValidation>("/api/v1/config/validate", patch),
  });
}

/** Always validate → apply: apply is only called after a successful validation. */
export function useApplyConfig() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: async (patch: ConfigPatch) => {
      const validation = await client.post<ConfigValidation>("/api/v1/config/validate", patch);
      if (!validation.valid) return { validation, result: null };
      const result = await client.post<ConfigApplyResult>("/api/v1/config/apply", patch);
      return { validation, result };
    },
    onSuccess: () => invalidate("setup", "readiness"),
  });
}

export function useResolveOperation() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { operationId: string; target: ResolutionTarget; reason: string }) =>
      client.post<{ operation_id: string; state: string }>(
        `/api/v1/operations/${encodeURIComponent(input.operationId)}/resolve`,
        { target: input.target, reason: input.reason },
      ),
    onSuccess: () => invalidate("operation-events"),
  });
}

export function useStartEngine() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (intervalSeconds: number = 60) =>
      client.post<EngineStatus>("/api/v1/engine/start", { interval_seconds: intervalSeconds }),
    onSuccess: () => invalidate(),
  });
}

export function useStopEngine() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: () => client.post<EngineStatus>("/api/v1/engine/stop"),
    onSuccess: () => invalidate(),
  });
}

export function useFlatten() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (cancel: boolean = false) => client.post<EngineStatus>(cancel ? "/api/v1/engine/flatten/cancel" : "/api/v1/engine/flatten"),
    onSuccess: () => invalidate(),
  });
}

export function useBackup() {
  const { client } = useApi();
  return useMutation({
    mutationFn: () => client.post<{ status: string; filename: string; size_bytes: number; verified: boolean }>("/api/v1/system/backup"),
  });
}

export function useMemoryCycle() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: () => client.post<Record<string, unknown>>("/api/v1/memory/cycle"),
    onSuccess: () => invalidate("memory", "knowledge", "memory-candidates", "memory-conflicts"),
  });
}

export function useProviderAction() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { providerId: string; action: "login" | "test" | "subscription" }) =>
      client.post<Record<string, unknown>>(`/api/v1/providers/${encodeURIComponent(input.providerId)}/${input.action}`),
    onSuccess: () => invalidate(),
  });
}

const LOGIN_ACTIVE = new Set(["starting", "waiting_browser", "verifying"]);

/** Official subscription login: start it, then poll progress until it settles. */
export function useProviderLogin(providerId: string) {
  const { client } = useApi();
  const invalidate = useInvalidate();
  const queryClient = useQueryClient();
  const key = ["provider-login", providerId];
  const status = useQuery({
    queryKey: key,
    queryFn: () => client.get<LoginSession>(`/api/v1/providers/${encodeURIComponent(providerId)}/login`),
    refetchInterval: (query) => (query.state.data && LOGIN_ACTIVE.has(query.state.data.state) ? 1_500 : false),
  });
  const start = useMutation({
    mutationFn: () => client.post<LoginSession>(`/api/v1/providers/${encodeURIComponent(providerId)}/login`),
    onSuccess: (session) => queryClient.setQueryData(key, session),
  });
  const cancel = useMutation({
    mutationFn: () => client.post<LoginSession>(`/api/v1/providers/${encodeURIComponent(providerId)}/login/cancel`),
    onSuccess: (session) => queryClient.setQueryData(key, session),
  });
  const connected = status.data?.state === "connected";
  useEffect(() => {
    if (connected) void invalidate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);
  return { session: status.data, start, cancel, active: Boolean(status.data && LOGIN_ACTIVE.has(status.data.state)) };
}

export function useReconcileBroker() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: () =>
      client.post<{ status: string; safe_mode: boolean; detail?: string; mismatches?: string[]; account?: BrokerAccountSync | null }>("/api/v1/broker/reconcile"),
    onSuccess: () => invalidate(),
  });
}

export function useVault() {
  const { client } = useApi();
  return useQuery({ queryKey: ["vault"], queryFn: () => client.get<VaultOverview>("/api/v1/memory/vault") });
}

export function useVaultNote(path: string | null) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["vault-note", path],
    queryFn: () => client.get<VaultNote>(`/api/v1/memory/vault/note?path=${encodeURIComponent(path ?? "")}`),
    enabled: Boolean(path),
  });
}

export function useSaveOwnerNotes() {
  const { client } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { path: string; notes: string }) => client.post<VaultNote>("/api/v1/memory/vault/note/owner", input),
    onSuccess: (note) => {
      queryClient.setQueryData(["vault-note", note.path], note);
      void queryClient.invalidateQueries({ queryKey: ["vault"] });
    },
  });
}

export function useChainProbe() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: () => client.post<Record<string, unknown>>("/api/v1/providers/chain/probe"),
    onSuccess: () => invalidate(),
  });
}

export interface PlanAudit {
  overall: string;
  requirements_total: number;
  status_counts: Record<string, number>;
  evidence_missing_count: number;
  agent_specs_total: number;
  agent_specs_valid: number;
  items: Array<{ requirement_id: number; requirement: string; status: string; missing_evidence: string[]; remaining_gate: string }>;
}

export function usePlanAudit(enabled: boolean) {
  const { client } = useApi();
  return useQuery({ queryKey: ["plan-audit"], queryFn: () => client.get<PlanAudit>("/api/v1/plan-audit"), enabled, staleTime: 60_000 });
}

export interface RetentionReport {
  status: string;
  eligible_rows: number;
  deleted_rows: number;
  tables: Record<string, { cutoff: string; eligible_rows: number; deleted_rows: number }>;
}

export function useRetention(enabled: boolean) {
  const { client } = useApi();
  return useQuery({ queryKey: ["retention"], queryFn: () => client.get<RetentionReport>("/api/v1/observability/retention"), enabled, staleTime: 60_000 });
}

export function useMetricsText() {
  const { client } = useApi();
  return useMutation({ mutationFn: () => client.get<string>("/api/v1/observability/metrics") });
}

/** Public OHLC candles for charts; refreshed every 15 s (the core caches as long). */
export function useBrokerStatus() {
  const { client } = useApi();
  return useQuery({
    queryKey: ["broker-status"],
    queryFn: () => client.get<BrokerStatus>("/api/v1/broker/status"),
    staleTime: 30_000,
  });
}

export function useMarketSession() {
  const { client } = useApi();
  return useQuery({
    queryKey: ["market-session"],
    queryFn: () => client.get<MarketSession>("/api/v1/market/session"),
    refetchInterval: 60_000,
    staleTime: 30_000,
  });
}

export function useCandles(symbol: string | undefined, interval: ChartInterval, limit = 120) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["candles", symbol, interval, limit],
    queryFn: () =>
      client.get<Candle[]>(
        `/api/v1/market/candles?symbol=${encodeURIComponent(symbol ?? "")}&interval=${interval}&limit=${limit}`,
      ),
    enabled: Boolean(symbol),
    refetchInterval: 15_000,
    staleTime: 10_000,
    placeholderData: (previous) => previous,
  });
}

export function useTrades(view?: TradeView) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["trades", view ?? "all"],
    queryFn: () => client.get<Trade[]>(`/api/v1/trades${view ? `?view=${view}` : ""}`),
    refetchInterval: 5_000,
  });
}

export function useOrders() {
  const { client } = useApi();
  return useQuery({ queryKey: ["orders"], queryFn: () => client.get<OrderRow[]>("/api/v1/orders"), refetchInterval: 5_000 });
}

export function useRejectedEntries() {
  const { client } = useApi();
  return useQuery({ queryKey: ["orders-rejected"], queryFn: () => client.get<RejectedEntry[]>("/api/v1/orders/rejected"), refetchInterval: 10_000 });
}

export function useComponents() {
  const { client } = useApi();
  return useQuery({ queryKey: ["components"], queryFn: () => client.get<ComponentVersion[]>("/api/v1/components") });
}

export function useComponentHistory(componentId: string | null) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["component-history", componentId],
    queryFn: () => client.get<VersionEntry[]>(`/api/v1/components/${encodeURIComponent(componentId ?? "")}/history`),
    enabled: Boolean(componentId),
  });
}

export function useAgentSpec(agentId: string | null) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["agent-spec", agentId],
    queryFn: () => client.get<AgentSpec>(`/api/v1/agents/${encodeURIComponent(agentId ?? "")}/spec`),
    enabled: Boolean(agentId),
  });
}

export function useChanges() {
  const { client } = useApi();
  return useQuery({ queryKey: ["changes"], queryFn: () => client.get<ChangeItem[]>("/api/v1/learning/timeline") });
}

const CHANGE_KEYS = ["changes", "components", "component-history"];

export function useTransitionChange() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { id: string; target: "TESTING" | "READY_FOR_REVIEW" | "APPROVED" | "REJECTED"; reason: string }) =>
      client.post<ChangeItem>(`/api/v1/learning/proposals/${encodeURIComponent(input.id)}/transition`, {
        target: input.target,
        reason: input.reason,
      }),
    onSuccess: () => invalidate(...CHANGE_KEYS),
  });
}

/** Owner approval: records APPROVED/DEPLOYED and activates the new version. */
export function useApplyChange() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { id: string; reason: string }) =>
      client.post<{ proposal_id: string; status: string; component_id: string; version: string }>(
        `/api/v1/learning/proposals/${encodeURIComponent(input.id)}/apply`,
        { reason: input.reason },
      ),
    onSuccess: () => invalidate(...CHANGE_KEYS),
  });
}

export function useRollbackComponent() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (input: { componentId: string; reason: string }) =>
      client.post<{ component_id: string; undone_version: string; active_version: string }>(
        `/api/v1/learning/components/${encodeURIComponent(input.componentId)}/rollback`,
        { reason: input.reason },
      ),
    onSuccess: () => invalidate(...CHANGE_KEYS),
  });
}

export function useOptimizer() {
  const { client } = useApi();
  const invalidate = useInvalidate();
  const status = useQuery({
    queryKey: ["optimizer"],
    queryFn: () => client.get<OptimizerStatus>("/api/v1/learning/optimizer"),
    refetchInterval: (query) => (query.state.data?.running ? 2_000 : false),
  });
  const run = useMutation({
    mutationFn: () => client.post<{ started: boolean }>("/api/v1/learning/optimizer/run"),
    onSuccess: () => invalidate("optimizer"),
  });
  const running = Boolean(status.data?.running);
  useEffect(() => {
    if (!running) void invalidate(...CHANGE_KEYS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);
  return { status, run };
}

export function useSourcesStatus() {
  const { client } = useApi();
  return useQuery({ queryKey: ["sources-status"], queryFn: () => client.get<DataSourceStatus[]>("/api/v1/sources/status"), staleTime: 30_000 });
}

export function useTestSource() {
  const { client } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: string) => client.post<DataSourceStatus>(`/api/v1/sources/${sourceId}/test`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["sources-status"] });
      await queryClient.invalidateQueries({ queryKey: ["intelligence"] });
    },
  });
}

export function useIntelligence() {
  const { client } = useApi();
  return useQuery({ queryKey: ["intelligence"], queryFn: () => client.get<Intelligence>("/api/v1/intelligence"), refetchInterval: 60_000, staleTime: 30_000 });
}

export function useRefreshIntelligence() {
  const { client } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => client.post<Intelligence>("/api/v1/intelligence/refresh"),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["intelligence"] });
      await queryClient.invalidateQueries({ queryKey: ["sources-status"] });
    },
  });
}

export function useDailyReport() {
  const { client } = useApi();
  return useQuery({ queryKey: ["report-daily"], queryFn: () => client.get<DailyReport>("/api/v1/reports/daily"), staleTime: 60_000 });
}

export function useLiveReadiness() {
  const { client } = useApi();
  return useQuery({ queryKey: ["report-live-readiness"], queryFn: () => client.get<LiveReadiness>("/api/v1/reports/live-readiness"), staleTime: 60_000 });
}

/** Model per role and what can be chosen for the primary subscription. */
export function useAiModels() {
  const { client } = useApi();
  return useQuery({ queryKey: ["ai-models"], queryFn: () => client.get<AiModels>("/api/v1/ai/models"), staleTime: 30_000 });
}

/** 5-hour and weekly limits of each subscription (cached ~5 min by the core). */
export function useAiUsage() {
  const { client } = useApi();
  return useQuery({ queryKey: ["ai-usage"], queryFn: () => client.get<AiUsage>("/api/v1/ai/usage"), refetchInterval: 60_000, staleTime: 30_000 });
}

export function useRefreshAiUsage() {
  const { client } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => client.get<AiUsage>("/api/v1/ai/usage?refresh=true"),
    onSuccess: (data) => queryClient.setQueryData(["ai-usage"], data),
  });
}

/** Save the model of one or more roles; the engine uses it from its next cycle. */
export function useSaveAiModels() {
  const apply = useApplyConfig();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (models: Partial<Record<"analysis" | "decision" | "improvement", string>>) => {
      const patch: ConfigPatch = {};
      if (models.analysis) patch.model_analysis = models.analysis;
      if (models.decision) patch.model_decision = models.decision;
      if (models.improvement) patch.model_improvement = models.improvement;
      const outcome = await apply.mutateAsync(patch);
      if (!outcome.validation.valid) throw new Error(outcome.validation.errors.join("; "));
      return outcome;
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ai-models"] }),
  });
}

/** Sign out of a subscription CLI and start the official login again. */
export function useSwitchAccount(providerId: string) {
  const { client } = useApi();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => client.post<{ signed_out: boolean; login: LoginSession }>(`/api/v1/providers/${encodeURIComponent(providerId)}/switch-account`),
    onSuccess: (result) => {
      queryClient.setQueryData(["provider-login", providerId], result.login);
      void queryClient.invalidateQueries({ queryKey: ["ai-usage"] });
    },
  });
}

/** Background engine (runs with the app closed). */
export function useServiceStatus() {
  const { client } = useApi();
  return useQuery({ queryKey: ["service"], queryFn: () => client.get<ServiceStatus>("/api/v1/service"), staleTime: 15_000 });
}

export function useSetService() {
  const { client } = useApi();
  const queryClient = useQueryClient();
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (install: boolean) => client.post<ServiceStatus>(`/api/v1/service/${install ? "install" : "uninstall"}`),
    onSuccess: (status) => {
      queryClient.setQueryData(["service"], status);
      invalidate();
    },
  });
}

function periodQuery(start: string | null, end: string | null): string {
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** KPIs for a day, month, year or everything (New York dates, end inclusive). */
export function usePeriodReport(start: string | null, end: string | null) {
  const { client } = useApi();
  return useQuery({
    queryKey: ["period-report", start, end],
    queryFn: () => client.get<PeriodReport>(`/api/v1/reports/period${periodQuery(start, end)}`),
    staleTime: 60_000,
  });
}

/** Closed trades of the period as CSV text. */
export function useExportTrades() {
  const { client } = useApi();
  return useMutation({
    mutationFn: (input: { start: string | null; end: string | null }) =>
      client.get<{ filename: string; csv: string }>(`/api/v1/reports/trades-export${periodQuery(input.start, input.end)}`),
  });
}
