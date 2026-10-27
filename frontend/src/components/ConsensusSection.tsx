"use client";

import { MetricRow } from "@/components/MetricRow";
import { ProposerDistributionChart } from "@/components/ProposerDistributionChart";
import { SectionShell } from "@/components/SectionShell";
import type { ResourceStatus } from "@/hooks/usePollingResource";
import {
  formatAgeSeconds,
  formatFreshness,
  formatInteger,
  formatQuorumState,
  formatRelativeAge,
  formatUpdatedAt,
} from "@/lib/format";
import { computeProposerDistribution } from "@/lib/proposers";
import type { ConsensusResponse, RecentBlock } from "@/lib/types";

interface ConsensusSectionProps {
  status: ResourceStatus;
  data: ConsensusResponse | null;
  error: string | null;
  recentBlocks: RecentBlock[];
  nowMs?: number;
  onRetry: () => void;
}

export function ConsensusSection({
  status,
  data,
  error,
  recentBlocks,
  nowMs,
  onRetry,
}: ConsensusSectionProps) {
  if (status === "loading" && !data) {
    return (
      <section className="detail-section detail-section--loading" aria-busy="true">
        <div className="skeleton skeleton--section" />
        <p className="dashboard-loading__label">Loading consensus…</p>
      </section>
    );
  }

  if (status === "error" && !data) {
    return (
      <section className="detail-section" aria-labelledby="consensus-detail-title">
        <header className="detail-section__header">
          <h2 id="consensus-detail-title" className="detail-section__title">
            QBFT Consensus
          </h2>
        </header>
        <div className="section-error">
          <p>Unable to load consensus data: {error ?? "Request failed"}</p>
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

  const distribution = computeProposerDistribution(recentBlocks);
  const freshnessUpdated = data.freshness.updated_at;

  return (
    <SectionShell
      id="consensus-detail-title"
      title="QBFT Consensus"
      description="Collected validator quorum and drift telemetry"
      health={data.health}
      error={error}
      meta={
        <p className="detail-section__freshness">
          <span>Telemetry {formatFreshness(data.freshness.state)}</span>
          {freshnessUpdated ? (
            <span className="mono">{formatUpdatedAt(freshnessUpdated)}</span>
          ) : null}
          {data.freshness.age_seconds !== null ? (
            <span>Age {formatAgeSeconds(data.freshness.age_seconds)}</span>
          ) : freshnessUpdated ? (
            <span>{formatRelativeAge(freshnessUpdated, nowMs)}</span>
          ) : null}
        </p>
      }
    >
      <div className="detail-two-col">
        <div className="panel-card">
          <h3 className="panel-card__title">Consensus metrics</h3>
          <dl className="domain-card__metrics">
            <MetricRow
              label="Reference block"
              value={formatInteger(data.consensus.reference_block)}
              mono
            />
            <MetricRow
              label="Validators"
              value={formatInteger(data.consensus.validator_count)}
              mono
            />
            <MetricRow
              label="Synced"
              value={formatInteger(data.consensus.synced_validators)}
              mono
            />
            <MetricRow
              label="Quorum required"
              value={formatInteger(data.consensus.quorum_required)}
              mono
            />
            <MetricRow
              label="Quorum state"
              value={formatQuorumState(data.consensus.has_quorum)}
            />
            <MetricRow
              label="Observed max drift"
              value={formatInteger(data.consensus.drift.observed_max_blocks)}
              mono
            />
            <MetricRow
              label="Allowed max drift"
              value={formatInteger(data.consensus.drift.allowed_max_blocks)}
              mono
            />
            <MetricRow
              label="Out of sync"
              value={formatInteger(data.consensus.out_of_sync_count)}
              mono
            />
          </dl>
        </div>

        <ProposerDistributionChart
          distribution={distribution}
          windowSize={recentBlocks.length}
        />
      </div>
    </SectionShell>
  );
}
