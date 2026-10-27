import type { ProposerCount, RecentBlock } from "./types";

/** Client-side presentation aggregation from recent_blocks proposers. */
export function computeProposerDistribution(
  blocks: RecentBlock[] | null | undefined,
): ProposerCount[] {
  if (!blocks || blocks.length === 0) {
    return [];
  }

  const counts = new Map<string, number>();
  for (const block of blocks) {
    const address = block.proposer?.trim();
    if (!address) {
      continue;
    }
    counts.set(address, (counts.get(address) ?? 0) + 1);
  }

  return Array.from(counts.entries())
    .map(([address, count]) => ({ address, count }))
    .sort((a, b) => b.count - a.count || a.address.localeCompare(b.address));
}
