import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { usePollingResource } from "@/hooks/usePollingResource";
import { MonitorFetchError } from "@/lib/api";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("usePollingResource", () => {
  it("keeps last good data when a later refresh fails", async () => {
    let calls = 0;
    const fetcher = vi.fn(async () => {
      calls += 1;
      if (calls === 1) {
        return { value: "ok" };
      }
      throw new MonitorFetchError("boom");
    });

    const { result } = renderHook(() =>
      usePollingResource({
        fetcher,
        refreshMs: 0,
        initialDelayMs: 0,
      }),
    );

    await waitFor(() => {
      expect(result.current.status).toBe("success");
    });
    expect(result.current.data).toEqual({ value: "ok" });

    await act(async () => {
      result.current.refresh();
    });

    await waitFor(() => {
      expect(result.current.error).toBe("boom");
    });
    expect(result.current.data).toEqual({ value: "ok" });
    expect(result.current.status).toBe("success");
  });
});
