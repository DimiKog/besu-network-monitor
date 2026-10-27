"""Dashboard overview composition from existing domain evaluators.

Overall health aggregation is pure and independently testable. Domain health,
freshness, and metrics come exclusively from:

- ``collect_chain_response`` (live JSON-RPC)
- ``evaluate_snapshot_payload`` / ``unknown_consensus_response`` (consensus snapshot)
- ``evaluate_infrastructure_payload`` / ``unknown_infrastructure_response``

This module does not re-implement chain, consensus, or infrastructure health
rules, does not query Prometheus or QBFT, and never includes ``recent_blocks``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from api.chain import collect_chain_response
from api.config import Settings
from api.evaluation import evaluate_snapshot_payload, unknown_consensus_response
from api.infrastructure import (
    evaluate_infrastructure_payload,
    unknown_infrastructure_response,
)
from api.models import (
    ChainResponse,
    ConsensusResponse,
    InfrastructureResponse,
    OverviewChainDomain,
    OverviewConsensusDomain,
    OverviewDomains,
    OverviewInfrastructureDomain,
    OverviewResponse,
    PublicHealth,
)
from api.rpc import RpcClient
from api.snapshot import (
    try_load_consensus_snapshot,
    try_load_infrastructure_snapshot,
)

Observability = Literal["complete", "partial"]


def aggregate_overall_health(
    chain: PublicHealth,
    consensus: PublicHealth,
    infrastructure: PublicHealth,
) -> PublicHealth:
    """Compose overall dashboard health from domain health values.

    Chain is primary. Unknown secondary domains do not downgrade a healthy
    live chain by themselves.
    """
    if chain == "critical":
        return "critical"
    if chain == "unknown":
        return "unknown"
    if chain == "degraded":
        if consensus == "critical" or infrastructure == "critical":
            return "critical"
        return "degraded"
    if chain == "healthy":
        if consensus == "critical" or infrastructure == "critical":
            return "critical"
        if consensus == "degraded" or infrastructure == "degraded":
            return "degraded"
        return "healthy"
    return "unknown"


def observability_state(
    chain: PublicHealth,
    consensus: PublicHealth,
    infrastructure: PublicHealth,
) -> Observability:
    """complete when every domain health is known; partial if any is unknown."""
    if "unknown" in (chain, consensus, infrastructure):
        return "partial"
    return "complete"


def _utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_consensus_domain_response(
    settings: Settings,
    now: datetime,
) -> ConsensusResponse:
    """Same load + evaluate path as GET /api/v1/consensus."""
    payload, load_error = try_load_consensus_snapshot(settings.consensus_snapshot_path)
    if load_error is not None:
        return unknown_consensus_response(
            stale_after_seconds=settings.stale_after_seconds,
        )
    return evaluate_snapshot_payload(
        payload,
        now=now,
        stale_after_seconds=settings.stale_after_seconds,
        future_tolerance_seconds=settings.future_tolerance_seconds,
    )


def load_infrastructure_domain_response(
    settings: Settings,
    now: datetime,
) -> InfrastructureResponse:
    """Same load + evaluate path as GET /api/v1/infrastructure."""
    payload, load_error = try_load_infrastructure_snapshot(
        settings.infrastructure_snapshot_path
    )
    if load_error is not None:
        return unknown_infrastructure_response(
            stale_after_seconds=settings.stale_after_seconds,
        )
    return evaluate_infrastructure_payload(
        payload,
        now=now,
        stale_after_seconds=settings.stale_after_seconds,
        future_tolerance_seconds=settings.future_tolerance_seconds,
    )


def summarize_chain(response: ChainResponse) -> OverviewChainDomain:
    return OverviewChainDomain(
        health=response.health,
        block_number=response.chain.block_number,
        latest_block_age_seconds=response.chain.latest_block_age_seconds,
        average_block_time_seconds=response.chain.average_block_time_seconds,
    )


def summarize_consensus(response: ConsensusResponse) -> OverviewConsensusDomain:
    return OverviewConsensusDomain(
        health=response.health,
        freshness=response.freshness.state,
        validators=response.consensus.validator_count,
        synced=response.consensus.synced_validators,
        quorum=response.consensus.quorum_required,
        has_quorum=response.consensus.has_quorum,
    )


def summarize_infrastructure(
    response: InfrastructureResponse,
) -> OverviewInfrastructureDomain:
    return OverviewInfrastructureDomain(
        health=response.health,
        freshness=response.freshness.state,
        systems_up=response.infrastructure.systems.up,
        systems_monitored=response.infrastructure.systems.monitored,
    )


def build_overview_response(
    *,
    settings: Settings,
    client: RpcClient,
    now: datetime,
) -> OverviewResponse:
    """Collect domain responses and compose the compact overview payload."""
    chain_response = collect_chain_response(
        settings=settings, client=client, now=now
    )
    consensus_response = load_consensus_domain_response(settings, now)
    infrastructure_response = load_infrastructure_domain_response(settings, now)

    chain_health = chain_response.health
    consensus_health = consensus_response.health
    infrastructure_health = infrastructure_response.health

    return OverviewResponse(
        health=aggregate_overall_health(
            chain_health, consensus_health, infrastructure_health
        ),
        observability=observability_state(
            chain_health, consensus_health, infrastructure_health
        ),
        updated_at=_utc_z(now),
        domains=OverviewDomains(
            chain=summarize_chain(chain_response),
            consensus=summarize_consensus(consensus_response),
            infrastructure=summarize_infrastructure(infrastructure_response),
        ),
    )
