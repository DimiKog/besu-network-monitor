import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { RecentBlocksTable } from "@/components/RecentBlocksTable";
import { makeRecentBlock } from "./fixtures";

afterEach(() => {
  cleanup();
});

describe("RecentBlocksTable", () => {
  it("renders shortened proposers, null intervals, and expandable rows", async () => {
    const user = userEvent.setup();
    const blocks = Array.from({ length: 12 }, (_, index) =>
      makeRecentBlock({
        number: 200 - index,
        proposer: "0x1234567890abcdef1234567890abcdef12345678",
        interval_seconds: index === 11 ? null : 5,
        age_seconds: index * 5,
      }),
    );

    render(<RecentBlocksTable blocks={blocks} />);

    expect(screen.getAllByText("0x1234…5678").length).toBeGreaterThanOrEqual(1);
    expect(
      screen.getAllByLabelText("0x1234567890abcdef1234567890abcdef12345678")
        .length,
    ).toBeGreaterThanOrEqual(1);
    expect(
      screen.getByRole("link", { name: "Open block 200 in Blockscout" }),
    ).toHaveAttribute("href", "https://blockexplorer.dimikog.org/block/200");
    expect(screen.getAllByRole("row")).toHaveLength(11); // header + 10

    await user.click(screen.getByRole("button", { name: /Show all 12 blocks/i }));
    expect(screen.getAllByRole("row")).toHaveLength(13);
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
  });
});
