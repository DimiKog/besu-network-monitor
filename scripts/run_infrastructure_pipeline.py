#!/usr/bin/env python3
"""Orchestrate infrastructure collection then atomic publish to my-backend.

Sequence:
  1. Run scripts/collect_infrastructure.py (local Prometheus HTTP only).
  2. Require exit code 0 and a local sanitized snapshot file.
  3. SCP the snapshot to a temporary name in the remote destination directory
     using the publisher SSH identity (~/.ssh/besu_monitor_publisher).
  4. SSH rename the temp file to infrastructure_snapshot.json only after a
     successful upload.

Publishing reuses scripts/snapshot_publisher.py (same mechanism as consensus).
Stdlib only. No shell=True. Scheduling is intentionally not implemented.
Never publishes raw Prometheus data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import snapshot_publisher as publisher


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_COLLECTOR_SCRIPT = BASE_DIR / "scripts" / "collect_infrastructure.py"
DEFAULT_LOCAL_SNAPSHOT = BASE_DIR / "output" / "infrastructure_snapshot.json"

DEFAULT_PUBLISHER_IDENTITY = publisher.DEFAULT_PUBLISHER_IDENTITY
DEFAULT_REMOTE_USER = publisher.DEFAULT_REMOTE_USER
DEFAULT_REMOTE_HOST = publisher.DEFAULT_REMOTE_HOST
DEFAULT_REMOTE_DIR = publisher.DEFAULT_REMOTE_DIR
DEFAULT_FINAL_NAME = "infrastructure_snapshot.json"

DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
DEFAULT_COLLECT_TIMEOUT = 60
DEFAULT_QUERY_TIMEOUT = 10.0
DEFAULT_SCP_TIMEOUT = publisher.DEFAULT_SCP_TIMEOUT
DEFAULT_SSH_TIMEOUT = publisher.DEFAULT_SSH_TIMEOUT
DEFAULT_CONNECT_TIMEOUT = publisher.DEFAULT_CONNECT_TIMEOUT

CommandRunner = publisher.CommandRunner
expand_path = publisher.expand_path
default_runner = publisher.default_runner


class PipelineError(Exception):
    """Pipeline stage failed; remote final snapshot must remain untouched."""


@dataclass(frozen=True)
class PipelineConfig:
    collector_script: Path = DEFAULT_COLLECTOR_SCRIPT
    local_snapshot: Path = DEFAULT_LOCAL_SNAPSHOT
    publisher_identity: Path = DEFAULT_PUBLISHER_IDENTITY
    remote_user: str = DEFAULT_REMOTE_USER
    remote_host: str = DEFAULT_REMOTE_HOST
    remote_dir: str = DEFAULT_REMOTE_DIR
    final_name: str = DEFAULT_FINAL_NAME
    prometheus_url: str = DEFAULT_PROMETHEUS_URL
    query_timeout: float = DEFAULT_QUERY_TIMEOUT
    collect_timeout: float = DEFAULT_COLLECT_TIMEOUT
    scp_timeout: float = DEFAULT_SCP_TIMEOUT
    ssh_timeout: float = DEFAULT_SSH_TIMEOUT
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT


def _publish_config(config: PipelineConfig) -> publisher.PublishConfig:
    return publisher.PublishConfig(
        publisher_identity=config.publisher_identity,
        remote_user=config.remote_user,
        remote_host=config.remote_host,
        remote_dir=config.remote_dir,
        final_name=config.final_name,
        scp_timeout=config.scp_timeout,
        ssh_timeout=config.ssh_timeout,
        connect_timeout=config.connect_timeout,
    )


def remote_final_path(config: PipelineConfig) -> str:
    return publisher.remote_final_path(_publish_config(config))


def _format_command_failure(stage: str, completed) -> str:
    return publisher.format_command_failure(stage, completed)


def _validate_sanitized_snapshot(path: Path) -> None:
    """Require a non-empty JSON object with the public infrastructure schema."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineError(f"unable to read snapshot: {exc}") from exc
    if not text.strip():
        raise PipelineError(f"collection produced empty snapshot: {path}")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipelineError("collection produced invalid JSON snapshot") from exc
    if not isinstance(payload, dict):
        raise PipelineError("collection produced non-object snapshot JSON")

    required_top = (
        "schema_version",
        "generated_at",
        "status",
        "systems",
        "validators",
        "rpc",
        "services",
    )
    missing = [key for key in required_top if key not in payload]
    if missing:
        raise PipelineError(
            "collection produced incomplete snapshot "
            f"(missing: {', '.join(missing)})"
        )

    status = payload.get("status")
    if not isinstance(status, str) or status not in {
        "healthy",
        "degraded",
        "critical",
        "unknown",
    }:
        raise PipelineError("collection produced invalid snapshot status")

    systems = payload.get("systems")
    if not isinstance(systems, dict) or systems.get("monitored") != 16:
        raise PipelineError(
            "collection produced incomplete systems inventory"
        )


