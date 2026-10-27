"use client";

import { useCallback } from "react";
import { fetchOverview } from "@/lib/api";
import { OVERVIEW_REFRESH_MS } from "@/lib/config";
import {
  usePollingResource,
  type ResourceStatus,
  type UsePollingResourceResult,
} from "@/hooks/usePollingResource";
import type { OverviewResponse } from "@/lib/types";

export type OverviewStatus = ResourceStatus;

export type UseOverviewResult = UsePollingResourceResult<OverviewResponse>;

export function useOverview(
  refreshMs: number = OVERVIEW_REFRESH_MS,
): UseOverviewResult {
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchOverview(signal),
    [],
  );
  return usePollingResource({ fetcher, refreshMs, initialDelayMs: 0 });
}
