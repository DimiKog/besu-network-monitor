"""Pure consensus snapshot validation, freshness, and public-health mapping.

No I/O. Callers supply the parsed snapshot dict and a timezone-aware ``now``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Mapping, Optional

from api.config import SUPPORTED_SCHEMA_VERSION
from api.models import (
    ConsensusPublic,
    ConsensusResponse,
    DriftPublic,
    FreshnessPublic,
    PublicHealth,
)

logger = logging.getLogger(__name__)

SourceStatus = Literal["ok", "warning", "critical"]
SOURCE_STATUSES = frozenset({"ok", "warning", "critical"})

SOURCE_TO_PUBLIC_HEALTH: dict[SourceStatus, PublicHealth] = {
    "ok": "healthy",
    "warning": "degraded",
    "critical": "critical",
}


class SnapshotValidationError(ValueError):
    """Raised when a snapshot fails schema or cross-field validation."""


@dataclass(frozen=True)
class ValidatedSnapshot:
    """Validated collector snapshot fields used to build the public response."""

    schema_version: str
    generated_at: datetime
    generated_at_raw: str
    source_status: SourceStatus
    reference_block: int
    validator_count: int
    synced_validators: int
    quorum_required: int
    has_quorum: bool
    observed_max_blocks: int
    allowed_max_blocks: int
    out_of_sync_count: int


def parse_generated_at(value: Any) -> datetime:
    """Parse ``generated_at`` as a timezone-aware UTC timestamp.

    Rejects non-strings, unparseable values, and timezone-naive datetimes.
    Never silently assumes UTC for naive timestamps.
    """
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError("generated_at must be a non-empty string")

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SnapshotValidationError(
            f"generated_at is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc

    if parsed.tzinfo is None:
        raise SnapshotValidationError(
            "generated_at must be timezone-aware (UTC); naive timestamps are rejected"
        )

    return parsed.astimezone(timezone.utc)


def _require_int(
    data: Mapping[str, Any],
    key: str,
    *,
    minimum: Optional[int] = None,
    allow_zero: bool = True,
) -> int:
    raw = data.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SnapshotValidationError(f"{key} must be an integer")
    if minimum is not None and raw < minimum:
        raise SnapshotValidationError(f"{key} must be >= {minimum}")
    if not allow_zero and raw <= 0:
        raise SnapshotValidationError(f"{key} must be a positive integer")
    return raw


def validate_snapshot(data: Any) -> ValidatedSnapshot:
    """Validate the complete collector snapshot before trusting it.

    Does not silently repair contradictory or malformed data.
    """
    if not isinstance(data, dict):
        raise SnapshotValidationError("snapshot root must be a JSON object")

    schema_version = data.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise SnapshotValidationError(
            f"unsupported schema_version: {schema_version!r} "
            f"(supported: {SUPPORTED_SCHEMA_VERSION!r})"
        )

    generated_at_raw = data.get("generated_at")
    generated_at = parse_generated_at(generated_at_raw)

    status = data.get("status")
    if not isinstance(status, str) or status not in SOURCE_STATUSES:
        raise SnapshotValidationError(
            "status must be one of: ok, warning, critical"
        )

    reference_block = _require_int(data, "reference_block", minimum=0)
    validator_count = _require_int(data, "validator_count", allow_zero=False)
    synced_validators = _require_int(data, "synced_validators", minimum=0)
    quorum_required = _require_int(data, "quorum_required", allow_zero=False)
    out_of_sync_count = _require_int(data, "out_of_sync_count", minimum=0)

    has_quorum = data.get("has_quorum")
    if not isinstance(has_quorum, bool):
        raise SnapshotValidationError("has_quorum must be a boolean")

    drift = data.get("drift")
    if not isinstance(drift, dict):
        raise SnapshotValidationError("drift must be an object")

    observed_max_blocks = _require_int(drift, "observed_max_blocks", minimum=0)
    allowed_max_blocks = _require_int(drift, "allowed_max_blocks", minimum=0)

    if synced_validators > validator_count:
        raise SnapshotValidationError(
            "synced_validators must be <= validator_count"
        )
    if quorum_required > validator_count:
        raise SnapshotValidationError(
            "quorum_required must be <= validator_count"
        )
    if out_of_sync_count > validator_count:
        raise SnapshotValidationError(
            "out_of_sync_count must be <= validator_count"
        )

    expected_quorum = synced_validators >= quorum_required
    if has_quorum != expected_quorum:
        raise SnapshotValidationError(
            "has_quorum is inconsistent with synced_validators and quorum_required"
        )

    return ValidatedSnapshot(
        schema_version=schema_version,
        generated_at=generated_at,
        generated_at_raw=str(generated_at_raw).strip(),
        source_status=status,  # type: ignore[arg-type]
        reference_block=reference_block,
        validator_count=validator_count,
        synced_validators=synced_validators,
        quorum_required=quorum_required,
        has_quorum=has_quorum,
        observed_max_blocks=observed_max_blocks,
        allowed_max_blocks=allowed_max_blocks,
        out_of_sync_count=out_of_sync_count,
    )


def map_source_status_to_health(source_status: SourceStatus) -> PublicHealth:
    """Map collector source status to public Monitor health (fresh snapshots only)."""
    return SOURCE_TO_PUBLIC_HEALTH[source_status]


def evaluate_freshness(
    generated_at: datetime,
    *,
    now: datetime,
    stale_after_seconds: int,
    future_tolerance_seconds: int,
) -> tuple[Literal["fresh", "stale", "unknown"], float]:
    """Evaluate freshness from ``generated_at`` relative to ``now``.

    Returns ``(state, age_seconds)`` where ``age_seconds`` is
    ``(now - generated_at).total_seconds()``.

    Rules:
    - ``generated_at`` more than ``future_tolerance_seconds`` ahead of ``now``
      → ``unknown``
    - age strictly greater than ``stale_after_seconds`` → ``stale``
    - otherwise → ``fresh`` (including small future skew within tolerance)
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if generated_at.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")

    now_utc = now.astimezone(timezone.utc)
    generated_utc = generated_at.astimezone(timezone.utc)
    age_seconds = (now_utc - generated_utc).total_seconds()

    # Materially in the future beyond allowed clock-skew tolerance.
    if age_seconds < -float(future_tolerance_seconds):
        return "unknown", age_seconds

    if age_seconds > float(stale_after_seconds):
        return "stale", age_seconds

    return "fresh", age_seconds


