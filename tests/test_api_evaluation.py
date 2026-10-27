"""Unit tests for pure consensus validation, freshness, and health mapping."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.evaluation import (  # noqa: E402
    SnapshotValidationError,
    evaluate_freshness,
    evaluate_snapshot_payload,
    evaluate_validated_snapshot,
    map_source_status_to_health,
    parse_generated_at,
    validate_snapshot,
)


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


class ParseGeneratedAtTests(unittest.TestCase):
    def test_accepts_zulu(self) -> None:
        dt = parse_generated_at("2026-10-26T12:00:00Z")
        self.assertEqual(dt, datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc))

    def test_accepts_offset(self) -> None:
        dt = parse_generated_at("2026-10-26T14:00:00+02:00")
        self.assertEqual(dt, datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc))

    def test_rejects_naive(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            parse_generated_at("2026-10-26T12:00:00")

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            parse_generated_at(12345)


class ValidationTests(unittest.TestCase):
    def test_valid_snapshot_ok(self) -> None:
        snap = validate_snapshot(valid_snapshot())
        self.assertEqual(snap.source_status, "ok")
        self.assertEqual(snap.validator_count, 7)

    def test_unsupported_schema_version(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(valid_snapshot(schema_version="2.0"))

    def test_invalid_source_status(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(valid_snapshot(status="healthy"))

    def test_synced_validators_exceeds_count(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(
                valid_snapshot(synced_validators=8, has_quorum=True)
            )

    def test_contradictory_quorum(self) -> None:
        # synced 7 >= quorum 5, but has_quorum falsely False
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(valid_snapshot(has_quorum=False))

    def test_contradictory_quorum_true_when_below(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(
                valid_snapshot(
                    synced_validators=3,
                    has_quorum=True,
                    out_of_sync_count=4,
                )
            )

    def test_invalid_drift_negative(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(
                valid_snapshot(
                    drift={
                        "observed_max_blocks": -1,
                        "allowed_max_blocks": 2,
                    }
                )
            )

    def test_invalid_drift_missing(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(valid_snapshot(drift="nope"))

    def test_bool_rejected_as_int(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(valid_snapshot(reference_block=True))

    def test_validator_count_must_be_positive(self) -> None:
        with self.assertRaises(SnapshotValidationError):
            validate_snapshot(
                valid_snapshot(
                    validator_count=0,
                    synced_validators=0,
                    quorum_required=1,
                    has_quorum=False,
                )
            )


class FreshnessTests(unittest.TestCase):
    def test_fresh(self) -> None:
        generated = _now() - timedelta(minutes=5)
        state, age = evaluate_freshness(
            generated,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(state, "fresh")
        self.assertEqual(age, 300.0)

    def test_stale(self) -> None:
        generated = _now() - timedelta(minutes=16)
        state, age = evaluate_freshness(
            generated,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(state, "stale")
        self.assertEqual(age, 960.0)

    def test_small_future_skew_allowed(self) -> None:
        generated = _now() + timedelta(seconds=30)
        state, age = evaluate_freshness(
            generated,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(state, "fresh")
        self.assertEqual(age, -30.0)

    def test_future_too_far_unknown(self) -> None:
        generated = _now() + timedelta(seconds=120)
        state, age = evaluate_freshness(
            generated,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(state, "unknown")
        self.assertEqual(age, -120.0)


class HealthMappingTests(unittest.TestCase):
    def test_source_mapping(self) -> None:
        self.assertEqual(map_source_status_to_health("ok"), "healthy")
        self.assertEqual(map_source_status_to_health("warning"), "degraded")
        self.assertEqual(map_source_status_to_health("critical"), "critical")

    def test_fresh_ok_healthy(self) -> None:
        response = evaluate_snapshot_payload(
            valid_snapshot(status="ok"),
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "healthy")
        self.assertEqual(response.freshness.state, "fresh")

    def test_fresh_warning_degraded(self) -> None:
        response = evaluate_snapshot_payload(
            valid_snapshot(status="warning", synced_validators=6, out_of_sync_count=1),
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "degraded")

    def test_fresh_critical_critical(self) -> None:
        response = evaluate_snapshot_payload(
            valid_snapshot(
                status="critical",
                synced_validators=3,
                has_quorum=False,
                out_of_sync_count=4,
            ),
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "critical")

    def test_stale_ok_unknown(self) -> None:
        snap = valid_snapshot(
            status="ok",
            generated_at=_iso(_now() - timedelta(minutes=20)),
        )
        response = evaluate_snapshot_payload(
            snap,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "unknown")
        self.assertEqual(response.freshness.state, "stale")
        # Consensus metrics still present for operational context when schema-valid.
        self.assertEqual(response.consensus.reference_block, 8162213)

    def test_future_too_far_unknown(self) -> None:
        snap = valid_snapshot(
            generated_at=_iso(_now() + timedelta(minutes=5)),
        )
        response = evaluate_snapshot_payload(
            snap,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "unknown")
        self.assertEqual(response.freshness.state, "unknown")

    def test_invalid_payload_unknown_empty_consensus(self) -> None:
        response = evaluate_snapshot_payload(
            {"schema_version": "9.9"},
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "unknown")
        self.assertEqual(response.freshness.state, "unknown")
        self.assertIsNone(response.consensus.reference_block)

    def test_validated_stale_keeps_metrics(self) -> None:
        snap = validate_snapshot(
            valid_snapshot(generated_at=_iso(_now() - timedelta(minutes=20)))
        )
        response = evaluate_validated_snapshot(
            snap,
            now=_now(),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(response.health, "unknown")
        self.assertEqual(response.freshness.state, "stale")
        self.assertEqual(response.consensus.validator_count, 7)


if __name__ == "__main__":
    unittest.main()
