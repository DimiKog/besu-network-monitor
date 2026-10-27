#!/usr/bin/env python3
"""
vm_consensus.py — Besu QBFT block sync and quorum comparator.

Queries eth_blockNumber from validators in parallel, flags nodes that drift
more than N blocks from the network head, and evaluates QBFT quorum against
the active validator set from qbft_getValidatorsByBlockNumber.

Usage:
    python3 vm_consensus.py
    python3 vm_consensus.py --max-drift 5
    python3 vm_consensus.py validator1 validator3
    python3 vm_consensus.py --output consensus.json
    python3 vm_consensus.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from vm_inventory import (
    CONSENSUS_DEFAULT_MAX_DRIFT_BLOCKS,
    VMS,
    is_besu_validator,
    qbft_quorum_threshold,
    validator_aliases,
)
from vm_ssh import make_rpc_command, parse_hex_int, run_ssh

# ============================================================
# JSON-RPC PARSING
# ============================================================


def parse_block_number_response(text: str) -> int | None:
    if "unavailable" in text.lower() or '"result"' not in text:
        return None
    try:
        return parse_hex_int(json.loads(text).get("result"))
    except (json.JSONDecodeError, TypeError):
        return None


def parse_validator_set_response(text: str) -> list[str] | None:
    if "unavailable" in text.lower() or '"result"' not in text:
        return None
    try:
        result = json.loads(text).get("result")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(result, list):
        return None
    return [str(addr).lower() for addr in result]


def query_rpc(alias: str, rpc_url: str, method: str, params: list, timeout: int) -> str:
    cmd = make_rpc_command(rpc_url, method, params)
    result = run_ssh(alias, cmd, timeout=timeout)
    if result["error"]:
        return f"ERROR: {result['error']}"
    if result["returncode"] != 0:
        return f"ERROR: {result['stderr'] or result['stdout'] or 'ssh failed'}"
    return result["stdout"]


# ============================================================
# CONSENSUS CHECKS
# ============================================================


def check_validator_block(alias: str) -> dict:
    """Query eth_blockNumber for a single validator via SSH."""
    vm_info = VMS.get(alias, {})
    rpc_url = vm_info.get("rpc_url")
    timeout = vm_info.get("timeout", 15)
    ip = vm_info.get("ip", "unknown")

    if not rpc_url:
        return {
            "alias": alias,
            "ip": ip,
            "status": "error",
            "error": "rpc_url not configured",
            "block_number": None,
            "drift_blocks": None,
            "synced": False,
        }

    print(f"  → Querying {alias} ({ip}) eth_blockNumber...", flush=True)

    conn = run_ssh(alias, "echo ok", timeout=timeout)
    if conn["stdout"] != "ok":
        return {
            "alias": alias,
            "ip": ip,
            "status": "unreachable",
            "error": conn["error"] or conn["stderr"] or "no response",
            "block_number": None,
            "drift_blocks": None,
            "synced": False,
        }

    raw = query_rpc(alias, rpc_url, "eth_blockNumber", [], timeout)
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
            "raw_response": raw[:200] if raw else "",
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


def fetch_active_validator_set(candidates: list[dict], block_tag: str | int = "latest") -> dict:
    """
    Query qbft_getValidatorsByBlockNumber from the first reachable validator
    that returns a valid validator list.
    """
    if block_tag == "latest":
        params = ["latest"]
    elif isinstance(block_tag, int):
        params = [hex(block_tag)]
    else:
        params = [str(block_tag)]

    for entry in candidates:
        if entry.get("status") != "ok":
            continue
        alias = entry["alias"]
        vm_info = VMS.get(alias, {})
        rpc_url = vm_info.get("rpc_url")
        timeout = vm_info.get("timeout", 15)
        if not rpc_url:
            continue

        raw = query_rpc(alias, rpc_url, "qbft_getValidatorsByBlockNumber", params, timeout)
        addresses = parse_validator_set_response(raw)
        if addresses is not None:
            return {
                "source_alias": alias,
                "block_tag": block_tag,
                "validator_addresses": addresses,
                "validator_count": len(addresses),
                "quorum_required": qbft_quorum_threshold(len(addresses)),
                "raw_response": raw[:300] if raw else "",
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
    validator_results: list[dict],
    max_drift_blocks: int,
    validator_set: dict,
) -> dict:
    """Compute drift, sync status, and quorum from per-validator block queries."""
    reachable = [r for r in validator_results if r.get("block_number") is not None]
    reference_block = max((r["block_number"] for r in reachable), default=None)

    out_of_sync: list[dict] = []
    synced_aliases: list[str] = []

    for entry in validator_results:
        block = entry.get("block_number")
        if block is None or reference_block is None:
            entry["drift_blocks"] = None
            entry["synced"] = False
            continue

        drift = reference_block - block
        entry["drift_blocks"] = drift
        entry["synced"] = drift <= max_drift_blocks
        if entry["synced"]:
            synced_aliases.append(entry["alias"])
        elif drift > max_drift_blocks:
            out_of_sync.append({
                "alias": entry["alias"],
                "block_number": block,
                "reference_block": reference_block,
                "drift_blocks": drift,
            })

    active_count = validator_set.get("validator_count", 0)
    quorum_required = validator_set.get("quorum_required", 0)
    synced_count = len(synced_aliases)
    has_quorum = synced_count >= quorum_required if quorum_required else False

    findings: list[str] = []
    if reference_block is None:
        findings.append("No validator returned a usable eth_blockNumber")
    if out_of_sync:
        names = ", ".join(item["alias"] for item in out_of_sync)
        findings.append(
            f"Validators drift > {max_drift_blocks} block(s) from head ({reference_block}): {names}"
        )
    unreachable = [r["alias"] for r in validator_results if r.get("status") == "unreachable"]
    if unreachable:
        findings.append(f"Unreachable validators: {', '.join(unreachable)}")
    rpc_errors = [r["alias"] for r in validator_results if r.get("status") == "rpc_error"]
    if rpc_errors:
        findings.append(f"RPC errors on validators: {', '.join(rpc_errors)}")
    if active_count and not has_quorum:
        findings.append(
            f"QBFT quorum at risk: {synced_count}/{active_count} synced, "
            f"need {quorum_required} (>{2}/{3} of active set)"
        )
    elif active_count and has_quorum:
        findings.append(
            f"QBFT quorum satisfied: {synced_count}/{active_count} synced "
            f"(required {quorum_required})"
        )
    if validator_set.get("error"):
        findings.append(validator_set["error"])

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
        "active_validator_set": validator_set,
        "synced_validators": synced_count,
        "has_quorum": has_quorum,
        "validators": validator_results,
        "out_of_sync": out_of_sync,
        "findings": findings,
    }


def print_summary(report: dict) -> None:
    print("\n" + "=" * 60)
    print("VM CONSENSUS SUMMARY")
    print("=" * 60)

    ref = report.get("reference_block")
    max_drift = report.get("max_drift_blocks")
    print(f"Reference block:  {ref if ref is not None else 'unknown'}")
    print(f"Max drift allowed: {max_drift} block(s)")
    print(f"Overall status:   {report.get('overall_status', 'unknown')}")

    vset = report.get("active_validator_set", {})
    print(
        f"Active validator set: {vset.get('validator_count', 0)} "
        f"(source: {vset.get('source_alias') or 'none'})"
    )
    print(
        f"Synced validators:    {report.get('synced_validators', 0)} / "
        f"{vset.get('validator_count', 0)} "
        f"(quorum required: {vset.get('quorum_required', 0)})"
    )
    print(f"Has QBFT quorum:      {'yes' if report.get('has_quorum') else 'no'}")

    print("\nValidator blocks:")
    for entry in sorted(report.get("validators", []), key=lambda x: x["alias"]):
        alias = entry["alias"]
        if entry.get("status") == "ok":
            drift = entry.get("drift_blocks")
            synced = "synced" if entry.get("synced") else f"DRIFT {drift}"
            print(f"  ✓ {alias:<14} block {entry['block_number']:<8} {synced}")
        else:
            print(f"  ✗ {alias:<14} {entry.get('status', 'error')} — {entry.get('error', 'unknown')}")

    if report.get("findings"):
        print("\nFindings:")
        for finding in report["findings"]:
            print(f"  - {finding}")

    print("=" * 60)


# ============================================================
# MAIN
# ============================================================


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Besu QBFT block sync and quorum comparator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 vm_consensus.py
  python3 vm_consensus.py --max-drift 5
  python3 vm_consensus.py validator1 validator2
  python3 vm_consensus.py --output consensus.json
  python3 vm_consensus.py --list
        """,
    )
    parser.add_argument(
        "validators",
        nargs="*",
        help="Validator aliases to check (default: all QBFT validators in inventory)",
    )
    parser.add_argument(
        "--max-drift",
        type=int,
        default=CONSENSUS_DEFAULT_MAX_DRIFT_BLOCKS,
        help=f"Flag validators more than N blocks behind head (default: {CONSENSUS_DEFAULT_MAX_DRIFT_BLOCKS})",
    )
    parser.add_argument(
        "--output", "-o",
        help="Save JSON report to file (timestamp appended before extension)",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=7,
        help="Parallel SSH workers (default: 7)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List QBFT validator aliases from inventory and exit",
    )

    args = parser.parse_args()

    if args.list:
        aliases = validator_aliases()
        print(f"\n{'Alias':<16} {'IP':<18} {'Timeout':<10} RPC URL")
        print("-" * 70)
        for alias in aliases:
            info = VMS[alias]
            print(
                f"  {alias:<16} {info['ip']:<18} {info['timeout']}s{'':<6} "
                f"{info.get('rpc_url', 'none')}"
            )
        print(f"\nTotal validators: {len(aliases)}")
        print(f"Default max drift: {CONSENSUS_DEFAULT_MAX_DRIFT_BLOCKS} blocks")
        return

    if args.validators:
        targets = []
        for alias in args.validators:
            if alias not in VMS:
                print(f"WARNING: Unknown VM '{alias}' — skipping.")
                continue
            if not is_besu_validator(alias):
                print(f"WARNING: '{alias}' is not a Besu validator — skipping.")
                continue
            targets.append(alias)
        if not targets:
            print("No valid validator aliases specified. Exiting.")
            sys.exit(1)
    else:
        targets = validator_aliases()

    print(f"\nvm_consensus.py — checking {len(targets)} validator(s) with {args.workers} parallel workers")
    print(f"Max drift: {args.max_drift} block(s)")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(check_validator_block, alias): alias for alias in targets}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r["alias"])

    reference_block = max(
        (r["block_number"] for r in results if r.get("block_number") is not None),
        default="latest",
    )
    validator_set = fetch_active_validator_set(results, block_tag=reference_block)
    report = build_consensus_report(results, args.max_drift, validator_set)

    print_summary(report)

    json_output = json.dumps(report, indent=2)
    if args.output:
        base, _, ext = args.output.rpartition(".")
        ext = f".{ext}" if ext else ".json"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"{base}_{timestamp}{ext}" if base else f"{args.output}_{timestamp}"
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(json_output)
        print(f"\nJSON report saved to: {output_path}")
    else:
        print(f"\nJSON Output:\n{json_output}")


if __name__ == "__main__":
    main()
