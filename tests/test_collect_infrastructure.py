#!/usr/bin/env python3
"""Unit tests for collect_infrastructure (no live Prometheus)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_infrastructure as ci  # noqa: E402


def _sample(job: str, value: float, *, instance_name: str | None = None) -> dict:
    metric: dict[str, str] = {"__name__": "up", "job": job}
    if instance_name is not None:
        metric["instance_name"] = instance_name
    return {"metric": metric, "value": [1_700_000_000.0, str(value)]}


def _all_up_series() -> dict[str, list[dict]]:
    """Complete inventory: every expected series present and UP."""
    return {
        f'up{{job="{ci.JOB_VALIDATOR_BESU}"}}': [
            _sample(ci.JOB_VALIDATOR_BESU, 1.0, instance_name=name)
            for name in ci.VALIDATOR_NAMES
        ],
        f'up{{job="{ci.JOB_VALIDATOR_NODE}"}}': [
            _sample(ci.JOB_VALIDATOR_NODE, 1.0, instance_name=name)
            for name in ci.VALIDATOR_NAMES
        ],
        f'up{{job="{ci.JOB_PRIMARY_RPC}"}}': [
            _sample(ci.JOB_PRIMARY_RPC, 1.0),
        ],
        f'up{{job="{ci.JOB_BACKUP_RPC}"}}': [
            _sample(
                ci.JOB_BACKUP_RPC,
                1.0,
                instance_name=ci.BACKUP_RPC_INSTANCE,
            ),
        ],
        f'up{{job="{ci.JOB_NODE_DIRECT}"}}': [
            _sample(ci.JOB_NODE_DIRECT, 1.0, instance_name=ci.BOOTNODE_INSTANCE),
            _sample(ci.JOB_NODE_DIRECT, 1.0, instance_name=ci.FAUCET_INSTANCE),
            _sample(ci.JOB_NODE_DIRECT, 1.0, instance_name=ci.IDENTITY_INSTANCE),
            _sample(ci.JOB_NODE_DIRECT, 1.0, instance_name=ci.BACKEND_INSTANCE),
        ],
        f'up{{job="{ci.JOB_BLOCK_EXPLORER}"}}': [
            _sample(ci.JOB_BLOCK_EXPLORER, 1.0),
        ],
        f'up{{job="{ci.JOB_GRAFANA}"}}': [
            _sample(ci.JOB_GRAFANA, 1.0),
        ],
        f'up{{job="{ci.JOB_PROMETHEUS}"}}': [
            _sample(ci.JOB_PROMETHEUS, 1.0),
        ],
    }


class FakeTransport:
    def __init__(self, series_by_query: dict[str, list[dict]]) -> None:
        self.series_by_query = series_by_query
        self.calls: list[str] = []

    def __call__(self, promql: str, timeout_seconds: float) -> Any:
        del timeout_seconds
        self.calls.append(promql)
        if promql not in self.series_by_query:
            raise ci.CollectorError(f"unexpected query: {promql}")
        return self.series_by_query[promql]


def _set_up(series: dict[str, list[dict]], promql: str, value: float, **kwargs: Any) -> None:
    """Replace all samples for a query with a single-value override helper."""
    del kwargs
    samples = series[promql]
    for sample in samples:
        sample["value"] = [1_700_000_000.0, str(value)]


def _set_named_up(
    series: dict[str, list[dict]],
    promql: str,
    instance_name: str,
    value: float,
) -> None:
    for sample in series[promql]:
        if sample["metric"].get("instance_name") == instance_name:
            sample["value"] = [1_700_000_000.0, str(value)]
            return
    raise AssertionError(f"instance_name {instance_name} not found in {promql}")


# Source identities / labels / IPs must not appear. Public field names such as
# faucet_up / identity_up / backend_up are part of the sanitized contract.
SENSITIVE_NEEDLES = (
    "195.251.",
    "83.212.",
    "127.0.0.1",
    "localhost",
    "besu_validators_nginx",
    "node_exporter_direct",
    "blackbox_eth_blocknumber",
    "besu_rpc_backup",
    "blackbox_block_explorer",
    "blackbox_grafana",
    "validator1",
    "validator2",
    "validator3",
    "validator4",
    "validator5",
    "validator6",
    "validator7",
    "rpc2",
    "keycloak",
    "my-backend",
    "bootnode2",
    "instance_name",
    "PromQL",
    "/api/v1/query",
)


class HealthyPathTests(unittest.TestCase):
    def test_all_16_available_healthy(self) -> None:
        snap = ci.run_collection(
            FakeTransport(_all_up_series()),
            generated_at="2026-10-26T12:00:00.000000Z",
        )
        self.assertEqual(snap["status"], "healthy")
        self.assertEqual(snap["schema_version"], 1)
        self.assertEqual(snap["systems"]["monitored"], 16)
        self.assertEqual(snap["systems"]["up"], 16)
        self.assertEqual(snap["systems"]["down"], 0)
        self.assertEqual(snap["validators"]["monitored"], 7)
        self.assertEqual(snap["validators"]["up"], 7)
        self.assertEqual(snap["validators"]["down"], 0)
        self.assertEqual(snap["systems"]["up"] + snap["systems"]["down"], 16)
        self.assertTrue(snap["rpc"]["primary_up"])
        self.assertTrue(snap["rpc"]["backup_up"])
        for key, value in snap["services"].items():
            self.assertIs(value, True, msg=key)

    def test_validators_counted_once_despite_dual_telemetry(self) -> None:
        transport = FakeTransport(_all_up_series())
        snap = ci.run_collection(transport)
        # 7 besu + 7 node queries worth of series, but validators.monitored == 7
        self.assertEqual(snap["validators"]["monitored"], 7)
        self.assertEqual(snap["systems"]["monitored"], 16)
        besu_q = f'up{{job="{ci.JOB_VALIDATOR_BESU}"}}'
        node_q = f'up{{job="{ci.JOB_VALIDATOR_NODE}"}}'
        self.assertIn(besu_q, transport.calls)
        self.assertIn(node_q, transport.calls)


class DegradedPathTests(unittest.TestCase):
    def test_backup_rpc_down_degraded(self) -> None:
        series = _all_up_series()
        _set_named_up(
            series,
            f'up{{job="{ci.JOB_BACKUP_RPC}"}}',
            ci.BACKUP_RPC_INSTANCE,
            0.0,
        )
        snap = ci.run_collection(FakeTransport(series))
        self.assertEqual(snap["status"], "degraded")
        self.assertFalse(snap["rpc"]["backup_up"])
        self.assertTrue(snap["rpc"]["primary_up"])
        self.assertEqual(snap["systems"]["up"], 15)
        self.assertEqual(snap["systems"]["down"], 1)

    def test_grafana_down_degraded(self) -> None:
        series = _all_up_series()
        _set_up(series, f'up{{job="{ci.JOB_GRAFANA}"}}', 0.0)
        snap = ci.run_collection(FakeTransport(series))
        self.assertEqual(snap["status"], "degraded")
        self.assertFalse(snap["services"]["grafana_endpoint_up"])

    def test_block_explorer_down_degraded(self) -> None:
        series = _all_up_series()
        _set_up(series, f'up{{job="{ci.JOB_BLOCK_EXPLORER}"}}', 0.0)
        snap = ci.run_collection(FakeTransport(series))
        self.assertEqual(snap["status"], "degraded")
        self.assertFalse(snap["services"]["block_explorer_endpoint_up"])

    def test_one_validator_besu_down_counts_validator_down(self) -> None:
        series = _all_up_series()
        _set_named_up(
            series,
            f'up{{job="{ci.JOB_VALIDATOR_BESU}"}}',
            "validator3",
            0.0,
        )
        snap = ci.run_collection(FakeTransport(series))
        self.assertEqual(snap["status"], "degraded")
        self.assertEqual(snap["validators"]["up"], 6)
        self.assertEqual(snap["validators"]["down"], 1)
        self.assertEqual(snap["systems"]["up"], 15)
        self.assertEqual(snap["systems"]["down"], 1)

    def test_one_validator_node_exporter_down_counts_validator_down(self) -> None:
        series = _all_up_series()
        _set_named_up(
            series,
            f'up{{job="{ci.JOB_VALIDATOR_NODE}"}}',
            "validator5",
            0.0,
        )
        snap = ci.run_collection(FakeTransport(series))
        self.assertEqual(snap["status"], "degraded")
        self.assertEqual(snap["validators"]["up"], 6)
        self.assertEqual(snap["validators"]["down"], 1)
        self.assertEqual(snap["systems"]["monitored"], 16)


class CriticalPathTests(unittest.TestCase):
    def test_primary_rpc_down_critical(self) -> None:
        series = _all_up_series()
        _set_up(series, f'up{{job="{ci.JOB_PRIMARY_RPC}"}}', 0.0)
        snap = ci.run_collection(FakeTransport(series))
        self.assertEqual(snap["status"], "critical")
        self.assertFalse(snap["rpc"]["primary_up"])
        self.assertEqual(snap["systems"]["down"], 1)


class IncompleteTelemetryTests(unittest.TestCase):
    def test_missing_validator_series_raises(self) -> None:
        series = _all_up_series()
        q = f'up{{job="{ci.JOB_VALIDATOR_BESU}"}}'
        series[q] = [s for s in series[q] if s["metric"]["instance_name"] != "validator7"]
        with self.assertRaises(ci.CollectorError):
            ci.run_collection(FakeTransport(series))

    def test_duplicate_validator_series_raises(self) -> None:
        series = _all_up_series()
        q = f'up{{job="{ci.JOB_VALIDATOR_BESU}"}}'
        series[q] = series[q] + [
            _sample(ci.JOB_VALIDATOR_BESU, 1.0, instance_name="validator1")
        ]
        with self.assertRaises(ci.CollectorError):
            ci.run_collection(FakeTransport(series))

    def test_missing_required_service_series_raises(self) -> None:
        series = _all_up_series()
        series[f'up{{job="{ci.JOB_GRAFANA}"}}'] = []
        with self.assertRaises(ci.CollectorError):
            ci.run_collection(FakeTransport(series))

    def test_prometheus_api_failure_raises(self) -> None:
        def failing(_promql: str, _timeout: float) -> Any:
            raise ci.CollectorError("Prometheus unreachable")

        with self.assertRaises(ci.CollectorError):
            ci.run_collection(failing)

    def test_malformed_prometheus_response(self) -> None:
        with self.assertRaises(ci.CollectorError):
            ci.extract_prometheus_result({"status": "error", "error": "boom"})
        with self.assertRaises(ci.CollectorError):
            ci.extract_prometheus_result({"status": "success", "data": {"resultType": "matrix", "result": []}})
        with self.assertRaises(ci.CollectorError):
            ci.extract_prometheus_result("not-an-object")

    def test_invalid_sample_value_raises(self) -> None:
        series = _all_up_series()
        series[f'up{{job="{ci.JOB_PROMETHEUS}"}}'] = [
            {
                "metric": {"__name__": "up", "job": ci.JOB_PROMETHEUS},
                "value": [1.0, "2"],
            }
        ]
        with self.assertRaises(ci.CollectorError):
            ci.run_collection(FakeTransport(series))

    def test_unknown_snapshot_schema_uses_nulls(self) -> None:
        snap = ci.build_unknown_snapshot(generated_at="2026-10-26T12:00:00.000000Z")
        self.assertEqual(snap["status"], "unknown")
        self.assertIsNone(snap["systems"]["up"])
        self.assertIsNone(snap["systems"]["down"])
        self.assertIsNone(snap["rpc"]["primary_up"])
        self.assertIsNone(snap["services"]["identity_up"])
        # Collector failure path must not invent booleans as True.
        dumped = json.dumps(snap)
        self.assertNotIn("true", dumped)


class SanitizationTests(unittest.TestCase):
    def test_sanitized_output_has_no_sensitive_fields(self) -> None:
        snap = ci.run_collection(FakeTransport(_all_up_series()))
        dumped = json.dumps(snap)
        for needle in SENSITIVE_NEEDLES:
            self.assertNotIn(needle, dumped, msg=needle)
        # Public service keys are allowed; ensure no raw label maps.
        self.assertNotIn("metric", snap)
        self.assertNotIn("labels", snap)
        self.assertNotIn("query", snap)


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_and_keeps_prior_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "infrastructure_snapshot.json"
            ci.atomic_write_json(path, {"ok": True})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"ok": True})

            ci.atomic_write_json(path, {"ok": False, "n": 1})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"ok": False, "n": 1},
            )

            # Failure during collection must not call write; simulate by ensuring
            # a failed run_collection leaves prior content untouched.
            prior = json.loads(path.read_text(encoding="utf-8"))

            def failing(_q: str, _t: float) -> Any:
                raise ci.CollectorError("Prometheus unreachable")

            with self.assertRaises(ci.CollectorError):
                ci.run_collection(failing)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), prior)


class HealthPolicyUnitTests(unittest.TestCase):
    def _state(self, **overrides: Any) -> ci.InfrastructureState:
        base = dict(
            validator_up=(True,) * 7,
            primary_rpc_up=True,
            backup_rpc_up=True,
            bootnode_up=True,
            block_explorer_up=True,
            faucet_up=True,
            identity_up=True,
            backend_up=True,
            prometheus_up=True,
            grafana_up=True,
        )
        base.update(overrides)
        return ci.InfrastructureState(**base)

    def test_policy_healthy_degraded_critical(self) -> None:
        self.assertEqual(ci.evaluate_infrastructure_status(self._state()), "healthy")
        self.assertEqual(
            ci.evaluate_infrastructure_status(self._state(backup_rpc_up=False)),
            "degraded",
        )
        self.assertEqual(
            ci.evaluate_infrastructure_status(self._state(primary_rpc_up=False)),
            "critical",
        )


if __name__ == "__main__":
    unittest.main()
