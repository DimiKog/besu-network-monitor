#!/usr/bin/env python3
"""Tests for GET /api/v1/overview and overall-health aggregation."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.config import Settings  # noqa: E402
from api.main import create_app  # noqa: E402
from api.overview import (  # noqa: E402
    aggregate_overall_health,
    observability_state,
)
from api.rpc import RpcParseError, RpcTransportError  # noqa: E402


def _now() -> datetime:
    return datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hex_qty(value: int) -> str:
    return hex(value)


PROPOSER = "0x000da85cc301d7b30c2722183b33cdb1676ce101"


def make_rpc_block(
    number: int,
    timestamp: int,
    *,
    tx_count: int = 0,
    gas_used: int = 21_000,
    gas_limit: int = 10_000_000,
) -> dict:
    return {
        "number": _hex_qty(number),
        "timestamp": _hex_qty(timestamp),
        "miner": PROPOSER,
        "gasUsed": _hex_qty(gas_used),
        "gasLimit": _hex_qty(gas_limit),
        "transactions": [f"0x{index:064x}" for index in range(tx_count)],
    }


class FakeRpcClient:
    def __init__(
        self,
        *,
        chain_id: int = 424242,
        latest: int = 1000,
        peer_count: int = 25,
        syncing: Any = False,
        client_version: str = "besu/v24.1.0",
        blocks: Optional[Dict[int, Any]] = None,
        transport_error_on: Optional[str] = None,
    ) -> None:
        self.chain_id = chain_id
        self.latest = latest
        self.peer_count = peer_count
        self.syncing = syncing
        self.client_version = client_version
        self.blocks = blocks if blocks is not None else {}
        self.transport_error_on = transport_error_on
        self.calls: List[Tuple[str, List[Any]]] = []

    def batch_call(self, calls: Sequence[Tuple[str, List[Any]]]) -> List[Any]:
        self.calls.extend(list(calls))
        methods = [method for method, _ in calls]
        if self.transport_error_on == "any":
            raise RpcTransportError("unreachable")
        if self.transport_error_on == "node" and "eth_chainId" in methods:
            raise RpcTransportError("unreachable")
        results: List[Any] = []
        for method, params in calls:
            if method == "eth_chainId":
                results.append(_hex_qty(self.chain_id))
            elif method == "eth_blockNumber":
                results.append(_hex_qty(self.latest))
            elif method == "net_peerCount":
                results.append(_hex_qty(self.peer_count))
            elif method == "eth_syncing":
                results.append(self.syncing)
            elif method == "web3_clientVersion":
                results.append(self.client_version)
            elif method == "eth_getBlockByNumber":
                number = int(params[0], 16)
                results.append(self.blocks.get(number))
            else:
                raise RpcParseError(f"unexpected method {method}")
        return results


def _blocks_for_age(latest_age_seconds: float, *, latest: int = 1000, count: int = 5):
    latest_ts = int(_now().timestamp() - latest_age_seconds)
    blocks: Dict[int, dict] = {}
    for offset in range(count):
        number = latest - offset
        ts = latest_ts - (offset * 5)
        blocks[number] = make_rpc_block(number, ts, tx_count=1)
    return blocks


def valid_consensus(**overrides: object) -> dict:
    base = {
        "schema_version": "1.0",
        "generated_at": _iso(_now() - timedelta(minutes=2)),
        "status": "ok",
        "reference_block": 8162213,
        "validator_count": 7,
        "synced_validators": 7,
        "quorum_required": 5,
        "has_quorum": True,
        "drift": {"observed_max_blocks": 1, "allowed_max_blocks": 2},
        "out_of_sync_count": 0,
    }
    base.update(overrides)
    return base


def valid_infrastructure(**overrides: object) -> dict:
    base = {
        "schema_version": 1,
        "generated_at": _iso(_now() - timedelta(minutes=2)),
        "status": "healthy",
        "systems": {"monitored": 16, "up": 16, "down": 0},
        "validators": {"monitored": 7, "up": 7, "down": 0},
        "rpc": {"primary_up": True, "backup_up": True},
        "services": {
            "bootnode_up": True,
            "block_explorer_endpoint_up": True,
            "faucet_up": True,
            "identity_up": True,
            "backend_up": True,
            "prometheus_up": True,
            "grafana_endpoint_up": True,
        },
    }
    base.update(overrides)
    return base


def infra_degraded_backup() -> dict:
    snap = valid_infrastructure(status="degraded")
    snap["rpc"] = {"primary_up": True, "backup_up": False}
    snap["systems"] = {"monitored": 16, "up": 15, "down": 1}
    return snap


def infra_critical_primary() -> dict:
    snap = valid_infrastructure(status="critical")
    snap["rpc"] = {"primary_up": False, "backup_up": True}
    snap["systems"] = {"monitored": 16, "up": 15, "down": 1}
    return snap


SENSITIVE_KEYS = {
    "status",
    "source_status",
    "ip",
    "hostname",
    "address",
    "ssh",
    "path",
    "exception",
    "traceback",
    "stack",
    "raw_response",
    "validator_addresses",
    "alias",
    "error",
    "promql",
    "job",
    "instance_name",
    "recent_blocks",
}


def _assert_no_sensitive_keys(obj: object, path: str = "$") -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            lower = str(key).lower()
            if lower in SENSITIVE_KEYS or lower.endswith("_path"):
                raise AssertionError(f"sensitive key {key!r} at {path}")
            _assert_no_sensitive_keys(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _assert_no_sensitive_keys(item, f"{path}[{index}]")


class AggregateOverallHealthTests(unittest.TestCase):
    """Pure overall-health policy tests (independent of HTTP / I/O)."""

    def test_matrix_examples(self) -> None:
        cases = [
            (("healthy", "healthy", "healthy"), "healthy"),
            (("healthy", "unknown", "healthy"), "healthy"),
            (("healthy", "healthy", "unknown"), "healthy"),
            (("healthy", "unknown", "unknown"), "healthy"),
            (("healthy", "degraded", "healthy"), "degraded"),
            (("healthy", "healthy", "degraded"), "degraded"),
            (("healthy", "critical", "healthy"), "critical"),
            (("healthy", "healthy", "critical"), "critical"),
            (("degraded", "unknown", "healthy"), "degraded"),
            (("degraded", "critical", "healthy"), "critical"),
            (("degraded", "healthy", "healthy"), "degraded"),
            (("unknown", "healthy", "healthy"), "unknown"),
            (("critical", "unknown", "unknown"), "critical"),
        ]
        for (chain, consensus, infra), expected in cases:
            with self.subTest(chain=chain, consensus=consensus, infra=infra):
                self.assertEqual(
                    aggregate_overall_health(chain, consensus, infra),
                    expected,
                )

    def test_observability_complete_and_partial(self) -> None:
        self.assertEqual(
            observability_state("healthy", "healthy", "healthy"),
            "complete",
        )
        self.assertEqual(
            observability_state("healthy", "unknown", "healthy"),
            "partial",
        )
        self.assertEqual(
            observability_state("critical", "unknown", "unknown"),
            "partial",
        )
        self.assertEqual(
            observability_state("degraded", "degraded", "critical"),
            "complete",
        )


class OverviewEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.consensus_path = root / "consensus_snapshot.json"
        self.infra_path = root / "infrastructure_snapshot.json"
        self.settings = Settings(
            consensus_snapshot_path=self.consensus_path,
            infrastructure_snapshot_path=self.infra_path,
            stale_after_seconds=900,
            future_tolerance_seconds=60,
            rpc_url="http://rpc.test/rpc/",
            expected_chain_id=424242,
            recent_block_count=5,
            chain_healthy_age_seconds=15,
            chain_critical_age_seconds=30,
            rpc_timeout_seconds=2.0,
            rpc_batch_size=25,
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_json(self, path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _client(
        self,
        *,
        chain_age: float = 2.0,
        transport_error: bool = False,
        consensus: Optional[dict] = None,
        infrastructure: Optional[dict] = None,
        omit_consensus: bool = False,
        omit_infrastructure: bool = False,
    ) -> TestClient:
        if not omit_consensus:
            self._write_json(
                self.consensus_path,
                consensus if consensus is not None else valid_consensus(),
            )
        if not omit_infrastructure:
            self._write_json(
                self.infra_path,
                infrastructure
                if infrastructure is not None
                else valid_infrastructure(),
            )

        if transport_error:
            rpc = FakeRpcClient(transport_error_on="any")
        else:
            rpc = FakeRpcClient(
                latest=1000,
                blocks=_blocks_for_age(chain_age, latest=1000, count=5),
            )
        app = create_app(self.settings, clock=_now, rpc_client=rpc)
        return TestClient(app)

    def test_response_schema_healthy_complete(self) -> None:
        client = self._client(chain_age=2.0)
        response = client.get("/api/v1/overview")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body.keys()),
            {"health", "observability", "updated_at", "domains"},
        )
        self.assertEqual(
            set(body["domains"].keys()),
            {"chain", "consensus", "infrastructure"},
        )
        self.assertEqual(
            set(body["domains"]["chain"].keys()),
            {
                "health",
                "block_number",
                "latest_block_age_seconds",
                "average_block_time_seconds",
            },
        )
        self.assertEqual(
            set(body["domains"]["consensus"].keys()),
            {
                "health",
                "freshness",
                "validators",
                "synced",
                "quorum",
                "has_quorum",
            },
        )
        self.assertEqual(
            set(body["domains"]["infrastructure"].keys()),
            {"health", "freshness", "systems_up", "systems_monitored"},
        )
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["observability"], "complete")
        self.assertEqual(body["updated_at"], _iso(_now()))
        self.assertEqual(body["domains"]["chain"]["health"], "healthy")
        self.assertEqual(body["domains"]["consensus"]["health"], "healthy")
        self.assertEqual(body["domains"]["infrastructure"]["health"], "healthy")
        self.assertEqual(body["domains"]["consensus"]["validators"], 7)
        self.assertEqual(body["domains"]["consensus"]["synced"], 7)
        self.assertEqual(body["domains"]["consensus"]["quorum"], 5)
        self.assertTrue(body["domains"]["consensus"]["has_quorum"])
        self.assertEqual(body["domains"]["infrastructure"]["systems_up"], 16)
        self.assertEqual(body["domains"]["infrastructure"]["systems_monitored"], 16)
        self.assertNotIn("recent_blocks", json.dumps(body))
        _assert_no_sensitive_keys(body)

    def test_healthy_unknown_healthy(self) -> None:
        client = self._client(omit_consensus=True)
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["observability"], "partial")
        self.assertEqual(body["domains"]["consensus"]["health"], "unknown")
        self.assertEqual(body["domains"]["chain"]["health"], "healthy")

    def test_healthy_healthy_unknown(self) -> None:
        client = self._client(omit_infrastructure=True)
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["observability"], "partial")
        self.assertEqual(body["domains"]["infrastructure"]["health"], "unknown")

    def test_healthy_unknown_unknown(self) -> None:
        client = self._client(omit_consensus=True, omit_infrastructure=True)
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["observability"], "partial")

    def test_healthy_degraded_healthy(self) -> None:
        client = self._client(
            consensus=valid_consensus(
                status="warning",
                synced_validators=6,
                out_of_sync_count=1,
            )
        )
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "degraded")
        self.assertEqual(body["observability"], "complete")
        self.assertEqual(body["domains"]["consensus"]["health"], "degraded")

    def test_healthy_healthy_degraded(self) -> None:
        client = self._client(infrastructure=infra_degraded_backup())
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "degraded")
        self.assertEqual(body["domains"]["infrastructure"]["health"], "degraded")
        self.assertEqual(body["domains"]["infrastructure"]["systems_up"], 15)

    def test_healthy_critical_healthy(self) -> None:
        client = self._client(
            consensus=valid_consensus(
                status="critical",
                synced_validators=3,
                has_quorum=False,
                out_of_sync_count=4,
            )
        )
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "critical")
        self.assertEqual(body["domains"]["consensus"]["health"], "critical")

    def test_healthy_healthy_critical(self) -> None:
        client = self._client(infrastructure=infra_critical_primary())
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "critical")
        self.assertEqual(body["domains"]["infrastructure"]["health"], "critical")

    def test_degraded_healthy_healthy(self) -> None:
        client = self._client(chain_age=20.0)
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["domains"]["chain"]["health"], "degraded")
        self.assertEqual(body["health"], "degraded")
        self.assertEqual(body["observability"], "complete")

    def test_degraded_unknown_healthy(self) -> None:
        client = self._client(chain_age=20.0, omit_consensus=True)
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "degraded")
        self.assertEqual(body["observability"], "partial")

    def test_degraded_critical_healthy(self) -> None:
        client = self._client(
            chain_age=20.0,
            consensus=valid_consensus(
                status="critical",
                synced_validators=3,
                has_quorum=False,
                out_of_sync_count=4,
            ),
        )
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["health"], "critical")

    def test_unknown_healthy_healthy(self) -> None:
        client = self._client(transport_error=True)
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["domains"]["chain"]["health"], "unknown")
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["observability"], "partial")

    def test_critical_unknown_unknown(self) -> None:
        client = self._client(
            chain_age=60.0,
            omit_consensus=True,
            omit_infrastructure=True,
        )
        body = client.get("/api/v1/overview").json()
        self.assertEqual(body["domains"]["chain"]["health"], "critical")
        self.assertEqual(body["health"], "critical")
        self.assertEqual(body["observability"], "partial")

    def test_unavailable_metrics_are_null(self) -> None:
        client = self._client(
            transport_error=True,
            omit_consensus=True,
            omit_infrastructure=True,
        )
        body = client.get("/api/v1/overview").json()
        self.assertIsNone(body["domains"]["chain"]["block_number"])
        self.assertIsNone(body["domains"]["chain"]["latest_block_age_seconds"])
        self.assertIsNone(body["domains"]["chain"]["average_block_time_seconds"])
        self.assertIsNone(body["domains"]["consensus"]["validators"])
        self.assertIsNone(body["domains"]["consensus"]["synced"])
        self.assertIsNone(body["domains"]["consensus"]["quorum"])
        self.assertIsNone(body["domains"]["consensus"]["has_quorum"])
        self.assertIsNone(body["domains"]["infrastructure"]["systems_up"])
        self.assertIsNone(body["domains"]["infrastructure"]["systems_monitored"])

    def test_overview_does_not_include_recent_blocks(self) -> None:
        client = self._client()
        body = client.get("/api/v1/overview").json()
        dumped = json.dumps(body)
        self.assertNotIn("recent_blocks", dumped)
        self.assertNotIn("proposer", dumped)
        self.assertNotIn("gas_used", dumped)
        # Domain /chain still returns recent_blocks; overview must not.
        chain_body = client.get("/api/v1/chain").json()
        self.assertIn("recent_blocks", chain_body)
        self.assertIsInstance(chain_body["recent_blocks"], list)
        self.assertGreater(len(chain_body["recent_blocks"]), 0)

    def test_http_200_for_all_overview_health_states(self) -> None:
        cases = [
            {"chain_age": 2.0},
            {"chain_age": 20.0},
            {"chain_age": 60.0, "omit_consensus": True},
            {"transport_error": True},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                client = self._client(**kwargs)
                response = client.get("/api/v1/overview")
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    response.json()["health"],
                    {"healthy", "degraded", "critical", "unknown"},
                )


if __name__ == "__main__":
    unittest.main()
