"use client";

import { useState } from "react";
import { blockscoutBlockUrl, RECENT_BLOCKS_INITIAL_ROWS } from "@/lib/config";
import {
  formatAgeSeconds,
  formatInteger,
  formatPercent,
  formatSeconds,
  shortenAddress,
} from "@/lib/format";
import type { RecentBlock } from "@/lib/types";

interface RecentBlocksTableProps {
  blocks: RecentBlock[];
}

export function RecentBlocksTable({ blocks }: RecentBlocksTableProps) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded
    ? blocks
    : blocks.slice(0, RECENT_BLOCKS_INITIAL_ROWS);
  const hiddenCount = Math.max(0, blocks.length - RECENT_BLOCKS_INITIAL_ROWS);

  return (
    <div className="table-panel">
      <div className="chart-panel__title-row">
        <h3 className="chart-panel__title">Recent Blocks</h3>
        <p className="chart-panel__subtitle">
          Showing {formatInteger(visible.length)} of{" "}
          {formatInteger(blocks.length)}
        </p>
      </div>

      {blocks.length === 0 ? (
        <div className="chart-empty">No recent blocks available.</div>
      ) : (
        <>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Block</th>
                  <th scope="col">Age</th>
                  <th scope="col">Proposer</th>
                  <th scope="col">Tx</th>
                  <th scope="col">Gas</th>
                  <th scope="col">Interval</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((block) => {
                  const blockLabel = formatInteger(block.number);
                  return (
                    <tr key={block.number}>
                      <td className="mono">
                        <a
                          className="table-link"
                          href={blockscoutBlockUrl(block.number)}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={`Open block ${blockLabel} in Blockscout`}
                        >
                          {blockLabel}
                        </a>
                      </td>
                      <td className="mono">
                        {formatAgeSeconds(block.age_seconds)}
                      </td>
                      <td
                        className="mono data-table__proposer"
                        title={block.proposer}
                        aria-label={block.proposer || "Proposer unavailable"}
                      >
                        {shortenAddress(block.proposer)}
                      </td>
                      <td className="mono">
                        {formatInteger(block.transaction_count)}
                      </td>
                      <td className="mono">
                        {formatPercent(block.gas_utilization_percent)}
                      </td>
                      <td className="mono">
                        {formatSeconds(block.interval_seconds)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {hiddenCount > 0 ? (
            <button
              type="button"
              className="button button--inline"
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded
                ? "Show fewer"
                : `Show all ${formatInteger(blocks.length)} blocks`}
            </button>
          ) : null}
        </>
      )}
    </div>
  );
}
