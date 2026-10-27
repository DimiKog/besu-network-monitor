"use client";

import { formatInteger, shortenAddress } from "@/lib/format";
import type { ProposerCount } from "@/lib/types";

interface ProposerDistributionChartProps {
  distribution: ProposerCount[];
  windowSize: number;
}

export function ProposerDistributionChart({
  distribution,
  windowSize,
}: ProposerDistributionChartProps) {
  const maxCount = Math.max(...distribution.map((item) => item.count), 1);

  return (
    <div className="chart-panel">
      <div className="chart-panel__title-row">
        <h3 className="chart-panel__title">
          Recent Proposer Distribution — Last {formatInteger(windowSize)} Blocks
        </h3>
        <p className="chart-panel__subtitle">
          Distribution reflects only the currently loaded recent block window.
        </p>
      </div>

      {distribution.length === 0 ? (
        <div className="chart-empty">No proposer data available.</div>
      ) : (
        <ul className="bar-list" aria-label="Proposer distribution">
          {distribution.map((item) => {
            const widthPct = (item.count / maxCount) * 100;
            return (
              <li key={item.address} className="bar-list__item">
                <div
                  className="bar-list__label mono"
                  title={item.address}
                  aria-label={item.address}
                >
                  {shortenAddress(item.address)}
                </div>
                <div className="bar-list__track" aria-hidden="true">
                  <div
                    className="bar-list__fill"
                    style={{ width: `${widthPct}%` }}
                  />
                </div>
                <div className="bar-list__value mono">
                  {formatInteger(item.count)}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
