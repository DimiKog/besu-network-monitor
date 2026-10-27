"""Tests for live /api/v1/chain (mocked JSON-RPC; no Internet)."""

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

from api.chain import (  # noqa: E402
    average_block_time_seconds,
    build_recent_block_views,
    collect_chain_response,
    evaluate_chain_health,
)
from api.config import Settings  # noqa: E402
from api.main import create_app  # noqa: E402
from api.rpc import (  # noqa: E402
    ParsedBlock,
    RpcParseError,
    RpcTransportError,
    gas_utilization_percent,
    parse_block,
    parse_hex_quantity,
    parse_syncing,
)


def _now() -> datetime:
    return datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


PROPOSER = "0x000da85cc301d7b30c2722183b33cdb1676ce101"


def _hex_qty(value: int) -> str:
    return hex(value)


def make_rpc_block(
    number: int,
    timestamp: int,
    *,
    tx_count: int = 0,
    gas_used: int = 21_000,
    gas_limit: int = 10_000_000,
    miner: str = PROPOSER,
) -> dict:
    return {
        "number": _hex_qty(number),
        "timestamp": _hex_qty(timestamp),
        "miner": miner,
        "gasUsed": _hex_qty(gas_used),
        "gasLimit": _hex_qty(gas_limit),
        "transactions": [f"0x{index:064x}" for index in range(tx_count)],
    }


class FakeRpcClient:
    """Deterministic in-memory JSON-RPC batch client."""

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
        malformed_node: bool = False,
        extra_batch_item: bool = False,
    ) -> None:
        self.chain_id = chain_id
        self.latest = latest
        self.peer_count = peer_count
        self.syncing = syncing
        self.client_version = client_version
        self.blocks = blocks if blocks is not None else {}
        self.transport_error_on = transport_error_on
        self.malformed_node = malformed_node
        self.extra_batch_item = extra_batch_item
        self.calls: List[Tuple[str, List[Any]]] = []

    def batch_call(self, calls: Sequence[Tuple[str, List[Any]]]) -> List[Any]:
        self.calls.extend(list(calls))
        methods = [method for method, _ in calls]

        if self.transport_error_on == "any":
            raise RpcTransportError("unreachable")
        if self.transport_error_on == "node" and "eth_chainId" in methods:
            raise RpcTransportError("unreachable")
        if self.transport_error_on == "blocks" and "eth_getBlockByNumber" in methods:
            raise RpcTransportError("unreachable")

        results: List[Any] = []
        for method, params in calls:
            if method == "eth_chainId":
                if self.malformed_node:
                    results.append({"bad": True})
                else:
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

        if self.extra_batch_item and "eth_getBlockByNumber" in methods:
            results.append({"extra": True})
        return results


