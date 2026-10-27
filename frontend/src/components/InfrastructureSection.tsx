"use client";

import { MetricRow } from "@/components/MetricRow";
import { SectionShell } from "@/components/SectionShell";
import type { ResourceStatus } from "@/hooks/usePollingResource";
import {
  formatAgeSeconds,
  formatFreshness,
  formatOnlineStatus,
  formatRelativeAge,
  formatSystemsOperational,
  formatUpdatedAt,
} from "@/lib/format";
import type { InfrastructureResponse } from "@/lib/types";

interface InfrastructureSectionProps {
  status: ResourceStatus;
  data: InfrastructureResponse | null;
  error: string | null;
  nowMs?: number;
  onRetry: () => void;
}

function StatusPill({ online }: { online: boolean | null }) {
  const label = formatOnlineStatus(online);
  const tone =
    online === true ? "online" : online === false ? "offline" : "unknown";
  return (
    <span className={`status-pill status-pill--${tone}`} role="status">
      <span className="status-pill__dot" aria-hidden="true" />
      {label}
    </span>
  );
}

export function InfrastructureSection({
  status,
  data,
  error,
  nowMs,
  onRetry,
}: InfrastructureSectionProps) {
  if (status === "loading" && !data) {
    return (
      <section className="detail-section detail-section--loading" aria-busy="true">
        <div className="skeleton skeleton--section" />
        <p className="dashboard-loading__label">Loading infrastructure…</p>
      </section>
    );
  }

  if (status === "error" && !data) {
    return (
      <section
        className="detail-section"
        aria-labelledby="infrastructure-detail-title"
      >
        <header className="detail-section__header">
          <h2 id="infrastructure-detail-title" className="detail-section__title">
            Infrastructure
          </h2>
        </header>
        <div className="section-error">
          <p>
            Unable to load infrastructure data: {error ?? "Request failed"}
          </p>
          <button type="button" className="button button--inline" onClick={onRetry}>
            Try again
          </button>
        </div>
      </section>
    );
  }

  if (!data) {
    return null;
  }

  const { infrastructure, freshness } = data;
  const services = [
    { label: "Bootnode", value: infrastructure.services.bootnode_up },
    {
      label: "Block Explorer",
      value: infrastructure.services.block_explorer_endpoint_up,
    },
    { label: "Faucet", value: infrastructure.services.faucet_up },
    { label: "Identity", value: infrastructure.services.identity_up },
    { label: "Backend API", value: infrastructure.services.backend_up },
    { label: "Prometheus", value: infrastructure.services.prometheus_up },
    { label: "Grafana", value: infrastructure.services.grafana_endpoint_up },
  ];

  return (
    <SectionShell
      id="infrastructure-detail-title"
      title="Infrastructure"
      description="Monitored system and service availability"
      health={data.health}
      error={error}
      meta={
        <p className="detail-section__freshness">
          <span>Telemetry {formatFreshness(freshness.state)}</span>
          {freshness.updated_at ? (
            <span className="mono">
              {formatUpdatedAt(freshness.updated_at)}
            </span>
          ) : null}
          {freshness.age_seconds !== null ? (
            <span>Age {formatAgeSeconds(freshness.age_seconds)}</span>
          ) : freshness.updated_at ? (
            <span>{formatRelativeAge(freshness.updated_at, nowMs)}</span>
          ) : null}
        </p>
      }
    >
      <div className="detail-two-col">
        <div className="panel-card">
          <h3 className="panel-card__title">Systems</h3>
          <dl className="domain-card__metrics">
            <MetricRow
              label="Systems operational"
              value={formatSystemsOperational(
                infrastructure.systems.up,
                infrastructure.systems.monitored,
              )}
              mono
            />
            <MetricRow
              label="Validators"
              value={formatSystemsOperational(
                infrastructure.validators.up,
                infrastructure.validators.monitored,
              )}
              mono
            />
          </dl>

          <h3 className="panel-card__title panel-card__title--spaced">RPC</h3>
          <ul className="status-list">
            <li className="status-list__item">
              <span>Primary RPC</span>
              <StatusPill online={infrastructure.rpc.primary_up} />
            </li>
            <li className="status-list__item">
              <span>Backup RPC</span>
              <StatusPill online={infrastructure.rpc.backup_up} />
            </li>
          </ul>
        </div>

        <div className="panel-card">
          <h3 className="panel-card__title">Services</h3>
          <ul className="status-list">
            {services.map((service) => (
              <li key={service.label} className="status-list__item">
                <span>{service.label}</span>
                <StatusPill online={service.value} />
              </li>
            ))}
          </ul>
        </div>
      </div>
    </SectionShell>
  );
}
