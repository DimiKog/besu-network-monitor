"use client";

import { BlockIntervalChart } from "@/components/BlockIntervalChart";
import { MetricRow } from "@/components/MetricRow";
import { RecentBlocksTable } from "@/components/RecentBlocksTable";
import { SectionShell } from "@/components/SectionShell";
import {
  formatInteger,
  formatPercent,
  formatRelativeAge,
  formatSeconds,
  formatUpdatedAt,
} from "@/lib/format";
import type { ChainResponse } from "@/lib/types";
import type { ResourceStatus } from "@/hooks/usePollingResource";

interface ChainActivitySectionProps {
  status: ResourceStatus;
  data: ChainResponse | null;
  error: string | null;
  nowMs?: number;
  onRetry: () => void;
}

export function ChainActivitySection({
  status,
  data,
  error,
  nowMs,
  onRetry,
}: ChainActivitySectionProps) {
  if (status === "loading" && !data) {
    return (
      <section className="detail-section detail-section--loading" aria-busy="true">
        <div className="skeleton skeleton--section" />
        <p className="dashboard-loading__label">Loading chain activity…</p>
      </section>
    );
  }

  if (status === "error" && !data) {
    return (
      <section className="detail-section" aria-labelledby="chain-activity-title">
        <header className="detail-section__header">
          <h2 id="chain-activity-title" className="detail-section__title">
            Chain Activity
          </h2>
        </header>
        <div className="section-error">
          <p>Unable to load chain data: {error ?? "Request failed"}</p>
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

  const windowCount =
    data.recent_blocks.length || data.chain.recent_block_count || 0;

  return (
    <SectionShell
      id="chain-activity-title"
      title="Chain Activity"
      description="Live head metrics and recent block timing from public JSON-RPC"
      health={data.health}
      error={error}
      meta={
        <p className="detail-section__freshness">
          <span>Updated</span>
          <span className="mono">{formatUpdatedAt(data.updated_at)}</span>
          <span>{formatRelativeAge(data.updated_at, nowMs)}</span>
        </p>
      }
    >
      <div className="metric-strip" aria-label="Chain key metrics">
        <div className="metric-strip__item">
          <MetricRow
            label="Current block"
            value={formatInteger(data.chain.block_number)}
            mono
          />
        </div>
        <div className="metric-strip__item">
          <MetricRow
            label="Average block time"
            value={formatSeconds(data.chain.average_block_time_seconds)}
            mono
          />
        </div>
        <div className="metric-strip__item">
          <MetricRow
            label={`Transactions — last ${formatInteger(windowCount)} blocks`}
            value={formatInteger(data.chain.transactions_recent_blocks)}
            mono
          />
        </div>
        <div className="metric-strip__item">
          <MetricRow
            label="Average gas utilization"
            value={formatPercent(data.chain.average_gas_utilization_percent)}
            mono
          />
        </div>
      </div>

      <div className="split-panels">
        <BlockIntervalChart blocks={data.recent_blocks} />
        <RecentBlocksTable blocks={data.recent_blocks} />
      </div>
    </SectionShell>
  );
}
