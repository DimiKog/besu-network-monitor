"use client";

import { useCallback } from "react";
import { fetchChain } from "@/lib/api";
import { CHAIN_FETCH_OFFSET_MS, CHAIN_REFRESH_MS } from "@/lib/config";
import {
  usePollingResource,
  type UsePollingResourceResult,
} from "@/hooks/usePollingResource";
import type { ChainResponse } from "@/lib/types";

export type UseChainResult = UsePollingResourceResult<ChainResponse>;

export function useChain(
  refreshMs: number = CHAIN_REFRESH_MS,
): UseChainResult {
  const fetcher = useCallback((signal: AbortSignal) => fetchChain(signal), []);
  return usePollingResource({
    fetcher,
    refreshMs,
    initialDelayMs: CHAIN_FETCH_OFFSET_MS,
  });
}
