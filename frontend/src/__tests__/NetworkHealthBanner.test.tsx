import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { NetworkHealthBanner } from "@/components/NetworkHealthBanner";
import { healthyOverview, partialUnknownOverview } from "./fixtures";

afterEach(() => {
  cleanup();
});

describe("NetworkHealthBanner", () => {
  it("renders a healthy overall state from API health", () => {
    const overview = healthyOverview();
    render(
      <NetworkHealthBanner
        health={overview.health}
        observability={overview.observability}
        updatedAt={overview.updated_at}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Network Healthy" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/All monitored domains operational/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Observability: Complete/i)).toBeInTheDocument();
  });

  it("shows partial observability without declaring the chain unhealthy", () => {
    const overview = partialUnknownOverview();
    render(
      <NetworkHealthBanner
        health={overview.health}
        observability={overview.observability}
        updatedAt={overview.updated_at}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Network Healthy" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Observability: Partial/i)).toBeInTheDocument();
    expect(screen.getByText(/Partial observability/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/All monitored domains operational/i),
    ).not.toBeInTheDocument();
  });

  it("renders critical and unknown labels from backend state", () => {
    const { rerender } = render(
      <NetworkHealthBanner
        health="critical"
        observability="complete"
        updatedAt="2026-10-27T08:38:11.490664Z"
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Network Critical" }),
    ).toBeInTheDocument();

    rerender(
      <NetworkHealthBanner
        health="unknown"
        observability="partial"
        updatedAt="2026-10-27T08:38:11.490664Z"
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Network Status Unknown" }),
    ).toBeInTheDocument();
  });
});