def run_collection(
    config: PipelineConfig,
    runner: CommandRunner = default_runner,
) -> Path:
    """Run collect_infrastructure.py; require success and a valid snapshot."""
    script = expand_path(config.collector_script).resolve()
    if not script.is_file():
        raise PipelineError(f"collector script not found: {script}")

    local_snapshot = expand_path(config.local_snapshot)
    argv = [
        sys.executable,
        str(script),
        "--output",
        str(local_snapshot),
        "--prometheus-url",
        str(config.prometheus_url),
        "--timeout",
        str(config.query_timeout),
    ]

    print("pipeline: collecting infrastructure", flush=True)
    try:
        completed = runner(argv, timeout=config.collect_timeout)
    except publisher.PublishError as exc:
        raise PipelineError(str(exc)) from exc
    if completed.returncode != 0:
        raise PipelineError(_format_command_failure("collection", completed))

    snapshot = local_snapshot.expanduser()
    if not snapshot.is_file():
        raise PipelineError(
            f"collection reported success but snapshot missing: {snapshot}"
        )
    if snapshot.stat().st_size <= 0:
        raise PipelineError(f"collection produced empty snapshot: {snapshot}")

    _validate_sanitized_snapshot(snapshot)
    print(f"pipeline: collection ok -> {snapshot}", flush=True)
    return snapshot


def publish_snapshot(
    config: PipelineConfig,
    local_snapshot: Path,
    runner: CommandRunner = default_runner,
) -> None:
    """Atomically publish local snapshot via SCP temp + remote rename."""
    try:
        publisher.publish_snapshot(
            _publish_config(config),
            local_snapshot,
            runner=runner,
        )
    except publisher.PublishError as exc:
        raise PipelineError(str(exc)) from exc


def run_pipeline(
    config: PipelineConfig,
    runner: CommandRunner = default_runner,
) -> None:
    snapshot = run_collection(config, runner=runner)
    publish_snapshot(config, snapshot, runner=runner)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect sanitized infrastructure snapshot from local Prometheus "
            "then atomically publish it to my-backend. Scheduling is not "
            "included."
        )
    )
    parser.add_argument(
        "--collector-script",
        type=Path,
        default=DEFAULT_COLLECTOR_SCRIPT,
        help=(
            "Path to collect_infrastructure.py "
            f"(default: {DEFAULT_COLLECTOR_SCRIPT})"
        ),
    )
    parser.add_argument(
        "--local-snapshot",
        type=Path,
        default=Path(
            os.environ.get(
                "BESU_MONITOR_INFRA_SNAPSHOT",
                str(DEFAULT_LOCAL_SNAPSHOT),
            )
        ),
        help=(
            f"Local snapshot path (default: {DEFAULT_LOCAL_SNAPSHOT} or "
            "BESU_MONITOR_INFRA_SNAPSHOT)"
        ),
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.environ.get(
            "BESU_MONITOR_PROMETHEUS_URL",
            DEFAULT_PROMETHEUS_URL,
        ),
        help=(
            f"Prometheus base URL (default: {DEFAULT_PROMETHEUS_URL} or "
            "BESU_MONITOR_PROMETHEUS_URL)"
        ),
    )
    parser.add_argument(
        "--query-timeout",
        type=float,
        default=float(
            os.environ.get(
                "BESU_MONITOR_PROMETHEUS_TIMEOUT",
                str(DEFAULT_QUERY_TIMEOUT),
            )
        ),
        help=(
            "Prometheus HTTP timeout passed to collector "
            f"(default: {DEFAULT_QUERY_TIMEOUT} or "
            "BESU_MONITOR_PROMETHEUS_TIMEOUT)"
        ),
    )
    parser.add_argument(
        "--publisher-identity",
        type=Path,
        default=DEFAULT_PUBLISHER_IDENTITY,
        help=f"Publisher SSH key (default: {DEFAULT_PUBLISHER_IDENTITY})",
    )
    parser.add_argument(
        "--remote-user",
        default=DEFAULT_REMOTE_USER,
        help=f"my-backend SSH user (default: {DEFAULT_REMOTE_USER})",
    )
    parser.add_argument(
        "--remote-host",
        default=DEFAULT_REMOTE_HOST,
        help=f"my-backend host (default: {DEFAULT_REMOTE_HOST})",
    )
    parser.add_argument(
        "--remote-dir",
        default=DEFAULT_REMOTE_DIR,
        help=f"Remote snapshot directory (default: {DEFAULT_REMOTE_DIR})",
    )
    parser.add_argument(
        "--final-name",
        default=DEFAULT_FINAL_NAME,
        help=f"Final remote filename (default: {DEFAULT_FINAL_NAME})",
    )
    parser.add_argument(
        "--collect-timeout",
        type=float,
        default=DEFAULT_COLLECT_TIMEOUT,
        help=(
            "Collector subprocess timeout seconds "
            f"(default: {DEFAULT_COLLECT_TIMEOUT})"
        ),
    )
    parser.add_argument(
        "--scp-timeout",
        type=float,
        default=DEFAULT_SCP_TIMEOUT,
        help=f"SCP timeout seconds (default: {DEFAULT_SCP_TIMEOUT})",
    )
    parser.add_argument(
        "--ssh-timeout",
        type=float,
        default=DEFAULT_SSH_TIMEOUT,
        help=(
            "Remote rename/cleanup SSH timeout seconds "
            f"(default: {DEFAULT_SSH_TIMEOUT})"
        ),
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=DEFAULT_CONNECT_TIMEOUT,
        help=f"SSH/SCP ConnectTimeout (default: {DEFAULT_CONNECT_TIMEOUT})",
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig(
        collector_script=args.collector_script,
        local_snapshot=args.local_snapshot,
        publisher_identity=args.publisher_identity,
        remote_user=args.remote_user,
        remote_host=args.remote_host,
        remote_dir=args.remote_dir,
        final_name=args.final_name,
        prometheus_url=args.prometheus_url,
        query_timeout=args.query_timeout,
        collect_timeout=args.collect_timeout,
        scp_timeout=args.scp_timeout,
        ssh_timeout=args.ssh_timeout,
        connect_timeout=args.connect_timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_args(args)
    try:
        run_pipeline(config, runner=default_runner)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
