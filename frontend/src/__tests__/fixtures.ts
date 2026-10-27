import type {
  ChainResponse,
  ConsensusResponse,
  InfrastructureResponse,
  OverviewResponse,
  RecentBlock,
} from "@/lib/types";

export function healthyOverview(
  overrides: Partial<OverviewResponse> = {},
): OverviewResponse {
  return {
    health: "healthy",
    observability: "complete",
    updated_at: "2026-10-27T08:38:11.490664Z",
    domains: {
      chain: {
        health: "healthy",
        block_number: 8197534,
        latest_block_age_seconds: 4.49,
        average_block_time_seconds: 5.0,
      },
      consensus: {
        health: "healthy",
        freshness: "fresh",
        validators: 7,
        synced: 7,
        quorum: 5,
        has_quorum: true,
      },
      infrastructure: {
        health: "healthy",
        freshness: "fresh",
        systems_up: 16,
        systems_monitored: 16,
      },
    },
    ...overrides,
  };
}

export function partialUnknownOverview(): OverviewResponse {
  return healthyOverview({
    health: "healthy",
    observability: "partial",
    domains: {
      chain: {
        health: "healthy",
        block_number: 8197534,
        latest_block_age_seconds: 4.49,
        average_block_time_seconds: 5.0,
      },
      consensus: {
        health: "unknown",
        freshness: "unknown",
        validators: null,
        synced: null,
        quorum: null,
        has_quorum: null,
      },
      infrastructure: {
        health: "healthy",
        freshness: "fresh",
        systems_up: 16,
        systems_monitored: 16,
      },
    },
  });
}

export function nullMetricsOverview(): OverviewResponse {
  return {
    health: "unknown",
    observability: "partial",
    updated_at: "2026-10-27T08:38:11.490664Z",
    domains: {
      chain: {
        health: "unknown",
        block_number: null,
        latest_block_age_seconds: null,
        average_block_time_seconds: null,
      },
      consensus: {
        health: "unknown",
        freshness: "unknown",
        validators: null,
        synced: null,
        quorum: null,
        has_quorum: null,
      },
      infrastructure: {
        health: "unknown",
        freshness: "unknown",
        systems_up: null,
        systems_monitored: null,
      },
    },
  };
}

export function makeRecentBlock(
  overrides: Partial<RecentBlock> & Pick<RecentBlock, "number" | "proposer">,
): RecentBlock {
  return {
    timestamp: "2026-10-27T08:55:52Z",
    age_seconds: 5,
    transaction_count: 0,
    gas_used: 0,
    gas_limit: 30_000_000,
    gas_utilization_percent: 0,
    interval_seconds: 5,
    ...overrides,
  };
}

export function sampleRecentBlocks(): RecentBlock[] {
  return [
    makeRecentBlock({
      number: 100,
      proposer: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      age_seconds: 2,
      transaction_count: 1,
      gas_utilization_percent: 1.5,
      interval_seconds: 5,
    }),
    makeRecentBlock({
      number: 99,
      proposer: "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      age_seconds: 7,
      interval_seconds: 5,
    }),
    makeRecentBlock({
      number: 98,
      proposer: "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      age_seconds: 12,
      interval_seconds: 4.5,
    }),
    makeRecentBlock({
      number: 97,
      proposer: "0xcccccccccccccccccccccccccccccccccccccccc",
      age_seconds: 17,
      interval_seconds: null,
    }),
  ];
}

export function sampleChainResponse(
  overrides: Partial<ChainResponse> = {},
): ChainResponse {
  const recent_blocks = sampleRecentBlocks();
  return {
    health: "healthy",
    updated_at: "2026-10-27T08:55:53.653943Z",
    chain: {
      chain_id: 424242,
      block_number: 100,
      latest_block_age_seconds: 2,
      average_block_time_seconds: 5,
      recent_block_count: recent_blocks.length,
      transactions_recent_blocks: 1,
      average_gas_utilization_percent: 0.4,
    },
    rpc: {
      reachable: true,
      syncing: false,
      peer_count: 9,
      client_version: "besu/v25.8.0",
    },
    recent_blocks,
    ...overrides,
  };
}

export function sampleConsensusResponse(
  overrides: Partial<ConsensusResponse> = {},
): ConsensusResponse {
  return {
    health: "healthy",
    freshness: {
      state: "fresh",
      age_seconds: 180,
      stale_after_seconds: 900,
      updated_at: "2026-10-27T08:52:53.969382Z",
    },
    consensus: {
      reference_block: 8197711,
      validator_count: 7,
      synced_validators: 7,
      quorum_required: 5,
      has_quorum: true,
      drift: {
        observed_max_blocks: 0,
        allowed_max_blocks: 2,
      },
      out_of_sync_count: 0,
    },
    ...overrides,
  };
}

export function sampleInfrastructureResponse(
  overrides: Partial<InfrastructureResponse> = {},
): InfrastructureResponse {
  return {
    health: "healthy",
    freshness: {
      state: "fresh",
      age_seconds: 212,
      stale_after_seconds: 900,
      updated_at: "2026-10-27T08:52:22.207219Z",
    },
    infrastructure: {
      systems: { monitored: 16, up: 16, down: 0 },
      validators: { monitored: 7, up: 7, down: 0 },
      rpc: { primary_up: true, backup_up: true },
      services: {
        bootnode_up: true,
        block_explorer_endpoint_up: true,
        faucet_up: true,
        identity_up: true,
        backend_up: true,
        prometheus_up: true,
        grafana_endpoint_up: false,
      },
    },
    ...overrides,
  };
}
