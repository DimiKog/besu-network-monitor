#!/usr/bin/env python3
"""Trusted collector: query Besu QBFT validators and emit a sanitized snapshot.

Designed to run on the Prometheus monitoring VM. Reuses consensus semantics
from scripts/vm_consensus_reference.py (reference material; not imported).

Stdlib only. Private key material is never embedded or logged.
Live SSH is optional for unit tests via an injectable RPC transport.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# ============================================================
# CONFIGURATION (collector host only; never emitted in output)
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = BASE_DIR / "output" / "consensus_snapshot.json"

# Matches CONSENSUS_DEFAULT_MAX_DRIFT_BLOCKS from the production inventory
# used by vm_consensus_reference.py (fixture / ops default: 2).
DEFAULT_MAX_DRIFT_BLOCKS = 2

DEFAULT_SSH_USER = "ubuntu"
DEFAULT_SSH_IDENTITY = Path.home() / ".ssh" / "besu_monitor_collector"
DEFAULT_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_SSH_CONNECT_TIMEOUT = 10
DEFAULT_COMMAND_TIMEOUT = 20
DEFAULT_WORKERS = 7

# Internal collector inventory. IPs/aliases must never appear in sanitized output.
VALIDATORS: tuple[dict[str, Any], ...] = (
    {"alias": "validator1", "ip": "195.251.92.253"},
    {"alias": "validator2", "ip": "195.251.92.233"},
    {"alias": "validator3", "ip": "195.251.92.163"},
    {"alias": "validator4", "ip": "83.212.77.14"},
    {"alias": "validator5", "ip": "83.212.77.31"},
    {"alias": "validator6", "ip": "83.212.77.13"},
    {"alias": "validator7", "ip": "83.212.80.117"},
)

ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class CollectorError(Exception):
    """Collection or sanitization failed; previous snapshot must be preserved."""


RpcTransport = Callable[[str, str, str, list[Any], int], str]
# (alias, ip, method, params, timeout) -> raw stdout / error text


@dataclass(frozen=True)
class SshSettings:
    user: str = DEFAULT_SSH_USER
    identity: Path = DEFAULT_SSH_IDENTITY
    rpc_url: str = DEFAULT_RPC_URL
    connect_timeout: int = DEFAULT_SSH_CONNECT_TIMEOUT
    command_timeout: int = DEFAULT_COMMAND_TIMEOUT


# ============================================================
# PURE HELPERS (adapted from vm_consensus_reference.py)
# ============================================================


def qbft_quorum_threshold(validator_count: int) -> int:
    """QBFT/IBFT2 quorum: 2 * floor(N/3) + 1 (== ceil(2N/3) for N>=1 except N%3==0).

    Matches production qbft_quorum_threshold used by the reference script.
    For N=7 -> 5.
    """
    if not isinstance(validator_count, int) or isinstance(validator_count, bool):
        raise CollectorError("validator_count must be an integer")
    if validator_count < 1:
        raise CollectorError(
            f"validator_count must be >= 1, got {validator_count}"
        )
    return 2 * (validator_count // 3) + 1


def parse_hex_int(value: Any) -> int:
    """Parse a JSON-RPC hex quantity or int into a non-negative int."""
    if isinstance(value, bool):
        raise CollectorError("block/quantity must not be a boolean")
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            number = int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError as exc:
            raise CollectorError(f"invalid hex quantity: {value!r}") from exc
    else:
        raise CollectorError(f"invalid hex quantity type: {type(value).__name__}")
    if number < 0:
        raise CollectorError(f"block/quantity must be non-negative, got {number}")
    return number


def parse_block_number_response(text: str) -> int | None:
    """Return block height, or None if the response is unusable (per reference)."""
    if not text or "unavailable" in text.lower() or '"result"' not in text:
        return None
    if text.startswith("ERROR:"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return parse_hex_int(payload.get("result"))
    except CollectorError:
        return None


def parse_validator_set_response(text: str) -> list[str] | None:
    """Return lowercased validator addresses, or None if unusable."""
    if not text or "unavailable" in text.lower() or '"result"' not in text:
        return None
    if text.startswith("ERROR:"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, list) or not result:
        return None
    addresses: list[str] = []
    for item in result:
        if not isinstance(item, str) or not ETH_ADDRESS_RE.fullmatch(item):
            return None
        addresses.append(item.lower())
    return addresses


def make_rpc_payload(method: str, params: list[Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        separators=(",", ":"),
    )


# ============================================================
# SSH TRANSPORT
# ============================================================


def _ssh_base_argv(settings: SshSettings, ip: str) -> list[str]:
    identity = settings.identity.expanduser()
    return [
        "ssh",
        "-i",
        str(identity),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={settings.connect_timeout}",
        "-o",
        "IdentitiesOnly=yes",
        f"{settings.user}@{ip}",
    ]


def run_ssh_command(
    settings: SshSettings,
    ip: str,
    remote_command: str,
    timeout: int,
) -> dict[str, Any]:
    """Run a fixed remote command over SSH without shell=True."""
    import subprocess

    identity = settings.identity.expanduser()
    if not identity.is_file():
        return {
            "returncode": 255,
            "stdout": "",
            "stderr": "",
            "error": f"SSH identity file not found: {identity}",
        }

    argv = _ssh_base_argv(settings, ip) + [remote_command]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "returncode": 255,
            "stdout": "",
            "stderr": "",
            "error": f"SSH/command timed out after {timeout}s",
        }
    except OSError as exc:
        return {
            "returncode": 255,
            "stdout": "",
            "stderr": "",
            "error": f"SSH failed to start: {exc}",
        }

    return {
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
        "error": None,
    }


def make_rpc_remote_command(rpc_url: str, method: str, params: list[Any]) -> str:
    """Build a remote curl command. Method/params are locally controlled."""
    payload = make_rpc_payload(method, params)
    # Single remote argv to ssh; shell-quoted so JSON is not reinterpreted.
    return (
        "curl -sS --max-time 10 "
        "-H 'Content-Type: application/json' "
        f"-d {shlex.quote(payload)} "
        f"{shlex.quote(rpc_url)}"
    )


def ssh_rpc_transport(
    settings: SshSettings,
) -> RpcTransport:
    def _query(
        alias: str, ip: str, method: str, params: list[Any], timeout: int
    ) -> str:
        del alias  # used only by callers for logging context
        remote = make_rpc_remote_command(settings.rpc_url, method, params)
        result = run_ssh_command(settings, ip, remote, timeout=timeout)
        if result["error"]:
            return f"ERROR: {result['error']}"
        if result["returncode"] != 0:
            detail = result["stderr"] or result["stdout"] or "ssh failed"
            return f"ERROR: {detail}"
        return result["stdout"]

    return _query


def ssh_connectivity_ok(settings: SshSettings, ip: str, timeout: int) -> tuple[bool, str]:
    result = run_ssh_command(settings, ip, "echo ok", timeout=timeout)
    if result["error"]:
        return False, result["error"]
    if result["returncode"] != 0 or result["stdout"] != "ok":
        return False, result["stderr"] or result["stdout"] or "no response"
    return True, ""


# ============================================================
# COLLECTION (adapted from vm_consensus_reference.py)
# ============================================================


def check_validator_block(
    alias: str,
    ip: str,
    transport: RpcTransport,
    *,
    connectivity_probe: Callable[[str, str, int], tuple[bool, str]] | None,
    timeout: int,
) -> dict[str, Any]:
    """Query eth_blockNumber for one validator (internal result; not public)."""
    if connectivity_probe is not None:
        ok, err = connectivity_probe(alias, ip, timeout)
        if not ok:
            return {
                "alias": alias,
                "ip": ip,
                "status": "unreachable",
                "error": err or "no response",
                "block_number": None,
                "drift_blocks": None,
                "synced": False,
            }

    raw = transport(alias, ip, "eth_blockNumber", [], timeout)
    block = parse_block_number_response(raw)
    if block is None:
        return {
            "alias": alias,
            "ip": ip,
            "status": "rpc_error",
            "error": raw if raw.startswith("ERROR:") else "eth_blockNumber unavailable",
            "block_number": None,
            "drift_blocks": None,
            "synced": False,
        }

    return {
        "alias": alias,
        "ip": ip,
        "status": "ok",
        "error": None,
        "block_number": block,
        "drift_blocks": None,
        "synced": False,
    }


def fetch_active_validator_set(
    candidates: Sequence[Mapping[str, Any]],
    transport: RpcTransport,
    *,
    timeout: int,
    block_tag: int | str = "latest",
) -> dict[str, Any]:
    """Query qbft_getValidatorsByBlockNumber from the first usable validator."""
    if block_tag == "latest":
        params: list[Any] = ["latest"]
    elif isinstance(block_tag, int):
        params = [hex(block_tag)]
    else:
        params = [str(block_tag)]

    for entry in candidates:
        if entry.get("status") != "ok":
            continue
        alias = str(entry["alias"])
        ip = str(entry["ip"])
        raw = transport(alias, ip, "qbft_getValidatorsByBlockNumber", params, timeout)
        addresses = parse_validator_set_response(raw)
        if addresses is not None:
            count = len(addresses)
            return {
                "source_alias": alias,
                "block_tag": block_tag,
                "validator_addresses": addresses,
                "validator_count": count,
                "quorum_required": qbft_quorum_threshold(count),
            }

    return {
        "source_alias": None,
        "block_tag": block_tag,
        "validator_addresses": [],
        "validator_count": 0,
        "quorum_required": 0,
        "error": "qbft_getValidatorsByBlockNumber unavailable on all queried validators",
    }


def build_consensus_report(
    validator_results: list[dict[str, Any]],
    max_drift_blocks: int,
    validator_set: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute drift, sync status, and quorum (reference semantics)."""
    if not isinstance(max_drift_blocks, int) or isinstance(max_drift_blocks, bool):
        raise CollectorError("max_drift_blocks must be an integer")
    if max_drift_blocks < 0:
        raise CollectorError("max_drift_blocks must be >= 0")

    reachable = [r for r in validator_results if r.get("block_number") is not None]
    reference_block = max((r["block_number"] for r in reachable), default=None)

    out_of_sync: list[dict[str, Any]] = []
    synced_aliases: list[str] = []

    for entry in validator_results:
        block = entry.get("block_number")
        if block is None or reference_block is None:
            entry["drift_blocks"] = None
            entry["synced"] = False
            continue

        if not isinstance(block, int) or isinstance(block, bool):
            raise CollectorError(
                f"invalid block_number for {entry.get('alias')!r}: {block!r}"
            )
        drift = reference_block - block
        if drift < 0:
            # Head should be the max; a negative drift indicates inconsistent input.
            raise CollectorError(
                f"negative drift for {entry.get('alias')!r}: "
                f"reference={reference_block}, block={block}"
            )
        entry["drift_blocks"] = drift
        entry["synced"] = drift <= max_drift_blocks
        if entry["synced"]:
            synced_aliases.append(str(entry["alias"]))
        elif drift > max_drift_blocks:
            out_of_sync.append(
                {
                    "alias": entry["alias"],
                    "block_number": block,
                    "reference_block": reference_block,
                    "drift_blocks": drift,
                }
            )

    active_count = int(validator_set.get("validator_count") or 0)
    quorum_required = int(validator_set.get("quorum_required") or 0)
    synced_count = len(synced_aliases)
    has_quorum = synced_count >= quorum_required if quorum_required else False

    unreachable = [r["alias"] for r in validator_results if r.get("status") == "unreachable"]
    rpc_errors = [r["alias"] for r in validator_results if r.get("status") == "rpc_error"]

    if reference_block is None:
        overall = "critical"
    elif not has_quorum and active_count:
        overall = "critical"
    elif out_of_sync or unreachable or rpc_errors:
        overall = "warning"
    else:
        overall = "ok"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_drift_blocks": max_drift_blocks,
        "reference_block": reference_block,
        "overall_status": overall,
        "active_validator_set": dict(validator_set),
        "synced_validators": synced_count,
        "has_quorum": has_quorum,
        "validators": validator_results,
        "out_of_sync": out_of_sync,
    }


