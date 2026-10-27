import {
  chainUrl,
  consensusUrl,
  infrastructureUrl,
  overviewUrl,
} from "./config";
import type {
  ChainResponse,
  ConsensusResponse,
  FreshnessPublic,
  FreshnessState,
  InfrastructureResponse,
  OverviewResponse,
  PublicHealth,
  RecentBlock,
} from "./types";

export class MonitorFetchError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = "MonitorFetchError";
    this.status = status;
  }
}

/** @deprecated Use MonitorFetchError — kept for existing overview tests. */
export class OverviewFetchError extends MonitorFetchError {
  constructor(message: string, status?: number) {
    super(message, status);
    this.name = "OverviewFetchError";
  }
}

const HEALTH_VALUES = new Set<PublicHealth>([
  "healthy",
  "degraded",
  "critical",
  "unknown",
]);

const FRESHNESS_VALUES = new Set<FreshnessState>(["fresh", "stale", "unknown"]);

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function assertHealth(value: unknown, path: string): PublicHealth {
  if (typeof value !== "string" || !HEALTH_VALUES.has(value as PublicHealth)) {
    throw new MonitorFetchError(`Invalid health at ${path}`);
  }
  return value as PublicHealth;
}

function asNullableNumber(value: unknown): number | null {
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

function asNullableBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseFreshness(value: unknown, path: string): FreshnessPublic {
  if (!isObject(value)) {
    throw new MonitorFetchError(`Missing freshness at ${path}`);
  }
  const state = value.state;
  if (typeof state !== "string" || !FRESHNESS_VALUES.has(state as FreshnessState)) {
    throw new MonitorFetchError(`Invalid freshness.state at ${path}`);
  }
  return {
    state: state as FreshnessState,
    age_seconds: asNullableNumber(value.age_seconds),
    stale_after_seconds:
      typeof value.stale_after_seconds === "number"
        ? value.stale_after_seconds
        : 900,
    updated_at: asNullableString(value.updated_at),
  };
}

async function fetchJson(url: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new MonitorFetchError(
      error instanceof Error ? error.message : "Network request failed",
    );
  }

  if (!response.ok) {
    throw new MonitorFetchError(
      `Request failed (${response.status})`,
      response.status,
    );
  }

  return response.json();
}

/** Lightweight runtime check so UI never assumes a broken payload shape. */
export function parseOverviewResponse(data: unknown): OverviewResponse {
  if (!isObject(data)) {
    throw new MonitorFetchError("Overview response is not an object");
  }
  if (!isObject(data.domains)) {
    throw new MonitorFetchError("Overview domains missing");
  }
  const { chain, consensus, infrastructure } = data.domains;
  if (!isObject(chain) || !isObject(consensus) || !isObject(infrastructure)) {
    throw new MonitorFetchError("Overview domain summaries incomplete");
  }
  if (data.observability !== "complete" && data.observability !== "partial") {
    throw new MonitorFetchError("Invalid observability state");
  }
  if (typeof data.updated_at !== "string") {
    throw new MonitorFetchError("Missing updated_at");
  }

  return {
    health: assertHealth(data.health, "health"),
    observability: data.observability,
    updated_at: data.updated_at,
    domains: {
      chain: {
        health: assertHealth(chain.health, "domains.chain.health"),
        block_number: asNullableNumber(chain.block_number),
        latest_block_age_seconds: asNullableNumber(
          chain.latest_block_age_seconds,
        ),
        average_block_time_seconds: asNullableNumber(
          chain.average_block_time_seconds,
        ),
      },
      consensus: {
        health: assertHealth(consensus.health, "domains.consensus.health"),
        freshness:
          typeof consensus.freshness === "string" &&
          FRESHNESS_VALUES.has(consensus.freshness as FreshnessState)
            ? (consensus.freshness as FreshnessState)
            : "unknown",
        validators: asNullableNumber(consensus.validators),
        synced: asNullableNumber(consensus.synced),
        quorum: asNullableNumber(consensus.quorum),
        has_quorum: asNullableBoolean(consensus.has_quorum),
      },
      infrastructure: {
        health: assertHealth(
          infrastructure.health,
          "domains.infrastructure.health",
        ),
        freshness:
          typeof infrastructure.freshness === "string" &&
          FRESHNESS_VALUES.has(infrastructure.freshness as FreshnessState)
            ? (infrastructure.freshness as FreshnessState)
            : "unknown",
        systems_up: asNullableNumber(infrastructure.systems_up),
        systems_monitored: asNullableNumber(infrastructure.systems_monitored),
      },
    },
  };
}

function parseRecentBlock(value: unknown): RecentBlock | null {
  if (!isObject(value) || typeof value.number !== "number") {
    return null;
  }
  return {
    number: value.number,
    timestamp: typeof value.timestamp === "string" ? value.timestamp : "",
    age_seconds:
      typeof value.age_seconds === "number" ? value.age_seconds : 0,
    proposer: typeof value.proposer === "string" ? value.proposer : "",
    transaction_count:
      typeof value.transaction_count === "number" ? value.transaction_count : 0,
    gas_used: typeof value.gas_used === "number" ? value.gas_used : 0,
    gas_limit: typeof value.gas_limit === "number" ? value.gas_limit : 0,
    gas_utilization_percent: asNullableNumber(value.gas_utilization_percent),
    interval_seconds: asNullableNumber(value.interval_seconds),
  };
}

