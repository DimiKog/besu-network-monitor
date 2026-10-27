"""HTTP-level tests for /api/v1/health and /api/v1/consensus."""

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

from api.config import SERVICE_NAME, Settings  # noqa: E402
from api.main import create_app  # noqa: E402


def _now() -> datetime:
    return datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_snapshot(**overrides: object) -> dict:
    base = {
        "schema_version": "1.0",
        "generated_at": _iso(_now() - timedelta(minutes=2)),
        "status": "ok",
        "reference_block": 8162213,
        "validator_count": 7,
        "synced_validators": 7,
        "quorum_required": 5,
        "has_quorum": True,
        "drift": {
            "observed_max_blocks": 1,
            "allowed_max_blocks": 2,
        },
        "out_of_sync_count": 0,
    }
    base.update(overrides)
    return base


SENSITIVE_KEYS = {
    "status",  # collector source status — not public Monitor health
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


class ApiEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.snapshot_path = Path(self._tmpdir.name) / "consensus_snapshot.json"
        self.settings = Settings(
            consensus_snapshot_path=self.snapshot_path,
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.app = create_app(self.settings, clock=_now)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _write_snapshot(self, payload: dict, path: Path | None = None) -> None:
        target = path if path is not None else self.snapshot_path
        target.write_text(json.dumps(payload), encoding="utf-8")

    def test_health_is_service_only(self) -> None:
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body, {"status": "ok", "service": SERVICE_NAME})
        self.assertNotIn("health", body)
        self.assertNotIn("consensus", body)
        self.assertNotIn("freshness", body)

    def test_fresh_ok_healthy(self) -> None:
        self._write_snapshot(valid_snapshot(status="ok"))
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["freshness"]["state"], "fresh")
        self.assertEqual(body["freshness"]["stale_after_seconds"], 900)
        self.assertEqual(body["consensus"]["reference_block"], 8162213)
        self.assertEqual(body["consensus"]["has_quorum"], True)
        _assert_no_sensitive_keys(body)

    def test_fresh_warning_degraded(self) -> None:
        self._write_snapshot(
            valid_snapshot(
                status="warning",
                synced_validators=6,
                out_of_sync_count=1,
            )
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "degraded")

    def test_fresh_critical_critical(self) -> None:
        self._write_snapshot(
            valid_snapshot(
                status="critical",
                synced_validators=3,
                has_quorum=False,
                out_of_sync_count=4,
            )
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "critical")

    def test_stale_ok_unknown_http_200(self) -> None:
        self._write_snapshot(
            valid_snapshot(
                status="ok",
                generated_at=_iso(_now() - timedelta(minutes=20)),
            )
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["freshness"]["state"], "stale")
        self.assertGreater(body["freshness"]["age_seconds"], 900)

    def test_missing_snapshot_unknown_http_200(self) -> None:
        # Do not create the snapshot file.
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["freshness"]["state"], "unknown")
        self.assertIsNone(body["freshness"]["age_seconds"])
        self.assertIsNone(body["consensus"]["reference_block"])
        _assert_no_sensitive_keys(body)

    def test_malformed_json_unknown(self) -> None:
        self.snapshot_path.write_text("{not-json", encoding="utf-8")
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_unsupported_schema_version(self) -> None:
        self._write_snapshot(valid_snapshot(schema_version="0.9"))
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_naive_generated_at_unknown(self) -> None:
        self._write_snapshot(
            valid_snapshot(generated_at="2026-10-26T12:00:00")
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_invalid_generated_at_unknown(self) -> None:
        self._write_snapshot(valid_snapshot(generated_at="yesterday"))
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_generated_at_too_far_future_unknown(self) -> None:
        self._write_snapshot(
            valid_snapshot(generated_at=_iso(_now() + timedelta(minutes=5)))
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "unknown")
        self.assertEqual(body["freshness"]["state"], "unknown")

    def test_small_allowed_future_skew_works(self) -> None:
        self._write_snapshot(
            valid_snapshot(generated_at=_iso(_now() + timedelta(seconds=45)))
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["health"], "healthy")
        self.assertEqual(body["freshness"]["state"], "fresh")

    def test_contradictory_quorum_unknown(self) -> None:
        self._write_snapshot(valid_snapshot(has_quorum=False))
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_invalid_drift_unknown(self) -> None:
        self._write_snapshot(
            valid_snapshot(
                drift={"observed_max_blocks": -5, "allowed_max_blocks": 2}
            )
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_synced_validators_gt_validator_count_unknown(self) -> None:
        self._write_snapshot(
            valid_snapshot(synced_validators=10, has_quorum=True)
        )
        response = self.client.get("/api/v1/consensus")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["health"], "unknown")

    def test_no_sensitive_fields_in_public_response(self) -> None:
        self._write_snapshot(valid_snapshot())
        body = self.client.get("/api/v1/consensus").json()
        _assert_no_sensitive_keys(body)
        # Explicitly ensure collector source status is not echoed.
        dumped = json.dumps(body)
        self.assertNotIn('"status": "ok"', dumped)
        self.assertNotIn("source_status", dumped)
        self.assertNotIn(str(self.snapshot_path), dumped)

    def test_atomic_replacement_observed_without_restart(self) -> None:
        """Publisher-style atomic rename must be visible on the next request."""
        first = valid_snapshot(
            status="ok",
            reference_block=100,
            synced_validators=7,
            out_of_sync_count=0,
        )
        self._write_snapshot(first)
        first_body = self.client.get("/api/v1/consensus").json()
        self.assertEqual(first_body["health"], "healthy")
        self.assertEqual(first_body["consensus"]["reference_block"], 100)

        second = valid_snapshot(
            status="warning",
            reference_block=200,
            synced_validators=6,
            out_of_sync_count=1,
        )
        # Atomic replace: write temp then rename onto the published path.
        tmp = self.snapshot_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(second), encoding="utf-8")
        os.replace(tmp, self.snapshot_path)

        second_body = self.client.get("/api/v1/consensus").json()
        self.assertEqual(second_body["health"], "degraded")
        self.assertEqual(second_body["consensus"]["reference_block"], 200)
        self.assertEqual(second_body["consensus"]["synced_validators"], 6)


class SettingsEnvTests(unittest.TestCase):
    def test_settings_from_environ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "snap.json")
            previous = {
                "BESU_MONITOR_CONSENSUS_SNAPSHOT": os.environ.get(
                    "BESU_MONITOR_CONSENSUS_SNAPSHOT"
                ),
                "BESU_MONITOR_STALE_AFTER_SECONDS": os.environ.get(
                    "BESU_MONITOR_STALE_AFTER_SECONDS"
                ),
                "BESU_MONITOR_FUTURE_TOLERANCE_SECONDS": os.environ.get(
                    "BESU_MONITOR_FUTURE_TOLERANCE_SECONDS"
                ),
            }
            try:
                os.environ["BESU_MONITOR_CONSENSUS_SNAPSHOT"] = path
                os.environ["BESU_MONITOR_STALE_AFTER_SECONDS"] = "1200"
                os.environ["BESU_MONITOR_FUTURE_TOLERANCE_SECONDS"] = "30"
                settings = Settings.from_environ()
                self.assertEqual(settings.consensus_snapshot_path, Path(path))
                self.assertEqual(settings.stale_after_seconds, 1200)
                self.assertEqual(settings.future_tolerance_seconds, 30)
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