def _settings(**overrides: Any) -> Settings:
    base = dict(
        consensus_snapshot_path=Path("/tmp/unused-consensus.json"),
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
    base.update(overrides)
    return Settings(**base)


def _blocks_for_window(
    latest: int,
    count: int,
    *,
    latest_age_seconds: float,
    interval_seconds: int = 5,
    tx_per_block: int = 2,
    gas_used: int = 2_000_000,
    gas_limit: int = 10_000_000,
) -> Dict[int, dict]:
    latest_ts = int(_now().timestamp() - latest_age_seconds)
    blocks: Dict[int, dict] = {}
    for offset in range(count):
        number = latest - offset
        if number < 0:
            break
        ts = latest_ts - (offset * interval_seconds)
        blocks[number] = make_rpc_block(
            number,
            ts,
            tx_count=tx_per_block,
            gas_used=gas_used,
            gas_limit=gas_limit,
        )
    return blocks


class HealthEvaluationTests(unittest.TestCase):
    def test_healthy(self) -> None:
        self.assertEqual(
            evaluate_chain_health(
                5,
                healthy_age_seconds=15,
                critical_age_seconds=30,
                future_tolerance_seconds=60,
            ),
            "healthy",
        )

    def test_degraded(self) -> None:
        self.assertEqual(
            evaluate_chain_health(
                20,
                healthy_age_seconds=15,
                critical_age_seconds=30,
                future_tolerance_seconds=60,
            ),
            "degraded",
        )

    def test_critical(self) -> None:
        self.assertEqual(
            evaluate_chain_health(
                45,
                healthy_age_seconds=15,
                critical_age_seconds=30,
                future_tolerance_seconds=60,
            ),
            "critical",
        )

    def test_future_beyond_tolerance_unknown(self) -> None:
        self.assertEqual(
            evaluate_chain_health(
                -120,
                healthy_age_seconds=15,
                critical_age_seconds=30,
                future_tolerance_seconds=60,
            ),
            "unknown",
        )

    def test_small_future_skew_healthy(self) -> None:
        self.assertEqual(
            evaluate_chain_health(
                -30,
                healthy_age_seconds=15,
                critical_age_seconds=30,
                future_tolerance_seconds=60,
            ),
            "healthy",
        )


class RpcParsingTests(unittest.TestCase):
    def test_peer_count_hex_parsing(self) -> None:
        self.assertEqual(parse_hex_quantity("0x19", field="net_peerCount"), 25)

    def test_block_quantity_parsing(self) -> None:
        self.assertEqual(parse_hex_quantity("0x3e8", field="eth_blockNumber"), 1000)

    def test_rejects_non_hex_quantity(self) -> None:
        with self.assertRaises(RpcParseError):
            parse_hex_quantity(25, field="net_peerCount")

    def test_eth_syncing_false(self) -> None:
        self.assertFalse(parse_syncing(False))

    def test_eth_syncing_object(self) -> None:
        self.assertTrue(
            parse_syncing({"startingBlock": "0x1", "currentBlock": "0x2", "highestBlock": "0x3"})
        )

    def test_transaction_counting(self) -> None:
        block = parse_block(make_rpc_block(10, 1_700_000_000, tx_count=4))
        self.assertEqual(block.transaction_count, 4)

    def test_gas_utilization(self) -> None:
        self.assertEqual(gas_utilization_percent(2_500_000, 10_000_000), 25.0)

    def test_zero_gas_limit(self) -> None:
        self.assertIsNone(gas_utilization_percent(0, 0))
        block = parse_block(
            make_rpc_block(1, 1_700_000_000, gas_used=0, gas_limit=0)
        )
        views = build_recent_block_views([block], now=_now())
        self.assertIsNone(views[0].gas_utilization_percent)


class RecentWindowMathTests(unittest.TestCase):
    def test_intervals_and_average_newest_first(self) -> None:
        # newest first: 12, 11, 10 with 5s spacing
        blocks = [
            ParsedBlock(12, 1_000_010, PROPOSER, 1, 100, 1000),
            ParsedBlock(11, 1_000_005, PROPOSER, 1, 100, 1000),
            ParsedBlock(10, 1_000_000, PROPOSER, 1, 100, 1000),
        ]
        views = build_recent_block_views(blocks, now=_now())
        self.assertEqual([v.number for v in views], [12, 11, 10])
        self.assertEqual(views[0].interval_seconds, 5.0)
        self.assertEqual(views[1].interval_seconds, 5.0)
        self.assertIsNone(views[2].interval_seconds)  # oldest
        self.assertEqual(average_block_time_seconds(views), 5.0)


class ChainCollectTests(unittest.TestCase):
    def test_healthy_recent_chain(self) -> None:
        latest = 1000
        client = FakeRpcClient(
            latest=latest,
            syncing=False,
            blocks=_blocks_for_window(latest, 5, latest_age_seconds=5),
        )
        response = collect_chain_response(
            settings=_settings(),
            client=client,
            now=_now(),
        )
        self.assertEqual(response.health, "healthy")
        self.assertEqual(response.chain.block_number, latest)
        self.assertEqual(response.chain.recent_block_count, 5)
        self.assertEqual(response.chain.transactions_recent_blocks, 10)
        self.assertEqual(response.rpc.reachable, True)
        self.assertEqual(response.rpc.syncing, False)
        self.assertEqual(response.rpc.peer_count, 25)
        self.assertEqual(len(response.recent_blocks), 5)
        self.assertEqual(response.recent_blocks[0].number, latest)
        self.assertLess(response.recent_blocks[-1].number, latest)

    def test_degraded_latest_block_age(self) -> None:
        client = FakeRpcClient(
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=20),
        )
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "degraded")

    def test_critical_latest_block_age(self) -> None:
        client = FakeRpcClient(
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=45),
        )
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "critical")

    def test_rpc_unavailable_unknown(self) -> None:
        client = FakeRpcClient(transport_error_on="node")
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "unknown")
        self.assertFalse(response.rpc.reachable)
        self.assertEqual(response.recent_blocks, [])

    def test_malformed_json_rpc_unknown(self) -> None:
        client = FakeRpcClient(malformed_node=True)
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "unknown")
        self.assertTrue(response.rpc.reachable)
        self.assertIsNone(response.chain.block_number)

    def test_wrong_chain_id_unknown(self) -> None:
        client = FakeRpcClient(
            chain_id=1,
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=5),
        )
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "unknown")
        self.assertEqual(response.chain.chain_id, 1)
        self.assertIsNone(response.chain.block_number)
        self.assertEqual(response.recent_blocks, [])
        self.assertTrue(response.rpc.reachable)

    def test_future_latest_block_timestamp(self) -> None:
        client = FakeRpcClient(
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=-120),
        )
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "unknown")
        self.assertIsNotNone(response.chain.latest_block_age_seconds)
        self.assertLess(response.chain.latest_block_age_seconds, 0)

    def test_partial_malformed_batch_unknown(self) -> None:
        blocks = _blocks_for_window(1000, 5, latest_age_seconds=5)
        del blocks[998]  # hole in the window
        client = FakeRpcClient(blocks=blocks)
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "unknown")
        self.assertEqual(response.recent_blocks, [])

    def test_batch_size_mismatch_unknown(self) -> None:
        client = FakeRpcClient(
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=5),
            extra_batch_item=True,
        )
        # Fake returns extra item; collect should treat as parse failure.
        # Actually FakeRpcClient appends extra to results list which
        # fetch_recent_blocks checks via len mismatch — but Fake returns
        # len(results) == len(calls)+1, and batch_call just returns that.
        # fetch_recent_blocks compares len(results) != len(numbers).
        response = collect_chain_response(
            settings=_settings(), client=client, now=_now()
        )
        self.assertEqual(response.health, "unknown")

    def test_configurable_recent_block_count(self) -> None:
        client = FakeRpcClient(
            blocks=_blocks_for_window(1000, 3, latest_age_seconds=5),
        )
        response = collect_chain_response(
            settings=_settings(recent_block_count=3),
            client=client,
            now=_now(),
        )
        self.assertEqual(response.health, "healthy")
        self.assertEqual(response.chain.recent_block_count, 3)
        self.assertEqual(len(response.recent_blocks), 3)
        self.assertEqual(response.chain.transactions_recent_blocks, 6)
        get_block_calls = [
            c for c in client.calls if c[0] == "eth_getBlockByNumber"
        ]
        self.assertEqual(len(get_block_calls), 3)

    def test_large_window_fails_on_oversized_single_batch_like_edu_net(self) -> None:
        """Regression: Edu-Net gateway partial-fails oversized 100-call batches.

        A single HTTP JSON-RPC batch of 100 eth_getBlockByNumber calls often
        returns per-item upstream errors. The API used to abort the whole
        window and return health=unknown with null chain metrics even though
        node signals were valid.

        Chunked batches (rpc_batch_size<=gateway limit) must still require
        every block; they must not loosen validation.
        """

        class GatewayStyleRpc(FakeRpcClient):
            """Fails any batch containing more than 50 block fetches."""

            max_block_calls_per_http_batch = 50

            def batch_call(self, calls: Sequence[Tuple[str, List[Any]]]) -> List[Any]:
                block_calls = [
                    call for call in calls if call[0] == "eth_getBlockByNumber"
                ]
                if len(block_calls) > self.max_block_calls_per_http_batch:
                    # Same failure mode as api.rpc._extract_batch_results when
                    # any batch item contains error: { ... }.
                    raise RpcParseError("RPC method returned an error")
                return super().batch_call(calls)

        latest = 2000
        window = 100
        blocks = _blocks_for_window(latest, window, latest_age_seconds=5)

        # Old behavior equivalent: one giant batch (== window size).
        oversized = GatewayStyleRpc(latest=latest, blocks=blocks)
        failed = collect_chain_response(
            settings=_settings(recent_block_count=window, rpc_batch_size=100),
            client=oversized,
            now=_now(),
        )
        self.assertEqual(failed.health, "unknown")
        self.assertTrue(failed.rpc.reachable)
        self.assertEqual(failed.rpc.peer_count, 25)
        self.assertEqual(failed.chain.chain_id, 424242)
        self.assertIsNone(failed.chain.block_number)
        self.assertIsNone(failed.chain.latest_block_age_seconds)
        self.assertIsNone(failed.chain.average_block_time_seconds)
        self.assertIsNone(failed.chain.transactions_recent_blocks)
        self.assertEqual(failed.recent_blocks, [])

        # Fixed behavior: chunk below the gateway's reliable batch size.
        chunked = GatewayStyleRpc(latest=latest, blocks=blocks)
        ok = collect_chain_response(
            settings=_settings(recent_block_count=window, rpc_batch_size=25),
            client=chunked,
            now=_now(),
        )
        self.assertEqual(ok.health, "healthy")
        self.assertEqual(ok.chain.block_number, latest)
        self.assertIsNotNone(ok.chain.latest_block_age_seconds)
        self.assertEqual(ok.chain.average_block_time_seconds, 5.0)
        self.assertEqual(len(ok.recent_blocks), window)
        self.assertEqual(ok.recent_blocks[0].number, latest)
        self.assertEqual(ok.recent_blocks[-1].number, latest - window + 1)
        self.assertIsNone(ok.recent_blocks[-1].interval_seconds)

        class CountingGateway(GatewayStyleRpc):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.http_batch_sizes: List[int] = []

            def batch_call(self, calls: Sequence[Tuple[str, List[Any]]]) -> List[Any]:
                block_calls = [
                    call for call in calls if call[0] == "eth_getBlockByNumber"
                ]
                if block_calls:
                    self.http_batch_sizes.append(len(block_calls))
                return super().batch_call(calls)

        counter = CountingGateway(latest=latest, blocks=blocks)
        collect_chain_response(
            settings=_settings(recent_block_count=window, rpc_batch_size=25),
            client=counter,
            now=_now(),
        )
        self.assertEqual(counter.http_batch_sizes, [25, 25, 25, 25])
        self.assertTrue(
            all(
                size <= counter.max_block_calls_per_http_batch
                for size in counter.http_batch_sizes
            )
        )


class ChainEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_path = Path(self._tmpdir.name) / "consensus_snapshot.json"
        self.settings = _settings(consensus_snapshot_path=self.snapshot_path)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _client(self, rpc: FakeRpcClient) -> TestClient:
        app = create_app(self.settings, clock=_now, rpc_client=rpc)
        return TestClient(app)

    def test_endpoint_healthy(self) -> None:
        rpc = FakeRpcClient(
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=5),
        )
        response = self._client(rpc).get("/api/v1/chain")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["chain"]["chain_id"], 424242)
        self.assertIn("transactions_recent_blocks", body["chain"])
        self.assertNotIn("transactions_last_100_blocks", body["chain"])
        self.assertEqual(body["rpc"]["syncing"], False)

    def test_endpoint_rpc_unavailable_http_200(self) -> None:
        rpc = FakeRpcClient(transport_error_on="any")
        response = self._client(rpc).get("/api/v1/chain")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertFalse(body["rpc"]["reachable"])

    def test_no_sensitive_or_raw_rpc_errors(self) -> None:
        rpc = FakeRpcClient(transport_error_on="any")
        body = self._client(rpc).get("/api/v1/chain").json()
        dumped = json.dumps(body)
        for needle in (
            "Traceback",
            "RpcTransportError",
            "unreachable",
            "exception",
            "stack",
            str(self.snapshot_path),
            "qbft_",
        ):
            self.assertNotIn(needle, dumped)
        self.assertNotIn("error", body)
        self.assertNotIn("raw", dumped.lower())

    def test_health_endpoint_unchanged(self) -> None:
        rpc = FakeRpcClient(
            blocks=_blocks_for_window(1000, 5, latest_age_seconds=5),
        )
        response = self._client(rpc).get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "service": "besu-monitor-api"},
        )

    def test_consensus_endpoint_unchanged(self) -> None:
        snap = {
            "schema_version": "1.0",
            "generated_at": (_now() - timedelta(minutes=2))
            .isoformat()
            .replace("+00:00", "Z"),
            "status": "ok",
            "reference_block": 8162213,
            "validator_count": 7,
            "synced_validators": 7,
            "quorum_required": 5,
            "has_quorum": True,
            "drift": {"observed_max_blocks": 1, "allowed_max_blocks": 2},
            "out_of_sync_count": 0,
        }
        self.snapshot_path.write_text(json.dumps(snap), encoding="utf-8")
        rpc = FakeRpcClient(transport_error_on="any")
        response = self._client(rpc).get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["freshness"]["state"], "fresh")
        self.assertEqual(body["consensus"]["reference_block"], 8162213)
        self.assertNotIn("rpc", body)
        self.assertNotIn("recent_blocks", body)


if __name__ == "__main__":
    unittest.main()
