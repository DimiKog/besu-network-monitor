import { describe, expect, it } from "vitest";
import { observabilityNote, presentHealth } from "@/lib/health";

describe("presentHealth", () => {
  it("maps all four backend health states without reinterpretation", () => {
    expect(presentHealth("healthy").label).toBe("Network Healthy");
    expect(presentHealth("degraded").label).toBe("Network Degraded");
    expect(presentHealth("critical").label).toBe("Network Critical");
    expect(presentHealth("unknown").label).toBe("Network Status Unknown");
  });

  it("keeps short labels for badges", () => {
    expect(presentHealth("healthy").shortLabel).toBe("Healthy");
    expect(presentHealth("degraded").shortLabel).toBe("Degraded");
    expect(presentHealth("critical").shortLabel).toBe("Critical");
    expect(presentHealth("unknown").shortLabel).toBe("Unknown");
  });
});

describe("observabilityNote", () => {
  it("surfaces partial observability without changing health wording", () => {
    expect(observabilityNote("partial")).toMatch(/Partial observability/i);
    expect(observabilityNote("complete")).toBeNull();
  });
});
