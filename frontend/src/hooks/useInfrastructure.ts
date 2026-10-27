"use client";

import { useCallback } from "react";
import { fetchInfrastructure } from "@/lib/api";
import {
  INFRASTRUCTURE_FETCH_OFFSET_MS,
  INFRASTRUCTURE_REFRESH_MS,
} from "@/lib/config";
import {
  usePollingResource,
  type UsePollingResourceResult,
} from "@/hooks/usePollingResource";
import type { InfrastructureResponse } from "@/lib/types";

export type UseInfrastructureResult =
  UsePollingResourceResult<InfrastructureResponse>;

export function useInfrastructure(
  refreshMs: number = INFRASTRUCTURE_REFRESH_MS,
): UseInfrastructureResult {
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchInfrastructure(signal),
    [],
  );
  return usePollingResource({
    fetcher,
    refreshMs,
    initialDelayMs: INFRASTRUCTURE_FETCH_OFFSET_MS,
  });
}