export function parseChainResponse(data: unknown): ChainResponse {
  if (!isObject(data) || !isObject(data.chain) || !isObject(data.rpc)) {
    throw new MonitorFetchError("Invalid chain response");
  }
  if (typeof data.updated_at !== "string") {
    throw new MonitorFetchError("Missing chain updated_at");
  }

  const recentRaw = Array.isArray(data.recent_blocks) ? data.recent_blocks : [];
  const recent_blocks = recentRaw
    .map(parseRecentBlock)
    .filter((block): block is RecentBlock => block !== null);

  return {
    health: assertHealth(data.health, "chain.health"),
    updated_at: data.updated_at,
    chain: {
      chain_id: asNullableNumber(data.chain.chain_id),
      block_number: asNullableNumber(data.chain.block_number),
      latest_block_age_seconds: asNullableNumber(
        data.chain.latest_block_age_seconds,
      ),
      average_block_time_seconds: asNullableNumber(
        data.chain.average_block_time_seconds,
      ),
      recent_block_count:
        typeof data.chain.recent_block_count === "number"
          ? data.chain.recent_block_count
          : recent_blocks.length,
      transactions_recent_blocks: asNullableNumber(
        data.chain.transactions_recent_blocks,
      ),
      average_gas_utilization_percent: asNullableNumber(
        data.chain.average_gas_utilization_percent,
      ),
    },
    rpc: {
      reachable: data.rpc.reachable === true,
      syncing: asNullableBoolean(data.rpc.syncing),
      peer_count: asNullableNumber(data.rpc.peer_count),
      client_version: asNullableString(data.rpc.client_version),
    },
    recent_blocks,
  };
}

export function parseConsensusResponse(data: unknown): ConsensusResponse {
  if (!isObject(data) || !isObject(data.consensus)) {
    throw new MonitorFetchError("Invalid consensus response");
  }
  const drift = isObject(data.consensus.drift) ? data.consensus.drift : {};

  return {
    health: assertHealth(data.health, "consensus.health"),
    freshness: parseFreshness(data.freshness, "consensus.freshness"),
    consensus: {
      reference_block: asNullableNumber(data.consensus.reference_block),
      validator_count: asNullableNumber(data.consensus.validator_count),
      synced_validators: asNullableNumber(data.consensus.synced_validators),
      quorum_required: asNullableNumber(data.consensus.quorum_required),
      has_quorum: asNullableBoolean(data.consensus.has_quorum),
      drift: {
        observed_max_blocks: asNullableNumber(drift.observed_max_blocks),
        allowed_max_blocks: asNullableNumber(drift.allowed_max_blocks),
      },
      out_of_sync_count: asNullableNumber(data.consensus.out_of_sync_count),
    },
  };
}

export function parseInfrastructureResponse(
  data: unknown,
): InfrastructureResponse {
  if (!isObject(data) || !isObject(data.infrastructure)) {
    throw new MonitorFetchError("Invalid infrastructure response");
  }
  const infra = data.infrastructure;
  const systems = isObject(infra.systems) ? infra.systems : {};
  const validators = isObject(infra.validators) ? infra.validators : {};
  const rpc = isObject(infra.rpc) ? infra.rpc : {};
  const services = isObject(infra.services) ? infra.services : {};

  return {
    health: assertHealth(data.health, "infrastructure.health"),
    freshness: parseFreshness(data.freshness, "infrastructure.freshness"),
    infrastructure: {
      systems: {
        monitored: asNullableNumber(systems.monitored),
        up: asNullableNumber(systems.up),
        down: asNullableNumber(systems.down),
      },
      validators: {
        monitored: asNullableNumber(validators.monitored),
        up: asNullableNumber(validators.up),
        down: asNullableNumber(validators.down),
      },
      rpc: {
        primary_up: asNullableBoolean(rpc.primary_up),
        backup_up: asNullableBoolean(rpc.backup_up),
      },
      services: {
        bootnode_up: asNullableBoolean(services.bootnode_up),
        block_explorer_endpoint_up: asNullableBoolean(
          services.block_explorer_endpoint_up,
        ),
        faucet_up: asNullableBoolean(services.faucet_up),
        identity_up: asNullableBoolean(services.identity_up),
        backend_up: asNullableBoolean(services.backend_up),
        prometheus_up: asNullableBoolean(services.prometheus_up),
        grafana_endpoint_up: asNullableBoolean(services.grafana_endpoint_up),
      },
    },
  };
}

export async function fetchOverview(
  signal?: AbortSignal,
): Promise<OverviewResponse> {
  return parseOverviewResponse(await fetchJson(overviewUrl(), signal));
}

export async function fetchChain(signal?: AbortSignal): Promise<ChainResponse> {
  return parseChainResponse(await fetchJson(chainUrl(), signal));
}

export async function fetchConsensus(
  signal?: AbortSignal,
): Promise<ConsensusResponse> {
  return parseConsensusResponse(await fetchJson(consensusUrl(), signal));
}

export async function fetchInfrastructure(
  signal?: AbortSignal,
): Promise<InfrastructureResponse> {
  return parseInfrastructureResponse(
    await fetchJson(infrastructureUrl(), signal),
  );
}
