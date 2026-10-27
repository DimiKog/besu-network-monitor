import { formatRelativeAge, formatUpdatedAt } from "@/lib/format";
import { observabilityNote, presentHealth } from "@/lib/health";
import type { ObservabilityState, PublicHealth } from "@/lib/types";

interface NetworkHealthBannerProps {
  health: PublicHealth;
  observability: ObservabilityState;
  updatedAt: string;
  nowMs?: number;
}

export function NetworkHealthBanner({
  health,
  observability,
  updatedAt,
  nowMs,
}: NetworkHealthBannerProps) {
  const presentation = presentHealth(health);
  const note = observabilityNote(observability);
  const description =
    health === "healthy" && observability === "partial"
      ? "Live chain is healthy; some domain telemetry is unavailable"
      : presentation.description;

  return (
    <section
      className={`health-banner health-banner--${presentation.tone}`}
      aria-labelledby="network-health-heading"
    >
      <div className="health-banner__main">
        <div className="health-banner__status-row">
          <span
            className={`health-banner__icon health-banner__icon--${presentation.tone}`}
            aria-hidden="true"
          >
            {health === "healthy" ? "✓" : health === "unknown" ? "?" : "!"}
          </span>
          <div>
            <p className="health-banner__eyebrow">Overall network health</p>
            <h2 id="network-health-heading" className="health-banner__title">
              {presentation.label}
            </h2>
            <p className="health-banner__description">{description}</p>
          </div>
        </div>

        <div className="health-banner__aside">
          <p className="health-banner__freshness">
            <span className="health-banner__freshness-label">Last update</span>
            <span className="health-banner__freshness-value mono">
              {formatUpdatedAt(updatedAt)}
            </span>
            <span className="health-banner__freshness-relative">
              {formatRelativeAge(updatedAt, nowMs)}
            </span>
          </p>
        </div>
      </div>

      {note ? (
        <p className="health-banner__observability" role="status">
          <span className="health-banner__observability-label">
            Observability: Partial
          </span>
          <span>{note}</span>
        </p>
      ) : (
        <p className="health-banner__observability health-banner__observability--quiet">
          <span className="health-banner__observability-label">
            Observability: Complete
          </span>
          <span>All monitored domains reporting</span>
        </p>
      )}
    </section>
  );
}
