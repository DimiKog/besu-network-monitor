/** Health vocabulary from the Monitor API — do not reinterpret. */
export type PublicHealth = "healthy" | "degraded" | "critical" | "unknown";

export type ObservabilityState = "complete" | "partial";

export type FreshnessState = "fresh" | "stale" | "unknown";

export interface OverviewChainDomain {
  health: PublicHealth;
  block_number: number | null;
  latest_block_age_seconds: number | null;
  average_block_time_seconds: number | null;
}

export interface OverviewConsensusDomain {
  health: PublicHealth;
  freshness: FreshnessState;
  validators: number | null;
  synced: number | null;
  quorum: number | null;
  has_quorum: boolean | null;
}

export interface OverviewInfrastructureDomain {
  health: PublicHealth;
  freshness: FreshnessState;
  systems_up: number | null;
  systems_monitored: number | null;
}

export interface OverviewDomains {
  chain: OverviewChainDomain;
  consensus: OverviewConsensusDomain;
  infrastructure: OverviewInfrastructureDomain;
}

/** Compact dashboard composition from GET /api/v1/overview. */
export interface OverviewResponse {
  health: PublicHealth;
  observability: ObservabilityState;
  updated_at: string;
  domains: OverviewDomains;
}

export interface FreshnessPublic {
  state: FreshnessState;
  age_seconds: number | null;
  stale_after_seconds: number;
  updated_at: string | null;
}

export interface RecentBlock {
  number: number;
  timestamp: string;
  age_seconds: number;
  proposer: string;
  transaction_count: number;
  gas_used: number;
  gas_limit: number;
  gas_utilization_percent: number | null;
  interval_seconds: number | null;
}

export interface ChainMetrics {
  chain_id: number | null;
  block_number: number | null;
  latest_block_age_seconds: number | null;
  average_block_time_seconds: number | null;
  recent_block_count: number;
  transactions_recent_blocks: number | null;
  average_gas_utilization_percent: number | null;
}

export interface RpcNode {
  reachable: boolean;
  syncing: boolean | null;
  peer_count: number | null;
  client_version: string | null;
}

export interface ChainResponse {
  health: PublicHealth;
  updated_at: string;
  chain: ChainMetrics;
  rpc: RpcNode;
  recent_blocks: RecentBlock[];
}

export interface DriftPublic {
  observed_max_blocks: number | null;
  allowed_max_blocks: number | null;
}

export interface ConsensusPublic {
  reference_block: number | null;
  validator_count: number | null;
  synced_validators: number | null;
  quorum_required: number | null;
  has_quorum: boolean | null;
  drift: DriftPublic;
  out_of_sync_count: number | null;
}

export interface ConsensusResponse {
  health: PublicHealth;
  freshness: FreshnessPublic;
  consensus: ConsensusPublic;
}

export interface SystemsPublic {
  monitored: number | null;
  up: number | null;
  down: number | null;
}

export interface ValidatorsInfraPublic {
  monitored: number | null;
  up: number | null;
  down: number | null;
}

export interface RpcInfraPublic {
  primary_up: boolean | null;
  backup_up: boolean | null;
}

export interface ServicesPublic {
  bootnode_up: boolean | null;
  block_explorer_endpoint_up: boolean | null;
  faucet_up: boolean | null;
  identity_up: boolean | null;
  backend_up: boolean | null;
  prometheus_up: boolean | null;
  grafana_endpoint_up: boolean | null;
}

export interface InfrastructurePublic {
  systems: SystemsPublic;
  validators: ValidatorsInfraPublic;
  rpc: RpcInfraPublic;
  services: ServicesPublic;
}

export interface InfrastructureResponse {
  health: PublicHealth;
  freshness: FreshnessPublic;
  infrastructure: InfrastructurePublic;
}

export interface ProposerCount {
  address: string;
  count: number;
}
