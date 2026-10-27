#!/usr/bin/env python3
"""Build a sanitized public-safe monitor snapshot from internal reports.

Stdlib only. Raw reports must never be copied into the output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_FILE = OUTPUT_DIR / "monitor_snapshot.json"

DAILY_GLOB = "daily_*.json"
CONSENSUS_GLOB = "consensus_*.json"
FILENAME_TS_RE = re.compile(
    r"^(?:daily|consensus)_(\d{8})_(\d{6})\.json$"
)


class SnapshotError(Exception):
    """Raised when input is missing, mismatched, or malformed."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transform internal Besu daily/consensus monitoring reports "
            "into a sanitized public-safe JSON snapshot."
        )
    )
    parser.add_argument(
        "--daily",
        type=Path,
        help="Path to a daily_*.json report (default: newest under input/)",
    )
    parser.add_argument(
        "--consensus",
        type=Path,
        help="Path to a consensus_*.json report (default: newest under input/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_FILE,
        help=f"Output path (default: {OUTPUT_FILE})",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SnapshotError(f"Input file not found: {path}")
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SnapshotError(f"Expected a JSON object in {path}")
    return data


def filename_sort_key(path: Path) -> tuple[str, str, float]:
    """Prefer embedded YYYYMMDD_HHMMSS; fall back to mtime."""
    match = FILENAME_TS_RE.match(path.name)
    if match:
        return (match.group(1), match.group(2), path.stat().st_mtime)
    return ("00000000", "000000", path.stat().st_mtime)


def newest_matching(directory: Path, pattern: str) -> Path:
    files = list(directory.glob(pattern))
    if not files:
        raise SnapshotError(
            f"No files matching {pattern!r} under {directory}"
        )
    return max(files, key=filename_sort_key)


def resolve_inputs(
    daily_arg: Path | None, consensus_arg: Path | None
) -> tuple[Path, Path]:
    daily_path = (
        daily_arg.expanduser().resolve()
        if daily_arg is not None
        else newest_matching(INPUT_DIR, DAILY_GLOB)
    )
    consensus_path = (
        consensus_arg.expanduser().resolve()
        if consensus_arg is not None
        else newest_matching(INPUT_DIR, CONSENSUS_GLOB)
    )
    return daily_path, consensus_path


def require_mapping(obj: Any, path: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise SnapshotError(f"Expected object at {path}")
    return obj


def require_list(obj: Any, path: str) -> list[Any]:
    if not isinstance(obj, list):
        raise SnapshotError(f"Expected list at {path}")
    return obj


def require_keys(mapping: dict[str, Any], keys: list[str], path: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise SnapshotError(
            f"Missing required field(s) at {path}: {', '.join(missing)}"
        )


def require_int(mapping: dict[str, Any], key: str, path: str) -> int:
    value = mapping[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError(f"Expected integer at {path}.{key}")
    return value


def require_bool(mapping: dict[str, Any], key: str, path: str) -> bool:
    value = mapping[key]
    if not isinstance(value, bool):
        raise SnapshotError(f"Expected boolean at {path}.{key}")
    return value


def require_str(mapping: dict[str, Any], key: str, path: str) -> str:
    value = mapping[key]
    if not isinstance(value, str) or not value:
        raise SnapshotError(f"Expected non-empty string at {path}.{key}")
    return value


def parse_report_date(generated_at: str, source: str) -> str:
    """Return YYYY-MM-DD from an ISO-8601 timestamp."""
    text = generated_at.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SnapshotError(
            f"Invalid generated_at in {source}: {generated_at!r}"
        ) from exc
    return dt.date().isoformat()


def validate_daily(daily: dict[str, Any]) -> dict[str, Any]:
    require_keys(daily, ["generated_at", "network_summary"], "daily")
    generated_at = require_str(daily, "generated_at", "daily")

    network = require_mapping(daily["network_summary"], "daily.network_summary")
    require_keys(
        network,
        ["overall_status", "counts", "besu", "rpc_nodes"],
        "daily.network_summary",
    )
    require_str(network, "overall_status", "daily.network_summary")

    counts = require_mapping(network["counts"], "daily.network_summary.counts")
    require_keys(
        counts,
        ["total", "reachable", "unreachable", "ok", "warning", "critical"],
        "daily.network_summary.counts",
    )
    for key in ("total", "reachable", "unreachable", "ok", "warning", "critical"):
        require_int(counts, key, "daily.network_summary.counts")

    besu = require_mapping(network["besu"], "daily.network_summary.besu")
    require_keys(
        besu,
        [
            "validators_checked",
            "validators_active",
            "min_peers",
            "max_peers",
            "low_peer_nodes",
        ],
        "daily.network_summary.besu",
    )
    for key in (
        "validators_checked",
        "validators_active",
        "min_peers",
        "max_peers",
    ):
        require_int(besu, key, "daily.network_summary.besu")
    require_list(besu["low_peer_nodes"], "daily.network_summary.besu.low_peer_nodes")

    rpc = require_mapping(network["rpc_nodes"], "daily.network_summary.rpc_nodes")
    require_keys(
        rpc,
        ["checked", "responding", "unavailable"],
        "daily.network_summary.rpc_nodes",
    )
    require_int(rpc, "checked", "daily.network_summary.rpc_nodes")
    require_int(rpc, "responding", "daily.network_summary.rpc_nodes")
    require_list(rpc["unavailable"], "daily.network_summary.rpc_nodes.unavailable")

    return {
        "generated_at": generated_at,
        "report_date": parse_report_date(generated_at, "daily"),
        "network": network,
        "besu": besu,
        "rpc": rpc,
        "counts": counts,
    }


def observed_max_drift_blocks(validators: list[Any]) -> int:
    """Max of validators[].drift_blocks. Fails if data is absent/malformed."""
    if not validators:
        raise SnapshotError(
            "consensus.validators is empty; cannot compute observed_max_blocks"
        )

    drifts: list[int] = []
    for index, entry in enumerate(validators):
        path = f"consensus.validators[{index}]"
        mapping = require_mapping(entry, path)
        require_keys(mapping, ["drift_blocks"], path)
        drifts.append(require_int(mapping, "drift_blocks", path))

    return max(drifts)


def validate_consensus(consensus: dict[str, Any]) -> dict[str, Any]:
    require_keys(
        consensus,
        [
            "generated_at",
            "overall_status",
            "reference_block",
            "synced_validators",
            "has_quorum",
            "max_drift_blocks",
            "out_of_sync",
            "validators",
            "active_validator_set",
        ],
        "consensus",
    )
    generated_at = require_str(consensus, "generated_at", "consensus")
    require_str(consensus, "overall_status", "consensus")
    require_int(consensus, "reference_block", "consensus")
    require_int(consensus, "synced_validators", "consensus")
    require_bool(consensus, "has_quorum", "consensus")
    allowed_max_blocks = require_int(consensus, "max_drift_blocks", "consensus")
    require_list(consensus["out_of_sync"], "consensus.out_of_sync")
    validators = require_list(consensus["validators"], "consensus.validators")
    observed_max_blocks = observed_max_drift_blocks(validators)

    validator_set = require_mapping(
        consensus["active_validator_set"], "consensus.active_validator_set"
    )
    require_keys(
        validator_set,
        ["validator_count", "quorum_required"],
        "consensus.active_validator_set",
    )
    require_int(validator_set, "validator_count", "consensus.active_validator_set")
    require_int(validator_set, "quorum_required", "consensus.active_validator_set")

    return {
        "generated_at": generated_at,
        "report_date": parse_report_date(generated_at, "consensus"),
        "validator_set": validator_set,
        "observed_max_blocks": observed_max_blocks,
        "allowed_max_blocks": allowed_max_blocks,
    }


def ensure_same_report_date(daily_date: str, consensus_date: str) -> None:
    if daily_date != consensus_date:
        raise SnapshotError(
            "Refusing to combine reports from different calendar dates: "
            f"daily={daily_date}, consensus={consensus_date}"
        )


def build_snapshot(
    daily_raw: dict[str, Any], consensus_raw: dict[str, Any]
) -> dict[str, Any]:
    daily = validate_daily(daily_raw)
    consensus = validate_consensus(consensus_raw)
    ensure_same_report_date(daily["report_date"], consensus["report_date"])

    network = daily["network"]
    besu = daily["besu"]
    rpc = daily["rpc"]
    counts = daily["counts"]
    validator_set = consensus["validator_set"]

    # Only aggregate, public-safe fields. Never copy nested VM/validator
    # objects, peer maps, addresses, raw_response, IPs, hostnames, or paths.
    return {
        "schema_version": "1.0",
        "generated_at": {
            "infrastructure": daily["generated_at"],
            "consensus": consensus["generated_at"],
        },
        "infrastructure": {
            "status": network["overall_status"],
            "vms": {
                "total": counts["total"],
                "reachable": counts["reachable"],
                "unreachable": counts["unreachable"],
                "ok": counts["ok"],
                "warning": counts["warning"],
                "critical": counts["critical"],
            },
            "validators": {
                "checked": besu["validators_checked"],
                "active": besu["validators_active"],
                "min_peers": besu["min_peers"],
                "max_peers": besu["max_peers"],
                "low_peer_count": len(besu["low_peer_nodes"]),
            },
            "rpc": {
                "checked": rpc["checked"],
                "responding": rpc["responding"],
                "unavailable_count": len(rpc["unavailable"]),
            },
        },
        "consensus": {
            "status": consensus_raw["overall_status"],
            "reference_block": consensus_raw["reference_block"],
            "validator_count": validator_set["validator_count"],
            "synced_validators": consensus_raw["synced_validators"],
            "quorum_required": validator_set["quorum_required"],
            "has_quorum": consensus_raw["has_quorum"],
            "drift": {
                "observed_max_blocks": consensus["observed_max_blocks"],
                "allowed_max_blocks": consensus["allowed_max_blocks"],
            },
            "out_of_sync_count": len(consensus_raw["out_of_sync"]),
        },
    }


def write_snapshot(snapshot: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
        f.write("\n")


def main() -> int:
    args = parse_args()
    try:
        daily_path, consensus_path = resolve_inputs(args.daily, args.consensus)
        daily_raw = load_json(daily_path)
        consensus_raw = load_json(consensus_path)
        snapshot = build_snapshot(daily_raw, consensus_raw)
        write_snapshot(snapshot, args.output)
    except SnapshotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Daily input:      {daily_path}")
    print(f"Consensus input:  {consensus_path}")
    print(f"Created:          {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
