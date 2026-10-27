"use client";

import { useCallback } from "react";
import { fetchConsensus } from "@/lib/api";
import {
  CONSENSUS_FETCH_OFFSET_MS,
  CONSENSUS_REFRESH_MS,
} from "@/lib/config";
import {
  usePollingResource,
  type UsePollingResourceResult,
} from "@/hooks/usePollingResource";
import type { ConsensusResponse } from "@/lib/types";

export type UseConsensusResult = UsePollingResourceResult<ConsensusResponse>;

export function useConsensus(
  refreshMs: number = CONSENSUS_REFRESH_MS,
): UseConsensusResult {
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchConsensus(signal),
    [],
  );
  return usePollingResource({
    fetcher,
    refreshMs,
    initialDelayMs: CONSENSUS_FETCH_OFFSET_MS,
  });
}
