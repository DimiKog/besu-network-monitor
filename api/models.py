"""Public response models for the Monitor API.

These models describe what clients receive. They intentionally omit collector
source ``status`` and any internal/operational fields.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

PublicHealth = Literal["healthy", "degraded", "critical", "unknown"]
FreshnessState = Literal["fresh", "stale", "unknown"]


class ServiceHealthResponse(BaseModel):
    """API process liveness only — not Besu network health."""

    status: Literal["ok"] = "ok"
    service: str


class DriftPublic(BaseModel):
    observed_max_blocks: Optional[int] = None
    allowed_max_blocks: Optional[int] = None


class ConsensusPublic(BaseModel):
    reference_block: Optional[int] = None
    validator_count: Optional[int] = None
    synced_validators: Optional[int] = None
    quorum_required: Optional[int] = None
    has_quorum: Optional[bool] = None
    drift: DriftPublic = Field(default_factory=DriftPublic)
    out_of_sync_count: Optional[int] = None


class FreshnessPublic(BaseModel):
    state: FreshnessState
    age_seconds: Optional[float] = None
    stale_after_seconds: int
    updated_at: Optional[str] = None


class ConsensusResponse(BaseModel):
    """Normalized public consensus view."""

    health: PublicHealth
    freshness: FreshnessPublic
    consensus: ConsensusPublic


class SystemsPublic(BaseModel):
    monitored: Optional[int] = None
    up: Optional[int] = None
    down: Optional[int] = None


class ValidatorsInfraPublic(BaseModel):
    monitored: Optional[int] = None
    up: Optional[int] = None
    down: Optional[int] = None


class RpcInfraPublic(BaseModel):
    primary_up: Optional[bool] = None
    backup_up: Optional[bool] = None


class ServicesPublic(BaseModel):
    bootnode_up: Optional[bool] = None
    block_explorer_endpoint_up: Optional[bool] = None
    faucet_up: Optional[bool] = None
    identity_up: Optional[bool] = None
    backend_up: Optional[bool] = None
    prometheus_up: Optional[bool] = None
    grafana_endpoint_up: Optional[bool] = None


class InfrastructurePublic(BaseModel):
    systems: SystemsPublic = Field(default_factory=SystemsPublic)
    validators: ValidatorsInfraPublic = Field(default_factory=ValidatorsInfraPublic)
    rpc: RpcInfraPublic = Field(default_factory=RpcInfraPublic)
    services: ServicesPublic = Field(default_factory=ServicesPublic)


class InfrastructureResponse(BaseModel):
    """Normalized public infrastructure availability view."""

    health: PublicHealth
    freshness: FreshnessPublic
    infrastructure: InfrastructurePublic


class ChainMetricsPublic(BaseModel):
    """Aggregate live chain metrics for the configured recent window."""

    chain_id: Optional[int] = None
    block_number: Optional[int] = None
    latest_block_age_seconds: Optional[float] = None
    average_block_time_seconds: Optional[float] = None
    recent_block_count: int
    transactions_recent_blocks: Optional[int] = None
    average_gas_utilization_percent: Optional[float] = None


class RpcNodePublic(BaseModel):
    """State of the queried public RPC node (not overall chain health)."""

    reachable: bool
    syncing: Optional[bool] = None
    peer_count: Optional[int] = None
    client_version: Optional[str] = None


class RecentBlockPublic(BaseModel):
    number: int
    timestamp: str
    age_seconds: float
    proposer: str
    transaction_count: int
    gas_used: int
    gas_limit: int
    gas_utilization_percent: Optional[float] = None
    interval_seconds: Optional[float] = None


class ChainResponse(BaseModel):
    """Live public chain view derived from JSON-RPC."""

    health: PublicHealth
    updated_at: str
    chain: ChainMetricsPublic
    rpc: RpcNodePublic
    recent_blocks: List[RecentBlockPublic] = Field(default_factory=list)


ObservabilityState = Literal["complete", "partial"]


class OverviewChainDomain(BaseModel):
    health: PublicHealth
    block_number: Optional[int] = None
    latest_block_age_seconds: Optional[float] = None
    average_block_time_seconds: Optional[float] = None


class OverviewConsensusDomain(BaseModel):
    health: PublicHealth
    freshness: FreshnessState
    validators: Optional[int] = None
    synced: Optional[int] = None
    quorum: Optional[int] = None
    has_quorum: Optional[bool] = None


class OverviewInfrastructureDomain(BaseModel):
    health: PublicHealth
    freshness: FreshnessState
    systems_up: Optional[int] = None
    systems_monitored: Optional[int] = None


class OverviewDomains(BaseModel):
    chain: OverviewChainDomain
    consensus: OverviewConsensusDomain
    infrastructure: OverviewInfrastructureDomain


class OverviewResponse(BaseModel):
    """Compact dashboard composition of chain, consensus, and infrastructure."""

    health: PublicHealth
    observability: ObservabilityState
    updated_at: str
    domains: OverviewDomains
