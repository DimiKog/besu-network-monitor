const DEFAULT_API_BASE_URL = "https://api.monitor.dimikog.org";
const DEFAULT_NETWORK_NAME = "Besu Edu-Net";
const DEFAULT_CHAIN_ID = "424242";
const DEFAULT_BLOCKSCOUT_BASE_URL = "https://blockexplorer.dimikog.org";

/** Overview / chain — live operational data. */
export const OVERVIEW_REFRESH_MS = 15_000;
export const CHAIN_REFRESH_MS = 15_000;

/** Consensus / infrastructure — collected every ~5 minutes. */
export const CONSENSUS_REFRESH_MS = 60_000;
export const INFRASTRUCTURE_REFRESH_MS = 60_000;

/** Stagger initial fetches to avoid a synchronized request burst. */
export const CHAIN_FETCH_OFFSET_MS = 2_000;
export const CONSENSUS_FETCH_OFFSET_MS = 4_000;
export const INFRASTRUCTURE_FETCH_OFFSET_MS = 6_000;

export const RECENT_BLOCKS_INITIAL_ROWS = 10;
export const TYPICAL_BLOCK_INTERVAL_SECONDS = 5;

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

export function getApiBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  return trimTrailingSlash(fromEnv || DEFAULT_API_BASE_URL);
}

export function getNetworkName(): string {
  return process.env.NEXT_PUBLIC_NETWORK_NAME?.trim() || DEFAULT_NETWORK_NAME;
}

export function getChainId(): string {
  return process.env.NEXT_PUBLIC_CHAIN_ID?.trim() || DEFAULT_CHAIN_ID;
}

export function getBlockscoutBaseUrl(): string {
  const fromEnv = process.env.NEXT_PUBLIC_BLOCKSCOUT_BASE_URL?.trim();
  return trimTrailingSlash(fromEnv || DEFAULT_BLOCKSCOUT_BASE_URL);
}

export function overviewUrl(): string {
  return `${getApiBaseUrl()}/api/v1/overview`;
}

export function chainUrl(): string {
  return `${getApiBaseUrl()}/api/v1/chain`;
}

export function consensusUrl(): string {
  return `${getApiBaseUrl()}/api/v1/consensus`;
}

export function infrastructureUrl(): string {
  return `${getApiBaseUrl()}/api/v1/infrastructure`;
}

/** Standard Blockscout block path: /block/{number} */
export function blockscoutBlockUrl(blockNumber: number): string {
  return `${getBlockscoutBaseUrl()}/block/${blockNumber}`;
}
