#!/usr/bin/env python3
"""Trusted collector: query local Prometheus and emit a sanitized infra snapshot.

Designed to run on the Prometheus monitoring VM. Read-only HTTP queries against
the Prometheus API (default http://127.0.0.1:9090). No SSH, no publisher, no API.

Stdlib only. Source labels, IPs, URLs, and raw Prometheus payloads never appear
in the sanitized output.

Inventory (exactly 16 systems):
  - 7 validators (each requires BOTH besu_validators_nginx AND node_exporter UP)
  - primary RPC, backup RPC
  - bootnode2, faucet, keycloak (identity), my-backend (backend)
  - Block Explorer, Grafana, Prometheus

Never derive systems.monitored by counting Prometheus scrape targets.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

# ============================================================
# CONFIGURATION (collector host only; never emitted in output)
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BASE_DIR / "infrastructure_snapshot.json"
DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
DEFAULT_TIMEOUT_SECONDS = 10.0

SCHEMA_VERSION = 1
EXPECTED_SYSTEMS = 16
EXPECTED_VALIDATORS = 7

VALIDATOR_NAMES: tuple[str, ...] = (
    "validator1",
    "validator2",
    "validator3",
    "validator4",
    "validator5",
    "validator6",
    "validator7",
)

# Internal Prometheus selectors. Never copy these strings into sanitized JSON.
JOB_VALIDATOR_BESU = "besu_validators_nginx"
JOB_VALIDATOR_NODE = "node_exporter"
JOB_PRIMARY_RPC = "blackbox_eth_blocknumber"
JOB_BACKUP_RPC = "besu_rpc_backup"
JOB_NODE_DIRECT = "node_exporter_direct"
JOB_BLOCK_EXPLORER = "blackbox_block_explorer"
JOB_GRAFANA = "blackbox_grafana"
JOB_PROMETHEUS = "prometheus"

BACKUP_RPC_INSTANCE = "rpc2"
BOOTNODE_INSTANCE = "bootnode2"
FAUCET_INSTANCE = "faucet"
IDENTITY_INSTANCE = "keycloak"
BACKEND_INSTANCE = "my-backend"


class CollectorError(Exception):
    """Collection or sanitization failed; previous snapshot must be preserved."""


PrometheusTransport = Callable[[str, float], Any]
# (promql, timeout_seconds) -> decoded Prometheus data.result (list)


# ============================================================
# TIME / ATOMIC OUTPUT
# ============================================================


def utc_now_z() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON via temp file + fsync + os.replace. No partial final file."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ============================================================
# PROMETHEUS HTTP
# ============================================================


def default_prometheus_transport(
    base_url: str,
) -> PrometheusTransport:
    """Build a transport that GETs /api/v1/query on the given Prometheus base URL."""

    root = base_url.rstrip("/")

    def _transport(promql: str, timeout_seconds: float) -> Any:
        query = urllib.parse.urlencode({"query": promql})
        url = f"{root}/api/v1/query?{query}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                status = getattr(response, "status", None) or response.getcode()
                if status != 200:
                    raise CollectorError(
                        f"Prometheus HTTP status {status}"
                    )
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise CollectorError(
                f"Prometheus HTTP error: {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise CollectorError("Prometheus unreachable") from exc
        except TimeoutError as exc:
            raise CollectorError("Prometheus query timed out") from exc
        except OSError as exc:
            raise CollectorError("Prometheus unreachable") from exc

        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CollectorError("Prometheus response is not JSON") from exc

        return extract_prometheus_result(body)

    return _transport


def extract_prometheus_result(body: Any) -> list[dict[str, Any]]:
    """Validate Prometheus instant-query envelope and return data.result."""
    if not isinstance(body, dict):
        raise CollectorError("Prometheus response must be a JSON object")
    if body.get("status") != "success":
        raise CollectorError("Prometheus API status is not success")
    data = body.get("data")
    if not isinstance(data, dict):
        raise CollectorError("Prometheus response missing data object")
    result_type = data.get("resultType")
    if result_type != "vector":
        raise CollectorError(
            f"unexpected Prometheus resultType: {result_type!r}"
        )
    result = data.get("result")
    if not isinstance(result, list):
        raise CollectorError("Prometheus data.result must be a list")
    return result


def parse_up_sample(sample: Mapping[str, Any]) -> tuple[dict[str, str], float]:
    """Return (metric_labels, up_value) from one instant-vector sample."""
    if not isinstance(sample, dict):
        raise CollectorError("Prometheus sample must be an object")
    metric = sample.get("metric")
    if not isinstance(metric, dict):
        raise CollectorError("Prometheus sample missing metric labels")
    labels = {str(key): str(value) for key, value in metric.items()}

    value = sample.get("value")
    if not isinstance(value, list) or len(value) != 2:
        raise CollectorError("Prometheus sample value must be [ts, number]")
    raw = value[1]
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise CollectorError("invalid Prometheus sample value type")
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise CollectorError("invalid Prometheus sample value") from exc
    if number not in (0.0, 1.0):
        raise CollectorError(
            f"Prometheus up value must be 0 or 1, got {number}"
        )
    return labels, number


def is_up(value: float) -> bool:
    return value == 1.0


# ============================================================
# INVENTORY EXTRACTION (strict)
# ============================================================


@dataclass(frozen=True)
class InfrastructureState:
    """Complete, trustworthy inventory state (all 16 systems classified)."""

    validator_up: tuple[bool, ...]  # length 7, order matches VALIDATOR_NAMES
    primary_rpc_up: bool
    backup_rpc_up: bool
    bootnode_up: bool
    block_explorer_up: bool
    faucet_up: bool
    identity_up: bool
    backend_up: bool
    prometheus_up: bool
    grafana_up: bool

    def validators_up_count(self) -> int:
        return sum(1 for flag in self.validator_up if flag)

    def systems_up_flags(self) -> list[bool]:
        """Flat list of the 16 monitored systems (validators first)."""
        return [
            *self.validator_up,
            self.primary_rpc_up,
            self.backup_rpc_up,
            self.bootnode_up,
            self.block_explorer_up,
            self.faucet_up,
            self.identity_up,
            self.backend_up,
            self.prometheus_up,
            self.grafana_up,
        ]


def _require_instance_name(labels: Mapping[str, str]) -> str:
    name = labels.get("instance_name")
    if not isinstance(name, str) or not name.strip():
        raise CollectorError("series missing instance_name label")
    return name.strip()


def _index_by_instance_name(
    samples: Sequence[Mapping[str, Any]],
    *,
    expected_names: Sequence[str],
    context: str,
) -> dict[str, float]:
    """Map instance_name -> up value. Exact expected set; reject extras/dupes."""
    expected = set(expected_names)
    found: dict[str, float] = {}
    for sample in samples:
        labels, value = parse_up_sample(sample)
        name = _require_instance_name(labels)
        if name not in expected:
            raise CollectorError(
                f"unexpected {context} instance_name"
            )
        if name in found:
            raise CollectorError(
                f"duplicate {context} series"
            )
        found[name] = value

    missing = expected - set(found)
    if missing:
        raise CollectorError(
            f"missing {context} series"
        )
    if len(found) != len(expected_names):
        raise CollectorError(
            f"incomplete {context} series set"
        )
    return found


def _require_single_up(
    samples: Sequence[Mapping[str, Any]],
    *,
    context: str,
) -> bool:
    """Require exactly one series; return whether it is UP."""
    if len(samples) == 0:
        raise CollectorError(f"missing {context} series")
    if len(samples) > 1:
        raise CollectorError(f"duplicate {context} series")
    _labels, value = parse_up_sample(samples[0])
    return is_up(value)


def _require_named_up(
    samples: Sequence[Mapping[str, Any]],
    *,
    instance_name: str,
    context: str,
) -> bool:
    indexed = _index_by_instance_name(
        samples,
        expected_names=(instance_name,),
        context=context,
    )
    return is_up(indexed[instance_name])


def query_up(
    transport: PrometheusTransport,
    promql: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    result = transport(promql, timeout_seconds)
    if not isinstance(result, list):
        raise CollectorError("Prometheus transport returned non-list result")
    return result


def collect_infrastructure_state(
    transport: PrometheusTransport,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> InfrastructureState:
    """Query Prometheus and build a complete inventory state.

    Raises CollectorError on unreachable/malformed/incomplete/ambiguous data.
    Missing telemetry is never treated as UP.
    """
    if timeout_seconds <= 0:
        raise CollectorError("timeout_seconds must be > 0")

    besu_samples = query_up(
        transport,
        f'up{{job="{JOB_VALIDATOR_BESU}"}}',
        timeout_seconds,
    )
    node_samples = query_up(
        transport,
        f'up{{job="{JOB_VALIDATOR_NODE}"}}',
        timeout_seconds,
    )
    primary_samples = query_up(
        transport,
        f'up{{job="{JOB_PRIMARY_RPC}"}}',
        timeout_seconds,
    )
    backup_samples = query_up(
        transport,
        f'up{{job="{JOB_BACKUP_RPC}"}}',
        timeout_seconds,
    )
    direct_samples = query_up(
        transport,
        f'up{{job="{JOB_NODE_DIRECT}"}}',
        timeout_seconds,
    )
    explorer_samples = query_up(
        transport,
        f'up{{job="{JOB_BLOCK_EXPLORER}"}}',
        timeout_seconds,
    )
    grafana_samples = query_up(
        transport,
        f'up{{job="{JOB_GRAFANA}"}}',
        timeout_seconds,
    )
    prometheus_samples = query_up(
        transport,
        f'up{{job="{JOB_PROMETHEUS}"}}',
        timeout_seconds,
    )

    besu_by_name = _index_by_instance_name(
        besu_samples,
        expected_names=VALIDATOR_NAMES,
        context="validator besu",
    )
    node_by_name = _index_by_instance_name(
        node_samples,
        expected_names=VALIDATOR_NAMES,
        context="validator node_exporter",
    )

    validator_up: list[bool] = []
    for name in VALIDATOR_NAMES:
        # One system: both telemetry sources must be UP.
        validator_up.append(
            is_up(besu_by_name[name]) and is_up(node_by_name[name])
        )

    direct_expected = (
        BOOTNODE_INSTANCE,
        FAUCET_INSTANCE,
        IDENTITY_INSTANCE,
        BACKEND_INSTANCE,
    )
    direct_by_name = _index_by_instance_name(
        direct_samples,
        expected_names=direct_expected,
        context="node_exporter_direct",
    )

    return InfrastructureState(
        validator_up=tuple(validator_up),
        primary_rpc_up=_require_single_up(
            primary_samples, context="primary RPC"
        ),
        backup_rpc_up=_require_named_up(
            backup_samples,
            instance_name=BACKUP_RPC_INSTANCE,
            context="backup RPC",
        ),
        bootnode_up=is_up(direct_by_name[BOOTNODE_INSTANCE]),
        block_explorer_up=_require_single_up(
            explorer_samples, context="block explorer"
        ),
        faucet_up=is_up(direct_by_name[FAUCET_INSTANCE]),
        identity_up=is_up(direct_by_name[IDENTITY_INSTANCE]),
        backend_up=is_up(direct_by_name[BACKEND_INSTANCE]),
        prometheus_up=_require_single_up(
            prometheus_samples, context="prometheus"
        ),
        grafana_up=_require_single_up(
            grafana_samples, context="grafana"
        ),
    )


# ============================================================
# HEALTH POLICY (isolated; no QBFT / consensus quorum logic)
# ============================================================


def evaluate_infrastructure_status(state: InfrastructureState) -> str:
    """Map a complete inventory state to healthy|degraded|critical.

    Policy (infrastructure only; does not consider QBFT quorum):
    - healthy: all 16 systems UP
    - critical: primary RPC unavailable (clearly critical public dependency)
    - degraded: any other unavailable system(s) (reduced redundancy /
      non-critical loss), including a single validator infrastructure system,
      backup RPC, Grafana, Block Explorer, or other core/public services

    Incomplete telemetry never reaches this function; callers must fail closed
    with CollectorError before evaluation.
    """
    flags = state.systems_up_flags()
    if len(flags) != EXPECTED_SYSTEMS:
        raise CollectorError("internal inventory size mismatch")

    if all(flags):
        return "healthy"

    # Primary RPC is the minimum clearly-critical dependency for this phase.
    if not state.primary_rpc_up:
        return "critical"

    return "degraded"


def build_unknown_snapshot(*, generated_at: Optional[str] = None) -> dict[str, Any]:
    """Public-schema placeholder when telemetry cannot support classification.

    Booleans and up/down counts are null so callers never invent UP.
    The live collector does **not** write this file on failure (consistent with
    collect_consensus.py): incomplete collection raises CollectorError and
    preserves any previous snapshot. This helper documents/tests the schema.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_z(),
        "status": "unknown",
        "systems": {
            "monitored": EXPECTED_SYSTEMS,
            "up": None,
            "down": None,
        },
        "validators": {
            "monitored": EXPECTED_VALIDATORS,
            "up": None,
            "down": None,
        },
        "rpc": {
            "primary_up": None,
            "backup_up": None,
        },
        "services": {
            "bootnode_up": None,
            "block_explorer_endpoint_up": None,
            "faucet_up": None,
            "identity_up": None,
            "backend_up": None,
            "prometheus_up": None,
            "grafana_endpoint_up": None,
        },
    }


