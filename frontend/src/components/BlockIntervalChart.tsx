"use client";

import { useId, useMemo, useState } from "react";
import { TYPICAL_BLOCK_INTERVAL_SECONDS } from "@/lib/config";
import { formatInteger, formatSeconds } from "@/lib/format";
import type { RecentBlock } from "@/lib/types";

interface BlockIntervalChartProps {
  blocks: RecentBlock[];
}

interface PlotPoint {
  number: number;
  interval: number;
}

export function BlockIntervalChart({ blocks }: BlockIntervalChartProps) {
  const gradientId = useId();
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const points = useMemo<PlotPoint[]>(() => {
    return [...blocks]
      .filter(
        (block) =>
          block.interval_seconds !== null &&
          block.interval_seconds !== undefined &&
          !Number.isNaN(block.interval_seconds),
      )
      .sort((a, b) => a.number - b.number)
      .map((block) => ({
        number: block.number,
        interval: block.interval_seconds as number,
      }));
  }, [blocks]);

  const width = 640;
  const height = 220;
  const pad = { top: 18, right: 16, bottom: 36, left: 40 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;

  if (points.length === 0) {
    return (
      <div className="chart-empty" role="img" aria-label="No block interval data">
        No interval data available for the current window.
      </div>
    );
  }

  const minY = 0;
  const maxY = Math.max(
    TYPICAL_BLOCK_INTERVAL_SECONDS * 1.4,
    ...points.map((point) => point.interval),
  );
  const yRange = maxY - minY || 1;

  const xAt = (index: number) =>
    pad.left +
    (points.length === 1 ? innerW / 2 : (index / (points.length - 1)) * innerW);
  const yAt = (value: number) =>
    pad.top + innerH - ((value - minY) / yRange) * innerH;

  const path = points
    .map((point, index) => {
      const command = index === 0 ? "M" : "L";
      return `${command}${xAt(index).toFixed(2)} ${yAt(point.interval).toFixed(2)}`;
    })
    .join(" ");

  const refY = yAt(TYPICAL_BLOCK_INTERVAL_SECONDS);
  const first = points[0];
  const last = points[points.length - 1];
  const hovered = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div className="chart-panel">
      <div className="chart-panel__title-row">
        <h3 className="chart-panel__title">
          Block Interval — Last {formatInteger(points.length)} Blocks
        </h3>
        <p className="chart-panel__subtitle">
          Null intervals omitted · visual reference at{" "}
          {TYPICAL_BLOCK_INTERVAL_SECONDS}s
        </p>
      </div>

      <div className="chart-svg-wrap">
        <svg
          className="chart-svg"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Block interval chart for ${points.length} blocks`}
          onMouseLeave={() => setHoverIndex(null)}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3b6fd8" stopOpacity="0.16" />
              <stop offset="100%" stopColor="#3b6fd8" stopOpacity="0" />
            </linearGradient>
          </defs>

          {[0, 0.5, 1].map((fraction) => {
            const value = minY + yRange * (1 - fraction);
            const y = pad.top + innerH * fraction;
            return (
              <g key={fraction}>
                <line
                  x1={pad.left}
                  x2={width - pad.right}
                  y1={y}
                  y2={y}
                  className="chart-grid"
                />
                <text
                  x={pad.left - 8}
                  y={y + 3}
                  textAnchor="end"
                  className="chart-axis-label"
                >
                  {value.toFixed(0)}s
                </text>
              </g>
            );
          })}

          <line
            x1={pad.left}
            x2={width - pad.right}
            y1={refY}
            y2={refY}
            className="chart-reference"
          />
          <text
            x={width - pad.right}
            y={refY - 6}
            textAnchor="end"
            className="chart-reference-label"
          >
            Typical {TYPICAL_BLOCK_INTERVAL_SECONDS}s
          </text>

          <path
            d={`${path} L${xAt(points.length - 1).toFixed(2)} ${pad.top + innerH} L${xAt(0).toFixed(2)} ${pad.top + innerH} Z`}
            fill={`url(#${gradientId})`}
          />
          <path d={path} className="chart-line" fill="none" />

          {points.map((point, index) => (
            <circle
              key={point.number}
              cx={xAt(index)}
              cy={yAt(point.interval)}
              r={hoverIndex === index ? 4.5 : 2.5}
              className={
                hoverIndex === index ? "chart-dot chart-dot--active" : "chart-dot"
              }
              onMouseEnter={() => setHoverIndex(index)}
            />
          ))}

          <text
            x={pad.left}
            y={height - 10}
            className="chart-axis-label"
          >
            {formatInteger(first.number)}
          </text>
          <text
            x={width - pad.right}
            y={height - 10}
            textAnchor="end"
            className="chart-axis-label"
          >
            {formatInteger(last.number)}
          </text>
        </svg>

        {hovered ? (
          <div className="chart-tooltip" role="status">
            Block {formatInteger(hovered.number)} ·{" "}
            {formatSeconds(hovered.interval)}
          </div>
        ) : null}
      </div>
    </div>
  );
}
