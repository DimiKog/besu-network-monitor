import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChainActivitySection } from "@/components/ChainActivitySection";
import { sampleChainResponse } from "./fixtures";

afterEach(() => {
  cleanup();
});

describe("ChainActivitySection", () => {
  it("renders chain metrics and recent blocks from API data", () => {
    render(
      <ChainActivitySection
        status="success"
        data={sampleChainResponse()}
        error={null}
        onRetry={() => undefined}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Chain Activity" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Current block")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open block 100 in Blockscout" }),
    ).toHaveAttribute("href", "https://blockexplorer.dimikog.org/block/100");
    expect(screen.getByText(/Transactions — last 4 blocks/i)).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: /Block Interval — Last 3 Blocks/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent Blocks" })).toBeInTheDocument();
  });

  it("shows an error state without inventing metrics", () => {
    render(
      <ChainActivitySection
        status="error"
        data={null}
        error="Request failed (500)"
        onRetry={() => undefined}
      />,
    );

    expect(screen.getByText(/Unable to load chain data/i)).toBeInTheDocument();
    expect(screen.queryByText("Current block")).not.toBeInTheDocument();
  });
});