def observed_max_blocks(validator_results: Sequence[Mapping[str, Any]]) -> int:
    """Max observed drift among validators with a known drift_blocks value."""
    drifts: list[int] = []
    for entry in validator_results:
        drift = entry.get("drift_blocks")
        if drift is None:
            continue
        if isinstance(drift, bool) or not isinstance(drift, int) or drift < 0:
            raise CollectorError(f"invalid drift_blocks value: {drift!r}")
        drifts.append(drift)
    if not drifts:
        raise CollectorError(
            "no observed validator drift values available for observed_max_blocks"
        )
    return max(drifts)


def sanitize_consensus_snapshot(report: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce an internal consensus report to the public-safe Monitor contract."""
    reference_block = report.get("reference_block")
    if reference_block is None:
        raise CollectorError("no validator returned a usable eth_blockNumber")
    if isinstance(reference_block, bool) or not isinstance(reference_block, int):
        raise CollectorError("reference_block must be an integer")

    validator_set = report.get("active_validator_set")
    if not isinstance(validator_set, dict):
        raise CollectorError("active_validator_set missing")
    if validator_set.get("error") or not validator_set.get("validator_count"):
        raise CollectorError(
            validator_set.get("error")
            or "active validator set unavailable"
        )

    validator_count = validator_set.get("validator_count")
    quorum_required = validator_set.get("quorum_required")
    if isinstance(validator_count, bool) or not isinstance(validator_count, int):
        raise CollectorError("validator_count must be an integer")
    if isinstance(quorum_required, bool) or not isinstance(quorum_required, int):
        raise CollectorError("quorum_required must be an integer")
    if quorum_required != qbft_quorum_threshold(validator_count):
        raise CollectorError(
            "quorum_required does not match QBFT formula for validator_count"
        )

    allowed = report.get("max_drift_blocks")
    if isinstance(allowed, bool) or not isinstance(allowed, int) or allowed < 0:
        raise CollectorError("max_drift_blocks/allowed_max_blocks invalid")

    validators = report.get("validators")
    if not isinstance(validators, list) or not validators:
        raise CollectorError("validators list missing from internal report")

    observed = observed_max_blocks(validators)
    out_of_sync = report.get("out_of_sync")
    if not isinstance(out_of_sync, list):
        raise CollectorError("out_of_sync must be a list")

    synced = report.get("synced_validators")
    has_quorum = report.get("has_quorum")
    status = report.get("overall_status")
    generated_at = report.get("generated_at")
    if isinstance(synced, bool) or not isinstance(synced, int) or synced < 0:
        raise CollectorError("synced_validators must be a non-negative integer")
    if not isinstance(has_quorum, bool):
        raise CollectorError("has_quorum must be a boolean")
    if not isinstance(status, str) or not status:
        raise CollectorError("overall_status/status missing")
    if not isinstance(generated_at, str) or not generated_at:
        raise CollectorError("generated_at missing")

    # Public-safe fields only. No IPs, aliases, addresses, raw RPC, or rows.
    return {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "status": status,
        "reference_block": reference_block,
        "validator_count": validator_count,
        "synced_validators": synced,
        "quorum_required": quorum_required,
        "has_quorum": has_quorum,
        "drift": {
            "observed_max_blocks": observed,
            "allowed_max_blocks": allowed,
        },
        "out_of_sync_count": len(out_of_sync),
    }


def collect_validator_blocks(
    validators: Sequence[Mapping[str, Any]],
    transport: RpcTransport,
    *,
    connectivity_probe: Callable[[str, str, int], tuple[bool, str]] | None,
    timeout: int,
    workers: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(
                check_validator_block,
                str(v["alias"]),
                str(v["ip"]),
                transport,
                connectivity_probe=connectivity_probe,
                timeout=timeout,
            ): v["alias"]
            for v in validators
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda r: str(r["alias"]))
    return results


def run_collection(
    *,
    transport: RpcTransport,
    connectivity_probe: Callable[[str, str, int], tuple[bool, str]] | None,
    max_drift_blocks: int = DEFAULT_MAX_DRIFT_BLOCKS,
    timeout: int = DEFAULT_COMMAND_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
    validators: Sequence[Mapping[str, Any]] = VALIDATORS,
) -> dict[str, Any]:
    """Run full collection and return the sanitized public snapshot."""
    results = collect_validator_blocks(
        validators,
        transport,
        connectivity_probe=connectivity_probe,
        timeout=timeout,
        workers=workers,
    )
    reference_block = max(
        (r["block_number"] for r in results if r.get("block_number") is not None),
        default=None,
    )
    if reference_block is None:
        raise CollectorError("No validator returned a usable eth_blockNumber")

    validator_set = fetch_active_validator_set(
        results,
        transport,
        timeout=timeout,
        block_tag=reference_block,
    )
    if validator_set.get("error") or not validator_set.get("validator_count"):
        raise CollectorError(
            str(
                validator_set.get("error")
                or "active validator set unavailable"
            )
        )

    report = build_consensus_report(results, max_drift_blocks, validator_set)
    return sanitize_consensus_snapshot(report)


# ============================================================
# ATOMIC OUTPUT
# ============================================================


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write JSON via temp file + os.replace. Does not leave partial output."""
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
# CLI
# ============================================================


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect Besu QBFT consensus telemetry via SSH+localhost RPC "
            "and write a sanitized public-safe snapshot."
        )
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--max-drift",
        type=int,
        default=DEFAULT_MAX_DRIFT_BLOCKS,
        help=(
            "Allowed sync drift in blocks "
            f"(default: {DEFAULT_MAX_DRIFT_BLOCKS})"
        ),
    )
    parser.add_argument(
        "--ssh-user",
        default=DEFAULT_SSH_USER,
        help=f"SSH username (default: {DEFAULT_SSH_USER})",
    )
    parser.add_argument(
        "--ssh-identity",
        type=Path,
        default=DEFAULT_SSH_IDENTITY,
        help=f"SSH identity file (default: {DEFAULT_SSH_IDENTITY})",
    )
    parser.add_argument(
        "--rpc-url",
        default=DEFAULT_RPC_URL,
        help=f"Remote Besu RPC URL via SSH (default: {DEFAULT_RPC_URL})",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=DEFAULT_SSH_CONNECT_TIMEOUT,
        help=f"SSH ConnectTimeout seconds (default: {DEFAULT_SSH_CONNECT_TIMEOUT})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_COMMAND_TIMEOUT,
        help=f"Per-validator command timeout seconds (default: {DEFAULT_COMMAND_TIMEOUT})",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel SSH workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--skip-connectivity-probe",
        action="store_true",
        help="Skip the echo-ok SSH probe and go straight to eth_blockNumber",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = SshSettings(
        user=args.ssh_user,
        identity=args.ssh_identity,
        rpc_url=args.rpc_url,
        connect_timeout=args.connect_timeout,
        command_timeout=args.timeout,
    )

    identity = settings.identity.expanduser()
    if not identity.is_file():
        print(
            f"ERROR: SSH identity file not found: {identity}",
            file=sys.stderr,
        )
        return 1

    transport = ssh_rpc_transport(settings)

    def probe(alias: str, ip: str, timeout: int) -> tuple[bool, str]:
        del alias
        return ssh_connectivity_ok(settings, ip, timeout)

    connectivity_probe = None if args.skip_connectivity_probe else probe

    print(
        f"collect_consensus.py — checking {len(VALIDATORS)} validator(s) "
        f"with {args.workers} workers; max drift={args.max_drift}",
        flush=True,
    )

    try:
        snapshot = run_collection(
            transport=transport,
            connectivity_probe=connectivity_probe,
            max_drift_blocks=args.max_drift,
            timeout=args.timeout,
            workers=args.workers,
        )
        atomic_write_json(args.output, snapshot)
    except CollectorError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: failed to write output: {exc}", file=sys.stderr)
        return 1

    print(f"Created: {args.output.resolve()}")
    print(f"status={snapshot['status']} reference_block={snapshot['reference_block']} "
          f"synced={snapshot['synced_validators']}/{snapshot['validator_count']} "
          f"has_quorum={snapshot['has_quorum']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
