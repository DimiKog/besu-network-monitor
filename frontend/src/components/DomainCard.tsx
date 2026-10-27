import { MetricRow } from "@/components/MetricRow";
import { StatusBadge } from "@/components/StatusBadge";
import {
  formatFreshness,
  formatInteger,
  formatQuorumState,
  formatSeconds,
  formatSystemsOperational,
} from "@/lib/format";
import type {
  OverviewChainDomain,
  OverviewConsensusDomain,
  OverviewInfrastructureDomain,
} from "@/lib/types";

type DomainKind = "chain" | "consensus" | "infrastructure";

interface DomainCardProps {
  kind: DomainKind;
  chain?: OverviewChainDomain;
  consensus?: OverviewConsensusDomain;
  infrastructure?: OverviewInfrastructureDomain;
}

const TITLES: Record<DomainKind, string> = {
  chain: "Chain",
  consensus: "Consensus",
  infrastructure: "Infrastructure",
};

const DESCRIPTIONS: Record<DomainKind, string> = {
  chain: "Live head and block timing",
  consensus: "QBFT validator quorum",
  infrastructure: "Monitored system availability",
};

export function DomainCard({
  kind,
  chain,
  consensus,
  infrastructure,
}: DomainCardProps) {
  const health =
    kind === "chain"
      ? chain?.health
      : kind === "consensus"
        ? consensus?.health
        : infrastructure?.health;

  return (
    <article className="domain-card" aria-labelledby={`domain-${kind}-title`}>
      <header className="domain-card__header">
        <div>
          <h3 id={`domain-${kind}-title`} className="domain-card__title">
            {TITLES[kind]}
          </h3>
          <p className="domain-card__description">{DESCRIPTIONS[kind]}</p>
        </div>
        {health ? <StatusBadge health={health} /> : null}
      </header>

      <dl className="domain-card__metrics">
        {kind === "chain" && chain ? (
          <>
            <MetricRow
              label="Current block"
              value={formatInteger(chain.block_number)}
              mono
            />
            <MetricRow
              label="Average block time"
              value={formatSeconds(chain.average_block_time_seconds)}
              mono
            />
            <MetricRow
              label="Last block age"
              value={formatSeconds(chain.latest_block_age_seconds)}
              mono
            />
          </>
        ) : null}

        {kind === "consensus" && consensus ? (
          <>
            <MetricRow
              label="Validators"
              value={formatInteger(consensus.validators)}
              mono
            />
            <MetricRow
              label="Synced"
              value={formatInteger(consensus.synced)}
              mono
            />
            <MetricRow
              label="Quorum"
              value={formatInteger(consensus.quorum)}
              mono
            />
            <MetricRow
              label="Quorum state"
              value={formatQuorumState(consensus.has_quorum)}
            />
            <MetricRow
              label="Telemetry"
              value={formatFreshness(consensus.freshness)}
            />
          </>
        ) : null}

        {kind === "infrastructure" && infrastructure ? (
          <>
            <MetricRow
              label="Systems operational"
              value={formatSystemsOperational(
                infrastructure.systems_up,
                infrastructure.systems_monitored,
              )}
              mono
            />
            <MetricRow
              label="Telemetry"
              value={formatFreshness(infrastructure.freshness)}
            />
          </>
        ) : null}
      </dl>
    </article>
  );
}
