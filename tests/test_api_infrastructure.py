#!/usr/bin/env python3
"""HTTP and validation tests for GET /api/v1/infrastructure."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.config import Settings  # noqa: E402
from api.evaluation import SnapshotValidationError  # noqa: E402
from api.infrastructure import (  # noqa: E402
    expected_infrastructure_status,
    validate_infrastructure_snapshot,
)
from api.main import create_app  # noqa: E402


def _now() -> datetime:
    return datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_infra_snapshot(**overrides: object) -> dict:
    """Build a complete healthy 16/16 infrastructure snapshot."""
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
    # Shallow overrides for top-level keys; nested dicts replaced wholesale.
    base.update(overrides)
    return base


def _with_one_system_down(
    *,
    which: str,
    status: str = "degraded",
) -> dict:
    """Return a consistent snapshot with exactly one non-primary loss."""
    snap = valid_infra_snapshot(status=status)
    systems_up = 15
    systems_down = 1
    validators_up = 7
    validators_down = 0

    if which == "backup_rpc":
        snap["rpc"] = {"primary_up": True, "backup_up": False}
    elif which == "grafana":
        snap["services"] = dict(snap["services"])
        snap["services"]["grafana_endpoint_up"] = False
    elif which == "validator":
        validators_up = 6
        validators_down = 1
        snap["validators"] = {
            "monitored": 7,
            "up": validators_up,
            "down": validators_down,
        }
    else:
        raise ValueError(f"unknown which={which!r}")

    snap["systems"] = {
        "monitored": 16,
        "up": systems_up,
        "down": systems_down,
    }
    return snap


def _primary_rpc_down() -> dict:
    snap = valid_infra_snapshot(status="critical")
    snap["rpc"] = {"primary_up": False, "backup_up": True}
    snap["systems"] = {"monitored": 16, "up": 15, "down": 1}
    return snap


SENSITIVE_KEYS = {
    "status",  # collector status — not echoed; public uses health
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


def _assert_null_infrastructure(body: dict) -> None:
    infra = body["infrastructure"]
    self_assert = unittest.TestCase()
    for section in ("systems", "validators"):
        for key in ("monitored", "up", "down"):
            self_assert.assertIsNone(infra[section][key])
    self_assert.assertIsNone(infra["rpc"]["primary_up"])
    self_assert.assertIsNone(infra["rpc"]["backup_up"])
    for key in (
        "bootnode_up",
        "block_explorer_endpoint_up",
        "faucet_up",
        "identity_up",
        "backend_up",
        "prometheus_up",
        "grafana_endpoint_up",
    ):
        self_assert.assertIsNone(infra["services"][key])


class InfrastructureEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_path = (
            Path(self._tmpdir.name) / "infrastructure_snapshot.json"
        )
        self.settings = Settings(
            consensus_snapshot_path=Path(self._tmpdir.name)
            / "unused_consensus.json",
            infrastructure_snapshot_path=self.snapshot_path,
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.app = create_app(self.settings, clock=_now)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write(self, payload: dict) -> None:
        self.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    def test_fresh_healthy_16_of_16(self) -> None:
        self._write(valid_infra_snapshot())
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["freshness"]["state"], "fresh")
        self.assertEqual(body["freshness"]["stale_after_seconds"], 900)
        self.assertEqual(body["infrastructure"]["systems"]["monitored"], 16)
        self.assertEqual(body["infrastructure"]["systems"]["up"], 16)
        self.assertEqual(body["infrastructure"]["validators"]["up"], 7)
        self.assertTrue(body["infrastructure"]["rpc"]["primary_up"])
        self.assertTrue(body["infrastructure"]["rpc"]["backup_up"])
        self.assertTrue(body["infrastructure"]["services"]["grafana_endpoint_up"])
        _assert_no_sensitive_keys(body)

    def test_fresh_backup_rpc_down_degraded(self) -> None:
        self._write(_with_one_system_down(which="backup_rpc"))
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "degraded")
        self.assertFalse(body["infrastructure"]["rpc"]["backup_up"])
        self.assertTrue(body["infrastructure"]["rpc"]["primary_up"])
        self.assertEqual(body["infrastructure"]["systems"]["up"], 15)

    def test_fresh_grafana_down_degraded(self) -> None:
        self._write(_with_one_system_down(which="grafana"))
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "degraded")
        self.assertFalse(
            body["infrastructure"]["services"]["grafana_endpoint_up"]
        )

    def test_fresh_one_validator_down_degraded(self) -> None:
        self._write(_with_one_system_down(which="validator"))
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "degraded")
        self.assertEqual(body["infrastructure"]["validators"]["up"], 6)
        self.assertEqual(body["infrastructure"]["validators"]["down"], 1)
        self.assertEqual(body["infrastructure"]["systems"]["up"], 15)

    def test_fresh_primary_rpc_down_critical(self) -> None:
        self._write(_primary_rpc_down())
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "critical")
        self.assertFalse(body["infrastructure"]["rpc"]["primary_up"])
        self.assertEqual(body["infrastructure"]["systems"]["up"], 15)

    def test_stale_valid_unknown_metrics_retained(self) -> None:
        self._write(
            valid_infra_snapshot(
                generated_at=_iso(_now() - timedelta(minutes=20)),
            )
        )
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["freshness"]["state"], "stale")
        self.assertGreater(body["freshness"]["age_seconds"], 900)
        self.assertEqual(body["infrastructure"]["systems"]["up"], 16)
        self.assertEqual(body["infrastructure"]["validators"]["up"], 7)
        self.assertTrue(body["infrastructure"]["rpc"]["primary_up"])

    def test_missing_snapshot_unknown_null_metrics(self) -> None:
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["freshness"]["state"], "unknown")
        self.assertIsNone(body["freshness"]["age_seconds"])
        _assert_null_infrastructure(body)
        _assert_no_sensitive_keys(body)

    def test_malformed_json_unknown(self) -> None:
        self.snapshot_path.write_text("{not-json", encoding="utf-8")
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        _assert_null_infrastructure(body)

    def test_wrong_schema_version_unknown(self) -> None:
        self._write(valid_infra_snapshot(schema_version=99))
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        _assert_null_infrastructure(body)

    def test_invalid_systems_counts_unknown(self) -> None:
        bad = valid_infra_snapshot()
        bad["systems"] = {"monitored": 16, "up": 10, "down": 3}
        self._write(bad)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")
        _assert_null_infrastructure(response.json())

    def test_systems_monitored_not_16_unknown(self) -> None:
        bad = valid_infra_snapshot()
        bad["systems"] = {"monitored": 15, "up": 15, "down": 0}
        self._write(bad)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_validators_monitored_not_7_unknown(self) -> None:
        bad = valid_infra_snapshot()
        bad["validators"] = {"monitored": 6, "up": 6, "down": 0}
        self._write(bad)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_invalid_boolean_field_unknown(self) -> None:
        bad = valid_infra_snapshot()
        bad["rpc"] = {"primary_up": "yes", "backup_up": True}
        self._write(bad)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")
        _assert_null_infrastructure(response.json())

    def test_contradictory_status_vs_metrics_unknown(self) -> None:
        # Metrics say healthy 16/16 but status claims degraded.
        bad = valid_infra_snapshot(status="degraded")
        self._write(bad)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")
        _assert_null_infrastructure(response.json())

    def test_contradictory_primary_down_but_healthy_status(self) -> None:
        bad = _primary_rpc_down()
        bad["status"] = "healthy"
        self._write(bad)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_future_beyond_tolerance_unknown_null_metrics(self) -> None:
        self._write(
            valid_infra_snapshot(
                generated_at=_iso(_now() + timedelta(minutes=5)),
            )
        )
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["freshness"]["state"], "unknown")
        _assert_null_infrastructure(body)

    def test_small_future_skew_allowed(self) -> None:
        self._write(
            valid_infra_snapshot(
                generated_at=_iso(_now() + timedelta(seconds=45)),
            )
        )
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["freshness"]["state"], "fresh")

    def test_response_never_exposes_path_or_raw_details(self) -> None:
        self._write(valid_infra_snapshot())
        body = self.client.get("/api/v1/infrastructure").json()
        _assert_no_sensitive_keys(body)
        dumped = json.dumps(body)
        self.assertNotIn(str(self.snapshot_path), dumped)
        self.assertNotIn("schema_version", dumped)
        self.assertNotIn("traceback", dumped)
        self.assertNotIn("besu_validators_nginx", dumped)
        self.assertNotIn("/api/v1/query", dumped)
        # Collector status vocabulary must not appear as a public field.
        self.assertNotIn('"status"', dumped)

    def test_http_200_for_all_health_states(self) -> None:
        cases = [
            (valid_infra_snapshot(), "healthy"),
            (_with_one_system_down(which="grafana"), "degraded"),
            (_primary_rpc_down(), "critical"),
        ]
        for payload, expected_health in cases:
            with self.subTest(health=expected_health):
                self._write(payload)
                response = self.client.get("/api/v1/infrastructure")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["health"], expected_health)

        # unknown via missing file
        self.snapshot_path.unlink(missing_ok=True)
        response = self.client.get("/api/v1/infrastructure")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_atomic_replacement_observed_without_restart(self) -> None:
        self._write(valid_infra_snapshot())
        first = self.client.get("/api/v1/infrastructure").json()
        self.assertEqual(first["health"], "healthy")

        second = _with_one_system_down(which="backup_rpc")
        tmp = self.snapshot_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(second), encoding="utf-8")
        os.replace(tmp, self.snapshot_path)

        body = self.client.get("/api/v1/infrastructure").json()
        self.assertEqual(body["health"], "degraded")
        self.assertFalse(body["infrastructure"]["rpc"]["backup_up"])


class InfrastructureValidationUnitTests(unittest.TestCase):
    def test_policy_healthy_degraded_critical(self) -> None:
        self.assertEqual(
            expected_infrastructure_status(
                systems_up=16, systems_monitored=16, primary_up=True
            ),
            "healthy",
        )
        self.assertEqual(
            expected_infrastructure_status(
                systems_up=15, systems_monitored=16, primary_up=True
            ),
            "degraded",
        )
        self.assertEqual(
            expected_infrastructure_status(
                systems_up=15, systems_monitored=16, primary_up=False
            ),
            "critical",
        )

    def test_validate_rejects_inconsistent_boolean_counts(self) -> None:
        bad = valid_infra_snapshot()
        # Claim 16 up but flip a service bool without adjusting counts.
        bad["services"] = dict(bad["services"])
        bad["services"]["faucet_up"] = False
        with self.assertRaises(SnapshotValidationError):
            validate_infrastructure_snapshot(bad)

    def test_validate_accepts_healthy(self) -> None:
        snap = validate_infrastructure_snapshot(valid_infra_snapshot())
        self.assertEqual(snap.status, "healthy")
        self.assertEqual(snap.systems_up, 16)


class InfrastructureSettingsEnvTests(unittest.TestCase):
    def test_infrastructure_snapshot_from_environ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "infra.json")
            previous = os.environ.get("BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT")
            try:
                os.environ["BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT"] = path
                settings = Settings.from_environ()
                self.assertEqual(
                    settings.infrastructure_snapshot_path, Path(path)
                )
            finally:
                if previous is None:
                    os.environ.pop("BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT", None)
                else:
                    os.environ["BESU_MONITOR_INFRASTRUCTURE_SNAPSHOT"] = previous


if __name__ == "__main__":
    unittest.main()
