import type { ReactNode } from "react";

import { ApiError } from "@/api/client";
import { useSnapshot } from "@/api/provider";
import type { Snapshot } from "@/api/types";
import { ErrorState, SkeletonRows } from "@/ui/feedback";

/** Renders snapshot-driven content with consistent loading and error states. */
export function SnapshotGate({ children }: { children: (snapshot: Snapshot) => ReactNode }) {
  const { data, error, isLoading, refetch, isFetching } = useSnapshot();
  if (data) return <>{children(data)}</>;
  if (isLoading) return <SkeletonRows rows={8} />;
  const offline = error instanceof ApiError && error.offline;
  return (
    <ErrorState
      title={offline ? "El núcleo local no responde" : "No se pudo cargar el estado del sistema"}
      impact="Sin estado actual no se muestran posiciones ni decisiones. El motor mantiene su protección determinista de forma independiente."
      detail={error instanceof Error ? error.message : undefined}
      onRetry={() => void refetch()}
      retrying={isFetching}
    />
  );
}

export function useTab<T extends string>(value: string | undefined, allowed: readonly T[], fallback: T): T {
  return (allowed as readonly string[]).includes(value ?? "") ? (value as T) : fallback;
}
