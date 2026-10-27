import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { InfrastructureSection } from "@/components/InfrastructureSection";
import { sampleInfrastructureResponse } from "./fixtures";

afterEach(() => {
  cleanup();
});

describe("InfrastructureSection", () => {
  it("renders systems, rpc, and service statuses from API booleans", () => {
    render(
      <InfrastructureSection
        status="success"
        data={sampleInfrastructureResponse()}
        error={null}
        onRetry={() => undefined}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Infrastructure" }),
    ).toBeInTheDocument();
    expect(screen.getByText("16 / 16")).toBeInTheDocument();
    expect(screen.getByText("Primary RPC")).toBeInTheDocument();
    expect(screen.getByText("Backup RPC")).toBeInTheDocument();
    expect(screen.getByText("Grafana")).toBeInTheDocument();
    expect(screen.getAllByText("Online").length).toBeGreaterThanOrEqual(8);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/192\.168\./)).not.toBeInTheDocument();
    expect(screen.queryByText(/prometheus/i)).toBeInTheDocument();
  });
});
