import { describe, expect, it } from "vitest";
import { parseOverviewResponse } from "@/lib/api";
import { healthyOverview } from "./fixtures";

describe("parseOverviewResponse", () => {
  it("accepts a valid overview payload", () => {
    const parsed = parseOverviewResponse(healthyOverview());
    expect(parsed.health).toBe("healthy");
    expect(parsed.domains.chain.block_number).toBe(8197534);
  });

  it("rejects an invalid health vocabulary", () => {
    expect(() =>
      parseOverviewResponse({
        ...healthyOverview(),
        health: "ok",
      }),
    ).toThrow(/Invalid health/);
  });
});
