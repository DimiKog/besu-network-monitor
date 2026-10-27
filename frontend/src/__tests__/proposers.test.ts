import { describe, expect, it } from "vitest";
import { computeProposerDistribution } from "@/lib/proposers";
import { makeRecentBlock } from "./fixtures";

describe("computeProposerDistribution", () => {
  it("aggregates proposers from recent blocks and sorts by count", () => {
    const distribution = computeProposerDistribution([
      makeRecentBlock({
        number: 1,
        proposer: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      }),
      makeRecentBlock({
        number: 2,
        proposer: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      }),
      makeRecentBlock({
        number: 3,
        proposer: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      }),
    ]);

    expect(distribution).toEqual([
      { address: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", count: 2 },
      { address: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", count: 1 },
    ]);
  });

  it("returns an empty list for missing blocks", () => {
    expect(computeProposerDistribution([])).toEqual([]);
    expect(computeProposerDistribution(null)).toEqual([]);
  });
});
