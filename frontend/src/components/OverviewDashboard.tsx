"use client";

import { useEffect, useState } from "react";
import { ChainActivitySection } from "@/components/ChainActivitySection";
import { ConsensusSection } from "@/components/ConsensusSection";
import { DomainCard } from "@/components/DomainCard";
import { Header } from "@/components/Header";
import { InfrastructureSection } from "@/components/InfrastructureSection";
import { NetworkHealthBanner } from "@/components/NetworkHealthBanner";
import { SiteFooter } from "@/components/SiteFooter";
import { useChain } from "@/hooks/useChain";
import { useConsensus } from "@/hooks/useConsensus";
import { useInfrastructure } from "@/hooks/useInfrastructure";
import { useOverview } from "@/hooks/useOverview";

function LoadingSkeleton() {
  return (
    <div className="dashboard-loading" role="status" aria-live="polite">
      <div className="skeleton skeleton--banner" />
      <div className="skeleton-grid">
        <div className="skeleton skeleton--card" />
        <div className="skeleton skeleton--card" />
        <div className="skeleton skeleton--card" />
      </div>
      <p className="dashboard-loading__label">Loading network overview…</p>
    </div>
  );
}

function ErrorPanel({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <section className="error-panel" role="alert">
      <h2 className="error-panel__title">Unable to load network overview</h2>
      <p className="error-panel__message">{message}</p>
      <p className="error-panel__hint">
        The Monitor API could not be reached. No demo values are shown.
      </p>
      <button type="button" className="button" onClick={onRetry}>
        Try again
      </button>
    </section>
  );
}

export function OverviewDashboard() {
  const overview = useOverview();
  const chain = useChain();
  const consensus = useConsensus();
  const infrastructure = useInfrastructure();
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const live = overview.status === "success" && overview.data !== null;

  return (
    <div className="dashboard">
      <Header
        updatedAt={overview.data?.updated_at ?? overview.lastFetchedAt}
        live={live}
        nowMs={nowMs}
      />

      <main className="dashboard__main">
        {overview.status === "loading" && !overview.data ? (
          <LoadingSkeleton />
        ) : null}

        {overview.status === "error" && !overview.data ? (
          <ErrorPanel
            message={overview.error ?? "Request failed"}
            onRetry={overview.refresh}
          />
        ) : null}

        {overview.data ? (
          <>
            {overview.error ? (
              <p className="refresh-warning" role="status">
                Latest overview refresh failed: {overview.error}. Showing last
                successful overview.
              </p>
            ) : null}

            <NetworkHealthBanner
              health={overview.data.health}
              observability={overview.data.observability}
              updatedAt={overview.data.updated_at}
              nowMs={nowMs}
            />

            <section
              className="domain-grid"
              aria-label="Domain health summaries"
            >
              <DomainCard kind="chain" chain={overview.data.domains.chain} />
              <DomainCard
                kind="consensus"
                consensus={overview.data.domains.consensus}
              />
              <DomainCard
                kind="infrastructure"
                infrastructure={overview.data.domains.infrastructure}
              />
            </section>
          </>
        ) : null}

        <ChainActivitySection
          status={chain.status}
          data={chain.data}
          error={chain.error}
          nowMs={nowMs}
          onRetry={chain.refresh}
        />

        <ConsensusSection
          status={consensus.status}
          data={consensus.data}
          error={consensus.error}
          recentBlocks={chain.data?.recent_blocks ?? []}
          nowMs={nowMs}
          onRetry={consensus.refresh}
        />

        <InfrastructureSection
          status={infrastructure.status}
          data={infrastructure.data}
          error={infrastructure.error}
          nowMs={nowMs}
          onRetry={infrastructure.refresh}
        />
      </main>

      <SiteFooter />
    </div>
  );
}