def _empty_consensus() -> ConsensusPublic:
    return ConsensusPublic(
        reference_block=None,
        validator_count=None,
        synced_validators=None,
        quorum_required=None,
        has_quorum=None,
        drift=DriftPublic(
            observed_max_blocks=None,
            allowed_max_blocks=None,
        ),
        out_of_sync_count=None,
    )


def _filled_consensus(snapshot: ValidatedSnapshot) -> ConsensusPublic:
    return ConsensusPublic(
        reference_block=snapshot.reference_block,
        validator_count=snapshot.validator_count,
        synced_validators=snapshot.synced_validators,
        quorum_required=snapshot.quorum_required,
        has_quorum=snapshot.has_quorum,
        drift=DriftPublic(
            observed_max_blocks=snapshot.observed_max_blocks,
            allowed_max_blocks=snapshot.allowed_max_blocks,
        ),
        out_of_sync_count=snapshot.out_of_sync_count,
    )


def unknown_consensus_response(
    *,
    stale_after_seconds: int,
    freshness_state: Literal["fresh", "stale", "unknown"] = "unknown",
    age_seconds: Optional[float] = None,
    updated_at: Optional[str] = None,
    consensus: Optional[ConsensusPublic] = None,
) -> ConsensusResponse:
    """Build a public response when consensus health cannot be established."""
    return ConsensusResponse(
        health="unknown",
        freshness=FreshnessPublic(
            state=freshness_state,
            age_seconds=age_seconds,
            stale_after_seconds=stale_after_seconds,
            updated_at=updated_at,
        ),
        consensus=consensus if consensus is not None else _empty_consensus(),
    )


def evaluate_validated_snapshot(
    snapshot: ValidatedSnapshot,
    *,
    now: datetime,
    stale_after_seconds: int,
    future_tolerance_seconds: int,
) -> ConsensusResponse:
    """Map a validated snapshot to the public consensus response."""
    freshness_state, age_seconds = evaluate_freshness(
        snapshot.generated_at,
        now=now,
        stale_after_seconds=stale_after_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    )
    updated_at = snapshot.generated_at.isoformat().replace("+00:00", "Z")
    consensus = _filled_consensus(snapshot)

    if freshness_state != "fresh":
        # Stale or future-skew-unknown: never promote source ok → healthy.
        return unknown_consensus_response(
            stale_after_seconds=stale_after_seconds,
            freshness_state=freshness_state,
            age_seconds=age_seconds,
            updated_at=updated_at,
            consensus=consensus,
        )

    return ConsensusResponse(
        health=map_source_status_to_health(snapshot.source_status),
        freshness=FreshnessPublic(
            state="fresh",
            age_seconds=age_seconds,
            stale_after_seconds=stale_after_seconds,
            updated_at=updated_at,
        ),
        consensus=consensus,
    )


def evaluate_snapshot_payload(
    data: Any,
    *,
    now: datetime,
    stale_after_seconds: int,
    future_tolerance_seconds: int,
) -> ConsensusResponse:
    """Validate and evaluate a loaded snapshot payload into a public response."""
    try:
        snapshot = validate_snapshot(data)
    except SnapshotValidationError as exc:
        logger.info("consensus snapshot validation failed: %s", exc)
        return unknown_consensus_response(stale_after_seconds=stale_after_seconds)

    return evaluate_validated_snapshot(
        snapshot,
        now=now,
        stale_after_seconds=stale_after_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    )
