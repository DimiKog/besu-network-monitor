"""Pure infrastructure snapshot validation, freshness, and public-health mapping.

No I/O. Callers supply the parsed snapshot dict and a timezone-aware ``now``.

Reuses generic freshness / timestamp helpers from ``api.evaluation`` without
changing consensus validation behavior.

Infrastructure ``status`` already uses the public health vocabulary
(healthy|degraded|critical|unknown). Fresh responses expose that value as
``health`` only after semantic consistency with the infrastructure policy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Optional

from api.config import (
    EXPECTED_INFRASTRUCTURE_SYSTEMS,
    EXPECTED_INFRASTRUCTURE_VALIDATORS,
    SUPPORTED_INFRASTRUCTURE_SCHEMA_VERSION,
)
from api.evaluation import (
    SnapshotValidationError,
    evaluate_freshness,
    parse_generated_at,
)
from api.models import (
    FreshnessPublic,
    InfrastructurePublic,
    InfrastructureResponse,
    PublicHealth,
    RpcInfraPublic,
    ServicesPublic,
    SystemsPublic,
    ValidatorsInfraPublic,
)

logger = logging.getLogger(__name__)

InfrastructureStatus = Literal["healthy", "degraded", "critical", "unknown"]
INFRASTRUCTURE_STATUSES = frozenset(
    {"healthy", "degraded", "critical", "unknown"}
)

SERVICE_BOOL_KEYS: tuple[str, ...] = (
    "bootnode_up",
    "block_explorer_endpoint_up",
    "faucet_up",
    "identity_up",
    "backend_up",
    "prometheus_up",
    "grafana_endpoint_up",
)


@dataclass(frozen=True)
class ValidatedInfrastructureSnapshot:
    """Validated infrastructure snapshot fields for the public response."""

    schema_version: int
    generated_at: datetime
    generated_at_raw: str
    status: InfrastructureStatus
    systems_monitored: int
    systems_up: int
    systems_down: int
    validators_monitored: int
    validators_up: int
    validators_down: int
    primary_up: bool
    backup_up: bool
    bootnode_up: bool
    block_explorer_endpoint_up: bool
    faucet_up: bool
    identity_up: bool
    backend_up: bool
    prometheus_up: bool
    grafana_endpoint_up: bool


def _require_non_negative_int(data: Mapping[str, Any], key: str) -> int:
    raw = data.get(key)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SnapshotValidationError(f"{key} must be an integer")
    if raw < 0:
        raise SnapshotValidationError(f"{key} must be >= 0")
    return raw


def _require_bool(data: Mapping[str, Any], key: str) -> bool:
    raw = data.get(key)
    if not isinstance(raw, bool):
        raise SnapshotValidationError(f"{key} must be a boolean")
    return raw


def _require_object(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    raw = data.get(key)
    if not isinstance(raw, dict):
        raise SnapshotValidationError(f"{key} must be an object")
    return raw


def expected_infrastructure_status(
    *,
    systems_up: int,
    systems_monitored: int,
    primary_up: bool,
) -> InfrastructureStatus:
    """Derive health from complete inventory metrics (collector policy).

    - all systems UP → healthy
    - primary RPC DOWN → critical
    - any other loss with primary UP → degraded

    Does not consider QBFT quorum.
    """
    if systems_up == systems_monitored:
        return "healthy"
    if not primary_up:
        return "critical"
    return "degraded"


def validate_infrastructure_snapshot(data: Any) -> ValidatedInfrastructureSnapshot:
    """Validate the complete infrastructure snapshot before trusting it.

    Rejects contradictory status vs. the infrastructure health policy.
    Does not silently repair malformed data.
    """
    if not isinstance(data, dict):
        raise SnapshotValidationError("snapshot root must be a JSON object")

    schema_version = data.get("schema_version")
    if schema_version != SUPPORTED_INFRASTRUCTURE_SCHEMA_VERSION:
        raise SnapshotValidationError(
            f"unsupported schema_version: {schema_version!r} "
            f"(supported: {SUPPORTED_INFRASTRUCTURE_SCHEMA_VERSION!r})"
        )

    generated_at_raw = data.get("generated_at")
    generated_at = parse_generated_at(generated_at_raw)

    status = data.get("status")
    if not isinstance(status, str) or status not in INFRASTRUCTURE_STATUSES:
        raise SnapshotValidationError(
            "status must be one of: healthy, degraded, critical, unknown"
        )

    systems = _require_object(data, "systems")
    validators = _require_object(data, "validators")
    rpc = _require_object(data, "rpc")
    services = _require_object(data, "services")

    systems_monitored = _require_non_negative_int(systems, "monitored")
    systems_up = _require_non_negative_int(systems, "up")
    systems_down = _require_non_negative_int(systems, "down")

    validators_monitored = _require_non_negative_int(validators, "monitored")
    validators_up = _require_non_negative_int(validators, "up")
    validators_down = _require_non_negative_int(validators, "down")

    if systems_monitored != EXPECTED_INFRASTRUCTURE_SYSTEMS:
        raise SnapshotValidationError(
            f"systems.monitored must be {EXPECTED_INFRASTRUCTURE_SYSTEMS}"
        )
    if validators_monitored != EXPECTED_INFRASTRUCTURE_VALIDATORS:
        raise SnapshotValidationError(
            f"validators.monitored must be {EXPECTED_INFRASTRUCTURE_VALIDATORS}"
        )

    if systems_up + systems_down != systems_monitored:
        raise SnapshotValidationError(
            "systems.up + systems.down must equal systems.monitored"
        )
    if validators_up + validators_down != validators_monitored:
        raise SnapshotValidationError(
            "validators.up + validators.down must equal validators.monitored"
        )
    if validators_up > validators_monitored:
        raise SnapshotValidationError(
            "validators.up must be <= validators.monitored"
        )
    if validators_down > validators_monitored:
        raise SnapshotValidationError(
            "validators.down must be <= validators.monitored"
        )
    if systems_up > systems_monitored:
        raise SnapshotValidationError(
            "systems.up must be <= systems.monitored"
        )

    primary_up = _require_bool(rpc, "primary_up")
    backup_up = _require_bool(rpc, "backup_up")

    service_bools: dict[str, bool] = {}
    for key in SERVICE_BOOL_KEYS:
        service_bools[key] = _require_bool(services, key)

    non_validator_up = sum(
        1
        for flag in (
            primary_up,
            backup_up,
            *(service_bools[key] for key in SERVICE_BOOL_KEYS),
        )
        if flag
    )
    # 7 validators + 2 RPC + 7 services = 16 systems.
    derived_systems_up = validators_up + non_validator_up
    if derived_systems_up != systems_up:
        raise SnapshotValidationError(
            "systems.up is inconsistent with validators/rpc/services booleans"
        )
    derived_systems_down = systems_monitored - derived_systems_up
    if derived_systems_down != systems_down:
        raise SnapshotValidationError(
            "systems.down is inconsistent with validators/rpc/services booleans"
        )

    expected = expected_infrastructure_status(
        systems_up=systems_up,
        systems_monitored=systems_monitored,
        primary_up=primary_up,
    )
    if status != expected:
        raise SnapshotValidationError(
            "status is inconsistent with infrastructure health policy"
        )

    return ValidatedInfrastructureSnapshot(
        schema_version=schema_version,
        generated_at=generated_at,
        generated_at_raw=str(generated_at_raw).strip(),
        status=status,  # type: ignore[arg-type]
        systems_monitored=systems_monitored,
        systems_up=systems_up,
        systems_down=systems_down,
        validators_monitored=validators_monitored,
        validators_up=validators_up,
        validators_down=validators_down,
        primary_up=primary_up,
        backup_up=backup_up,
        bootnode_up=service_bools["bootnode_up"],
        block_explorer_endpoint_up=service_bools["block_explorer_endpoint_up"],
        faucet_up=service_bools["faucet_up"],
        identity_up=service_bools["identity_up"],
        backend_up=service_bools["backend_up"],
        prometheus_up=service_bools["prometheus_up"],
        grafana_endpoint_up=service_bools["grafana_endpoint_up"],
    )


def _empty_infrastructure() -> InfrastructurePublic:
    return InfrastructurePublic(
        systems=SystemsPublic(),
        validators=ValidatorsInfraPublic(),
        rpc=RpcInfraPublic(),
        services=ServicesPublic(),
    )


def _filled_infrastructure(
    snapshot: ValidatedInfrastructureSnapshot,
) -> InfrastructurePublic:
    return InfrastructurePublic(
        systems=SystemsPublic(
            monitored=snapshot.systems_monitored,
            up=snapshot.systems_up,
            down=snapshot.systems_down,
        ),
        validators=ValidatorsInfraPublic(
            monitored=snapshot.validators_monitored,
            up=snapshot.validators_up,
            down=snapshot.validators_down,
        ),
        rpc=RpcInfraPublic(
            primary_up=snapshot.primary_up,
            backup_up=snapshot.backup_up,
        ),
        services=ServicesPublic(
            bootnode_up=snapshot.bootnode_up,
            block_explorer_endpoint_up=snapshot.block_explorer_endpoint_up,
            faucet_up=snapshot.faucet_up,
            identity_up=snapshot.identity_up,
            backend_up=snapshot.backend_up,
            prometheus_up=snapshot.prometheus_up,
            grafana_endpoint_up=snapshot.grafana_endpoint_up,
        ),
    )


def unknown_infrastructure_response(
    *,
    stale_after_seconds: int,
    freshness_state: Literal["fresh", "stale", "unknown"] = "unknown",
    age_seconds: Optional[float] = None,
    updated_at: Optional[str] = None,
    infrastructure: Optional[InfrastructurePublic] = None,
) -> InfrastructureResponse:
    """Build a public response when infrastructure health cannot be established."""
    return InfrastructureResponse(
        health="unknown",
        freshness=FreshnessPublic(
            state=freshness_state,
            age_seconds=age_seconds,
            stale_after_seconds=stale_after_seconds,
            updated_at=updated_at,
        ),
        infrastructure=(
            infrastructure
            if infrastructure is not None
            else _empty_infrastructure()
        ),
    )


def evaluate_validated_infrastructure(
    snapshot: ValidatedInfrastructureSnapshot,
    *,
    now: datetime,
    stale_after_seconds: int,
    future_tolerance_seconds: int,
) -> InfrastructureResponse:
    """Map a validated infrastructure snapshot to the public response."""
    freshness_state, age_seconds = evaluate_freshness(
        snapshot.generated_at,
        now=now,
        stale_after_seconds=stale_after_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    )
    updated_at = snapshot.generated_at.isoformat().replace("+00:00", "Z")
    infrastructure = _filled_infrastructure(snapshot)

    if freshness_state == "fresh":
        # Snapshot status already uses public vocabulary and was policy-checked.
        # Complete inventory never yields status=unknown under the collector policy
        # (that case is rejected during validation as contradictory).
        health: PublicHealth = snapshot.status  # type: ignore[assignment]
        return InfrastructureResponse(
            health=health,
            freshness=FreshnessPublic(
                state="fresh",
                age_seconds=age_seconds,
                stale_after_seconds=stale_after_seconds,
                updated_at=updated_at,
            ),
            infrastructure=infrastructure,
        )

    if freshness_state == "stale":
        # Stale: do not assert current health; retain last-known metrics.
        return unknown_infrastructure_response(
            stale_after_seconds=stale_after_seconds,
            freshness_state="stale",
            age_seconds=age_seconds,
            updated_at=updated_at,
            infrastructure=infrastructure,
        )

    # Future beyond tolerance: freshness unknown; do not expose metrics.
    return unknown_infrastructure_response(
        stale_after_seconds=stale_after_seconds,
        freshness_state="unknown",
        age_seconds=age_seconds,
        updated_at=None,
        infrastructure=None,
    )


def evaluate_infrastructure_payload(
    data: Any,
    *,
    now: datetime,
    stale_after_seconds: int,
    future_tolerance_seconds: int,
) -> InfrastructureResponse:
    """Validate and evaluate a loaded infrastructure snapshot payload."""
    try:
        snapshot = validate_infrastructure_snapshot(data)
    except SnapshotValidationError as exc:
        logger.info("infrastructure snapshot validation failed: %s", exc)
        return unknown_infrastructure_response(
            stale_after_seconds=stale_after_seconds,
        )

    return evaluate_validated_infrastructure(
        snapshot,
        now=now,
        stale_after_seconds=stale_after_seconds,
        future_tolerance_seconds=future_tolerance_seconds,
    )
