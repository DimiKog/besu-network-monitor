"""Runtime configuration for the Monitor API.

Environment variables (production defaults shown):

Consensus / infrastructure (snapshot-based):
- BESU_MONITOR_CONSENSUS_SNAPSHOT
    /var/lib/besu-monitor/current/consensus_snapshot.json
- BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT
    /var/lib/besu-monitor/current/infrastructure_snapshot.json
- BESU_MONITOR_STALE_AFTER_SECONDS
    900  (15 minutes; collectors run every 5 minutes)
- BESU_MONITOR_FUTURE_TOLERANCE_SECONDS
    60   (allowed clock skew for timestamps ahead of wall clock;
         used by consensus/infrastructure freshness and chain latest-block age)

Chain (live public JSON-RPC):
- BESU_MONITOR_RPC_URL
    https://rpc.dimikog.org/rpc/
- BESU_MONITOR_EXPECTED_CHAIN_ID
    424242
- BESU_MONITOR_RECENT_BLOCK_COUNT
    100
- BESU_MONITOR_CHAIN_HEALTHY_AGE_SECONDS
    15
- BESU_MONITOR_CHAIN_CRITICAL_AGE_SECONDS
    30
- BESU_MONITOR_RPC_TIMEOUT_SECONDS
    10
- BESU_MONITOR_RPC_BATCH_SIZE
    25   (max eth_getBlockByNumber calls per HTTP JSON-RPC batch)

Browser CORS (not authentication):
- BESU_MONITOR_CORS_ORIGINS
    https://monitor.dimikog.org
    Comma-separated allow-list for browser Origin. Production default is the
    official frontend only. Localhost is never included unless set explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Tuple

DEFAULT_CONSENSUS_SNAPSHOT = Path(
    "/var/lib/besu-monitor/current/consensus_snapshot.json"
)
DEFAULT_INFRASTRUCTURE_SNAPSHOT = Path(
    "/var/lib/besu-monitor/current/infrastructure_snapshot.json"
)
DEFAULT_STALE_AFTER_SECONDS = 900
DEFAULT_FUTURE_TOLERANCE_SECONDS = 60

DEFAULT_RPC_URL = "https://rpc.dimikog.org/rpc/"
DEFAULT_EXPECTED_CHAIN_ID = 424242
DEFAULT_RECENT_BLOCK_COUNT = 100
DEFAULT_CHAIN_HEALTHY_AGE_SECONDS = 15
DEFAULT_CHAIN_CRITICAL_AGE_SECONDS = 30
DEFAULT_RPC_TIMEOUT_SECONDS = 10.0
# Edu-Net's public gateway frequently returns partial per-item upstream errors
# inside very large single batches (e.g. 100). Smaller batches remain reliable.
DEFAULT_RPC_BATCH_SIZE = 25

# Official V1 frontend origin only. Not an API access-control boundary.
DEFAULT_CORS_ORIGINS: Tuple[str, ...] = ("https://monitor.dimikog.org",)

SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_INFRASTRUCTURE_SCHEMA_VERSION = 1
EXPECTED_INFRASTRUCTURE_SYSTEMS = 16
EXPECTED_INFRASTRUCTURE_VALIDATORS = 7
SERVICE_NAME = "besu-monitor-api"


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _float_env(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def parse_cors_origins(
    raw: str | None,
    *,
    default: Sequence[str] = DEFAULT_CORS_ORIGINS,
) -> Tuple[str, ...]:
    """Parse a comma-separated CORS origin allow-list.

    Empty / whitespace-only entries are ignored. If the variable is unset,
    blank, or yields no origins after trimming, ``default`` is used.
    """
    if raw is None or raw.strip() == "":
        return tuple(default)
    origins = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not origins:
        return tuple(default)
    return origins


@dataclass(frozen=True)
class Settings:
    """Immutable API settings loaded from the environment."""

    consensus_snapshot_path: Path
    stale_after_seconds: int
    future_tolerance_seconds: int
    infrastructure_snapshot_path: Path = DEFAULT_INFRASTRUCTURE_SNAPSHOT
    rpc_url: str = DEFAULT_RPC_URL
    expected_chain_id: int = DEFAULT_EXPECTED_CHAIN_ID
    recent_block_count: int = DEFAULT_RECENT_BLOCK_COUNT
    chain_healthy_age_seconds: int = DEFAULT_CHAIN_HEALTHY_AGE_SECONDS
    chain_critical_age_seconds: int = DEFAULT_CHAIN_CRITICAL_AGE_SECONDS
    rpc_timeout_seconds: float = DEFAULT_RPC_TIMEOUT_SECONDS
    rpc_batch_size: int = DEFAULT_RPC_BATCH_SIZE
    cors_origins: Tuple[str, ...] = DEFAULT_CORS_ORIGINS

    @classmethod
    def from_environ(cls) -> "Settings":
        raw_path = os.environ.get("BESU_MONITOR_CONSENSUS_SNAPSHOT")
        path = (
            Path(raw_path)
            if raw_path is not None and raw_path.strip() != ""
            else DEFAULT_CONSENSUS_SNAPSHOT
        )
        raw_infra = os.environ.get("BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT")
        infra_path = (
            Path(raw_infra)
            if raw_infra is not None and raw_infra.strip() != ""
            else DEFAULT_INFRASTRUCTURE_SNAPSHOT
        )
        raw_rpc = os.environ.get("BESU_MONITOR_RPC_URL")
        rpc_url = (
            raw_rpc.strip()
            if raw_rpc is not None and raw_rpc.strip() != ""
            else DEFAULT_RPC_URL
        )

        healthy = _int_env(
            "BESU_MONITOR_CHAIN_HEALTHY_AGE_SECONDS",
            DEFAULT_CHAIN_HEALTHY_AGE_SECONDS,
            minimum=0,
        )
        critical = _int_env(
            "BESU_MONITOR_CHAIN_CRITICAL_AGE_SECONDS",
            DEFAULT_CHAIN_CRITICAL_AGE_SECONDS,
            minimum=0,
        )
        if critical < healthy:
            raise ValueError(
                "BESU_MONITOR_CHAIN_CRITICAL_AGE_SECONDS must be >= "
                "BESU_MONITOR_CHAIN_HEALTHY_AGE_SECONDS"
            )

        return cls(
            consensus_snapshot_path=path,
            infrastructure_snapshot_path=infra_path,
            stale_after_seconds=_int_env(
                "BESU_MONITOR_STALE_AFTER_SECONDS",
                DEFAULT_STALE_AFTER_SECONDS,
                minimum=0,
            ),
            future_tolerance_seconds=_int_env(
                "BESU_MONITOR_FUTURE_TOLERANCE_SECONDS",
                DEFAULT_FUTURE_TOLERANCE_SECONDS,
                minimum=0,
            ),
            rpc_url=rpc_url,
            expected_chain_id=_int_env(
                "BESU_MONITOR_EXPECTED_CHAIN_ID",
                DEFAULT_EXPECTED_CHAIN_ID,
                minimum=1,
            ),
            recent_block_count=_int_env(
                "BESU_MONITOR_RECENT_BLOCK_COUNT",
                DEFAULT_RECENT_BLOCK_COUNT,
                minimum=1,
            ),
            chain_healthy_age_seconds=healthy,
            chain_critical_age_seconds=critical,
            rpc_timeout_seconds=_float_env(
                "BESU_MONITOR_RPC_TIMEOUT_SECONDS",
                DEFAULT_RPC_TIMEOUT_SECONDS,
                minimum=0.1,
            ),
            rpc_batch_size=_int_env(
                "BESU_MONITOR_RPC_BATCH_SIZE",
                DEFAULT_RPC_BATCH_SIZE,
                minimum=1,
            ),
            cors_origins=parse_cors_origins(
                os.environ.get("BESU_MONITOR_CORS_ORIGINS")
            ),
        )


def get_settings() -> Settings:
    """Load settings from the current process environment."""
    return Settings.from_environ()
