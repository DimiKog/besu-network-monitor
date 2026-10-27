import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DomainCard } from "@/components/DomainCard";
import { healthyOverview, nullMetricsOverview } from "./fixtures";

describe("DomainCard", () => {
  it("renders successful overview metrics for each domain", () => {
    const overview = healthyOverview();

    const { rerender } = render(
      <DomainCard kind="chain" chain={overview.domains.chain} />,
    );
    expect(screen.getByRole("heading", { name: "Chain" })).toBeInTheDocument();
    expect(screen.getByText("8,197,534")).toBeInTheDocument();
    expect(screen.getByText("5s")).toBeInTheDocument();
    expect(screen.getByLabelText("Health: Healthy")).toBeInTheDocument();

    rerender(
      <DomainCard kind="consensus" consensus={overview.domains.consensus} />,
    );
    expect(screen.getByRole("heading", { name: "Consensus" })).toBeInTheDocument();
    expect(screen.getByText("Quorum met")).toBeInTheDocument();
    expect(screen.getAllByText("7").length).toBeGreaterThanOrEqual(1);

    rerender(
      <DomainCard
        kind="infrastructure"
        infrastructure={overview.domains.infrastructure}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Infrastructure" }),
    ).toBeInTheDocument();
    expect(screen.getByText("16 / 16")).toBeInTheDocument();
    expect(screen.getByText("Fresh")).toBeInTheDocument();
  });

  it("shows placeholders for null metrics instead of inventing values", () => {
    const overview = nullMetricsOverview();
    render(<DomainCard kind="chain" chain={overview.domains.chain} />);

    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
    expect(screen.getByLabelText("Health: Unknown")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
