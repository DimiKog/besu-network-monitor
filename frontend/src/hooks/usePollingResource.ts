"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { MonitorFetchError } from "@/lib/api";

export type ResourceStatus = "loading" | "success" | "error";

export interface UsePollingResourceResult<T> {
  status: ResourceStatus;
  data: T | null;
  error: string | null;
  lastFetchedAt: string | null;
  refresh: () => void;
}

interface UsePollingResourceOptions<T> {
  fetcher: (signal: AbortSignal) => Promise<T>;
  refreshMs: number;
  /** Delay before the first fetch to stagger endpoints. */
  initialDelayMs?: number;
}

export function usePollingResource<T>({
  fetcher,
  refreshMs,
  initialDelayMs = 0,
}: UsePollingResourceOptions<T>): UsePollingResourceResult<T> {
  const [status, setStatus] = useState<ResourceStatus>("loading");
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const hasDataRef = useRef(false);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const refresh = useCallback(() => {
    setTick((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let delayId: number | undefined;

    async function load() {
      try {
        const next = await fetcherRef.current(controller.signal);
        if (cancelled) {
          return;
        }
        hasDataRef.current = true;
        setData(next);
        setError(null);
        setStatus("success");
        setLastFetchedAt(new Date().toISOString());
      } catch (err) {
        if (
          cancelled ||
          (err instanceof DOMException && err.name === "AbortError")
        ) {
          return;
        }
        const message =
          err instanceof MonitorFetchError
            ? err.message
            : err instanceof Error
              ? err.message
              : "Request failed";
        setError(message);
        if (!hasDataRef.current) {
          setStatus("error");
        }
      }
    }

    if (initialDelayMs > 0 && tick === 0 && !hasDataRef.current) {
      delayId = window.setTimeout(() => {
        void load();
      }, initialDelayMs);
    } else {
      void load();
    }

    return () => {
      cancelled = true;
      controller.abort();
      if (delayId !== undefined) {
        window.clearTimeout(delayId);
      }
    };
  }, [tick, initialDelayMs]);

  useEffect(() => {
    if (refreshMs <= 0) {
      return;
    }
    const id = window.setInterval(() => {
      setTick((value) => value + 1);
    }, refreshMs);
    return () => window.clearInterval(id);
  }, [refreshMs]);

  return { status, data, error, lastFetchedAt, refresh };
}