def sanitize_infrastructure_snapshot(
    state: InfrastructureState,
    *,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Reduce complete inventory state to the public-safe Monitor contract."""
    status = evaluate_infrastructure_status(state)
    flags = state.systems_up_flags()
    systems_up = sum(1 for flag in flags if flag)
    systems_down = EXPECTED_SYSTEMS - systems_up
    validators_up = state.validators_up_count()
    validators_down = EXPECTED_VALIDATORS - validators_up

    if systems_up + systems_down != EXPECTED_SYSTEMS:
        raise CollectorError("systems up/down do not sum to monitored")
    if validators_up + validators_down != EXPECTED_VALIDATORS:
        raise CollectorError("validators up/down do not sum to monitored")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at or utc_now_z(),
        "status": status,
        "systems": {
            "monitored": EXPECTED_SYSTEMS,
            "up": systems_up,
            "down": systems_down,
        },
        "validators": {
            "monitored": EXPECTED_VALIDATORS,
            "up": validators_up,
            "down": validators_down,
        },
        "rpc": {
            "primary_up": state.primary_rpc_up,
            "backup_up": state.backup_rpc_up,
        },
        "services": {
            "bootnode_up": state.bootnode_up,
            "block_explorer_endpoint_up": state.block_explorer_up,
            "faucet_up": state.faucet_up,
            "identity_up": state.identity_up,
            "backend_up": state.backend_up,
            "prometheus_up": state.prometheus_up,
            "grafana_endpoint_up": state.grafana_up,
        },
    }


def run_collection(
    transport: PrometheusTransport,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    generated_at: Optional[str] = None,
) -> dict[str, Any]:
    """Run full collection and return the sanitized public snapshot."""
    state = collect_infrastructure_state(
        transport,
        timeout_seconds=timeout_seconds,
    )
    return sanitize_infrastructure_snapshot(
        state,
        generated_at=generated_at,
    )


# ============================================================
# CLI
# ============================================================


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect infrastructure availability from local Prometheus "
            "and write a sanitized public-safe snapshot."
        )
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path(
            os.environ.get(
                "BESU_MONITOR_INFRA_SNAPSHOT",
                str(DEFAULT_OUTPUT),
            )
        ),
        help=(
            f"Output path (default: {DEFAULT_OUTPUT} or "
            "BESU_MONITOR_INFRA_SNAPSHOT)"
        ),
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.environ.get(
            "BESU_MONITOR_PROMETHEUS_URL",
            DEFAULT_PROMETHEUS_URL,
        ),
        help=(
            f"Prometheus base URL (default: {DEFAULT_PROMETHEUS_URL} or "
            "BESU_MONITOR_PROMETHEUS_URL)"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(
            os.environ.get(
                "BESU_MONITOR_PROMETHEUS_TIMEOUT",
                str(DEFAULT_TIMEOUT_SECONDS),
            )
        ),
        help=(
            f"HTTP timeout seconds (default: {DEFAULT_TIMEOUT_SECONDS} or "
            "BESU_MONITOR_PROMETHEUS_TIMEOUT)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        print("ERROR: --timeout must be > 0", file=sys.stderr)
        return 1

    transport = default_prometheus_transport(str(args.prometheus_url))

    print(
        "collect_infrastructure.py — querying Prometheus for "
        f"{EXPECTED_SYSTEMS} monitored systems "
        f"(validators counted once; dual telemetry required)",
        flush=True,
    )

    try:
        snapshot = run_collection(
            transport,
            timeout_seconds=args.timeout,
        )
        atomic_write_json(args.output, snapshot)
    except CollectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: failed to write output: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {args.output.resolve()}")
    print(
        f"status={snapshot['status']} "
        f"systems={snapshot['systems']['up']}/{snapshot['systems']['monitored']} "
        f"validators={snapshot['validators']['up']}/{snapshot['validators']['monitored']} "
        f"primary_rpc={snapshot['rpc']['primary_up']} "
        f"backup_rpc={snapshot['rpc']['backup_up']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
