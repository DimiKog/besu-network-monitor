import type { ObservabilityState, PublicHealth } from "./types";

export interface HealthPresentation {
  label: string;
  shortLabel: string;
  description: string;
  /** CSS modifier class suffix matching globals.css */
  tone: "healthy" | "degraded" | "critical" | "unknown";
}

const HEALTH_MAP: Record<PublicHealth, HealthPresentation> = {
  healthy: {
    label: "Network Healthy",
    shortLabel: "Healthy",
    description: "All monitored domains operational",
    tone: "healthy",
  },
  degraded: {
    label: "Network Degraded",
    shortLabel: "Degraded",
    description: "One or more monitored domains need attention",
    tone: "degraded",
  },
  critical: {
    label: "Network Critical",
    shortLabel: "Critical",
    description: "A monitored domain reports a critical condition",
    tone: "critical",
  },
  unknown: {
    label: "Network Status Unknown",
    shortLabel: "Unknown",
    description: "Overall health could not be determined from available telemetry",
    tone: "unknown",
  },
};

export function presentHealth(health: PublicHealth): HealthPresentation {
  return HEALTH_MAP[health];
}

export function presentDomainHealth(health: PublicHealth): string {
  return presentHealth(health).shortLabel;
}

export function observabilityNote(
  observability: ObservabilityState,
): string | null {
  if (observability === "partial") {
    return "Partial observability — some domain telemetry is unavailable";
  }
  return null;
}
