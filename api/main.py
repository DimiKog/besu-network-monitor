"""Besu Network Monitor API — FastAPI application.

Endpoints implemented:

- GET /api/v1/health          — API service liveness only
- GET /api/v1/consensus       — public consensus health + freshness + metrics
- GET /api/v1/infrastructure  — public infrastructure health + freshness + metrics
- GET /api/v1/chain           — live public Besu JSON-RPC chain health + recent blocks
- GET /api/v1/overview        — compact dashboard composition of the three domains
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from api.chain import collect_chain_response
from api.config import SERVICE_NAME, Settings, get_settings
from api.models import (
    ChainResponse,
    ConsensusResponse,
    InfrastructureResponse,
    OverviewResponse,
    ServiceHealthResponse,
)
from api.overview import (
    build_overview_response,
    load_consensus_domain_response,
    load_infrastructure_domain_response,
)
from api.rpc import HttpxRpcClient, RpcClient

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

# Headers sufficient for normal browser GET fetches. Not an auth surface.
_CORS_ALLOW_HEADERS = ("Accept", "Content-Type")


def create_app(
    settings: Optional[Settings] = None,
    *,
    clock: Optional[Clock] = None,
    rpc_client: Optional[RpcClient] = None,
) -> FastAPI:
    """Application factory (testable via injected settings, clock, and RPC client)."""

    resolved_settings = settings if settings is not None else get_settings()
    resolved_clock: Clock = clock if clock is not None else (
        lambda: datetime.now(timezone.utc)
    )

    app = FastAPI(
        title="Besu Network Monitor API",
        version="0.1.0",
        description=(
            "Public read-only Monitor API. Service health is distinct from "
            "monitored-network / consensus / infrastructure / chain health."
        ),
    )
    app.state.settings = resolved_settings
    app.state.clock = resolved_clock
    app.state.rpc_client = rpc_client

    # Browser Origin allow-list only. Not authentication or API access control;
    # the API remains publicly reachable for non-browser clients.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=list(_CORS_ALLOW_HEADERS),
    )

    def _rpc_client_for(request: Request) -> RpcClient:
        client: RpcClient = request.app.state.rpc_client
        if client is None:
            cfg: Settings = request.app.state.settings
            client = HttpxRpcClient(
                url=cfg.rpc_url,
                timeout_seconds=cfg.rpc_timeout_seconds,
            )
        return client

    @app.get("/api/v1/health", response_model=ServiceHealthResponse)
    def health() -> ServiceHealthResponse:
        """Return whether this API process is alive.

        Does not reflect Besu network, consensus, infrastructure, or chain health.
        """
        return ServiceHealthResponse(status="ok", service=SERVICE_NAME)

    @app.get("/api/v1/consensus", response_model=ConsensusResponse)
    def consensus(request: Request) -> ConsensusResponse:
        """Return public-safe consensus health, freshness, and metrics.

        Always attempts to return HTTP 200 with ``health=unknown`` when
        telemetry is missing, stale, or invalid. Does not expose collector
        source ``status`` as Monitor health.
        """
        cfg: Settings = request.app.state.settings
        now: datetime = request.app.state.clock()
        return load_consensus_domain_response(cfg, now)

    @app.get("/api/v1/infrastructure", response_model=InfrastructureResponse)
    def infrastructure(request: Request) -> InfrastructureResponse:
        """Return public-safe infrastructure health, freshness, and metrics.

        Reads the sanitized trusted snapshot on disk. Does not query Prometheus
        and does not represent QBFT quorum. HTTP 200 with ``health=unknown``
        when telemetry is missing, stale, or invalid.
        """
        cfg: Settings = request.app.state.settings
        now: datetime = request.app.state.clock()
        return load_infrastructure_domain_response(cfg, now)

    @app.get("/api/v1/chain", response_model=ChainResponse)
    def chain(request: Request) -> ChainResponse:
        """Return live chain health and recent block activity via public JSON-RPC.

        peer_count and syncing describe the queried public RPC node, not the
        overall validator set. HTTP 200 with health=unknown is used when the
        chain state cannot be trusted.
        """
        cfg: Settings = request.app.state.settings
        now: datetime = request.app.state.clock()
        return collect_chain_response(
            settings=cfg, client=_rpc_client_for(request), now=now
        )

    @app.get("/api/v1/overview", response_model=OverviewResponse)
    def overview(request: Request) -> OverviewResponse:
        """Return a compact dashboard composition of chain/consensus/infrastructure.

        Reuses domain collectors/evaluators. Does not include recent_blocks or
        other detailed domain payloads. HTTP 200 for all valid health states.
        """
        cfg: Settings = request.app.state.settings
        now: datetime = request.app.state.clock()
        return build_overview_response(
            settings=cfg,
            client=_rpc_client_for(request),
            now=now,
        )

    return app


app = create_app()
