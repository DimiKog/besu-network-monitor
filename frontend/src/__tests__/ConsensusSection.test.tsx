import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ConsensusSection } from "@/components/ConsensusSection";
import { sampleConsensusResponse, sampleRecentBlocks } from "./fixtures";

afterEach(() => {
  cleanup();
});

describe("ConsensusSection", () => {
  it("renders consensus metrics and proposer distribution", () => {
    render(
      <ConsensusSection
        status="success"
        data={sampleConsensusResponse()}
        error={null}
        recentBlocks={sampleRecentBlocks()}
        onRetry={() => undefined}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "QBFT Consensus" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Reference block")).toBeInTheDocument();
    expect(screen.getByText("8,197,711")).toBeInTheDocument();
    expect(screen.getByText("Quorum met")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", {
        name: /Recent Proposer Distribution — Last 4 Blocks/i,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/currently loaded recent block window/i),
    ).toBeInTheDocument();
  });
});
