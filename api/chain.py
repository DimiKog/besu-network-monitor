"""Live chain collection and public health evaluation.

Queries the configured public Besu JSON-RPC endpoint. No snapshot I/O,
no QBFT-only methods, no internal validator access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from api.config import Settings
from api.models import (
    ChainMetricsPublic,
    ChainResponse,
    PublicHealth,
    RecentBlockPublic,
    RpcNodePublic,
)
from api.rpc import (
    ParsedBlock,
    RpcClient,
    RpcError,
    RpcParseError,
    RpcTransportError,
    gas_utilization_percent,
    parse_block,
    parse_block_number_tag,
    parse_client_version,
    parse_hex_quantity,
    parse_syncing,
)

logger = logging.getLogger(__name__)


def _utc_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def evaluate_chain_health(
    latest_block_age_seconds: float,
    *,
    healthy_age_seconds: int,
    critical_age_seconds: int,
    future_tolerance_seconds: int,
) -> PublicHealth:
    """Map latest block age to public chain health.

    - age materially in the future beyond tolerance → unknown
    - age within future tolerance (slightly negative) → healthy
    - age <= healthy_age_seconds → healthy
    - age <= critical_age_seconds → degraded
    - age > critical_age_seconds → critical
    """
    if latest_block_age_seconds < -float(future_tolerance_seconds):
        return "unknown"

    # Small allowed clock skew: treat as effectively current.
    age = max(0.0, latest_block_age_seconds)

    if age <= float(healthy_age_seconds):
        return "healthy"
    if age <= float(critical_age_seconds):
        return "degraded"
    return "critical"


def unknown_chain_response(
    *,
    now: datetime,
    recent_block_count: int,
    reachable: bool,
    syncing: Optional[bool] = None,
    peer_count: Optional[int] = None,
    client_version: Optional[str] = None,
    chain_id: Optional[int] = None,
) -> ChainResponse:
    """Predictable unknown response for unreachable/untrusted chain state."""
    return ChainResponse(
        health="unknown",
        updated_at=_utc_z(now),
        chain=ChainMetricsPublic(
            chain_id=chain_id,
            block_number=None,
            latest_block_age_seconds=None,
            average_block_time_seconds=None,
            recent_block_count=recent_block_count,
            transactions_recent_blocks=None,
            average_gas_utilization_percent=None,
        ),
        rpc=RpcNodePublic(
            reachable=reachable,
            syncing=syncing,
            peer_count=peer_count,
            client_version=client_version,
        ),
        recent_blocks=[],
    )


@dataclass(frozen=True)
class NodeSignals:
    chain_id: int
    block_number: int
    peer_count: int
    syncing: bool
    client_version: str


def fetch_node_signals(client: RpcClient) -> NodeSignals:
    """Batch-fetch head/node signals from the public RPC."""
    results = client.batch_call(
        (
            ("eth_chainId", []),
            ("eth_blockNumber", []),
            ("net_peerCount", []),
            ("eth_syncing", []),
            ("web3_clientVersion", []),
        )
    )
    if len(results) != 5:
        raise RpcParseError("unexpected node-signal batch size")

    return NodeSignals(
        chain_id=parse_hex_quantity(results[0], field="eth_chainId"),
        block_number=parse_hex_quantity(results[1], field="eth_blockNumber"),
        peer_count=parse_hex_quantity(results[2], field="net_peerCount"),
        syncing=parse_syncing(results[3]),
        client_version=parse_client_version(results[4]),
    )


def fetch_recent_blocks(
    client: RpcClient,
    *,
    latest_block: int,
    recent_block_count: int,
    rpc_batch_size: int,
) -> List[ParsedBlock]:
    """Fetch the recent window newest-first via chunked JSON-RPC batches.

    Requests ``min(recent_block_count, latest_block + 1)`` blocks ending at
    ``latest_block``. Calls are split into HTTP batches of at most
    ``rpc_batch_size`` methods so public gateways that fail partial items in
    very large single batches remain usable. Every requested block must still
    parse successfully — chunks do not loosen validation.
    """
    if recent_block_count < 1:
        raise RpcParseError("recent_block_count must be >= 1")
    if latest_block < 0:
        raise RpcParseError("latest_block must be >= 0")
    if rpc_batch_size < 1:
        raise RpcParseError("rpc_batch_size must be >= 1")

    count = min(recent_block_count, latest_block + 1)
    numbers = [latest_block - offset for offset in range(count)]

    blocks: List[ParsedBlock] = []
    for start in range(0, len(numbers), rpc_batch_size):
        chunk_numbers = numbers[start : start + rpc_batch_size]
        calls = [
            ("eth_getBlockByNumber", [parse_block_number_tag(number), False])
            for number in chunk_numbers
        ]
        results = client.batch_call(calls)
        if len(results) != len(chunk_numbers):
            raise RpcParseError("recent-block batch size mismatch")

        for expected_number, raw in zip(chunk_numbers, results):
            block = parse_block(raw)
            if block.number != expected_number:
                raise RpcParseError(
                    "block number in result does not match requested tag"
                )
            blocks.append(block)

    return blocks


def build_recent_block_views(
    blocks_newest_first: Sequence[ParsedBlock],
    *,
    now: datetime,
) -> List[RecentBlockPublic]:
    """Build public recent-block rows.

    ``interval_seconds`` is the time since the previous (older) block.
    The oldest block in the window therefore has ``interval_seconds=null``.
    """
    now_ts = now.astimezone(timezone.utc).timestamp()
    views: List[RecentBlockPublic] = []

    for index, block in enumerate(blocks_newest_first):
        age = now_ts - float(block.timestamp)
        utilization = gas_utilization_percent(block.gas_used, block.gas_limit)

        interval: Optional[float] = None
        if index + 1 < len(blocks_newest_first):
            older = blocks_newest_first[index + 1]
            interval = float(block.timestamp - older.timestamp)
            if interval < 0:
                raise RpcParseError("negative block interval in recent window")

        views.append(
            RecentBlockPublic(
                number=block.number,
                timestamp=_utc_z(datetime.fromtimestamp(block.timestamp, tz=timezone.utc)),
                age_seconds=age,
                proposer=block.proposer,
                transaction_count=block.transaction_count,
                gas_used=block.gas_used,
                gas_limit=block.gas_limit,
                gas_utilization_percent=utilization,
                interval_seconds=interval,
            )
        )
    return views


def average_block_time_seconds(recent_blocks: Sequence[RecentBlockPublic]) -> Optional[float]:
    intervals = [
        block.interval_seconds
        for block in recent_blocks
        if block.interval_seconds is not None
    ]
    if not intervals:
        return None
    return sum(intervals) / len(intervals)


def average_gas_utilization_percent(
    recent_blocks: Sequence[RecentBlockPublic],
) -> Optional[float]:
    values = [
        block.gas_utilization_percent
        for block in recent_blocks
        if block.gas_utilization_percent is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def build_chain_response(
    *,
    now: datetime,
    settings: Settings,
    node: NodeSignals,
    blocks: Sequence[ParsedBlock],
) -> ChainResponse:
    recent = build_recent_block_views(blocks, now=now)
    if not recent:
        raise RpcParseError("recent block window is empty")

    latest = recent[0]
    health = evaluate_chain_health(
        latest.age_seconds,
        healthy_age_seconds=settings.chain_healthy_age_seconds,
        critical_age_seconds=settings.chain_critical_age_seconds,
        future_tolerance_seconds=settings.future_tolerance_seconds,
    )

    tx_total = sum(block.transaction_count for block in recent)

    return ChainResponse(
        health=health,
        updated_at=_utc_z(now),
        chain=ChainMetricsPublic(
            chain_id=node.chain_id,
            block_number=latest.number,
            latest_block_age_seconds=latest.age_seconds,
            average_block_time_seconds=average_block_time_seconds(recent),
            recent_block_count=settings.recent_block_count,
            transactions_recent_blocks=tx_total,
            average_gas_utilization_percent=average_gas_utilization_percent(recent),
        ),
        rpc=RpcNodePublic(
            reachable=True,
            syncing=node.syncing,
            peer_count=node.peer_count,
            client_version=node.client_version,
        ),
        recent_blocks=list(recent),
    )


def collect_chain_response(
    *,
    settings: Settings,
    client: RpcClient,
    now: datetime,
) -> ChainResponse:
    """Collect live RPC state and build the public ``/api/v1/chain`` response.

    Never raises for expected RPC/parse failures; returns health=unknown instead.
    Unexpected exceptions should propagate as API/service failures (5xx).
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    try:
        node = fetch_node_signals(client)
    except RpcTransportError:
        logger.info("chain RPC unreachable while fetching node signals")
        return unknown_chain_response(
            now=now,
            recent_block_count=settings.recent_block_count,
            reachable=False,
        )
    except RpcError:
        logger.info("chain RPC node signals invalid/malformed")
        return unknown_chain_response(
            now=now,
            recent_block_count=settings.recent_block_count,
            reachable=True,
        )

    if node.chain_id != settings.expected_chain_id:
        logger.info(
            "chain ID mismatch: observed=%s expected=%s",
            node.chain_id,
            settings.expected_chain_id,
        )
        return unknown_chain_response(
            now=now,
            recent_block_count=settings.recent_block_count,
            reachable=True,
            syncing=node.syncing,
            peer_count=node.peer_count,
            client_version=node.client_version,
            chain_id=node.chain_id,
        )

    try:
        blocks = fetch_recent_blocks(
            client,
            latest_block=node.block_number,
            recent_block_count=settings.recent_block_count,
            rpc_batch_size=settings.rpc_batch_size,
        )
        return build_chain_response(
            now=now,
            settings=settings,
            node=node,
            blocks=blocks,
        )
    except RpcTransportError:
        logger.info("chain RPC unreachable while fetching recent blocks")
        return unknown_chain_response(
            now=now,
            recent_block_count=settings.recent_block_count,
            reachable=False,
            syncing=node.syncing,
            peer_count=node.peer_count,
            client_version=node.client_version,
            chain_id=node.chain_id,
        )
    except RpcError:
        logger.info("chain recent-block window invalid/malformed")
        return unknown_chain_response(
            now=now,
            recent_block_count=settings.recent_block_count,
            reachable=True,
            syncing=node.syncing,
            peer_count=node.peer_count,
            client_version=node.client_version,
            chain_id=node.chain_id,
        )
