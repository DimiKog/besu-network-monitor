#!/usr/bin/env python3
"""Orchestrate consensus collection then atomic publish to my-backend.

Sequence:
  1. Run scripts/collect_consensus.py (collector SSH identity only).
  2. Require exit code 0 and a local sanitized snapshot file.
  3. SCP the snapshot to a temporary name in the remote destination directory
     using the publisher SSH identity (never the collector identity).
  4. SSH rename the temp file to consensus_snapshot.json only after a
     successful upload.

Publishing uses scripts/snapshot_publisher.py (shared with infrastructure).
Stdlib only. No shell=True. Scheduling is intentionally not implemented.

Deployment-specific addresses are not hardcoded: supply
BESU_MONITOR_VALIDATORS_FILE / --validators-file and
BESU_MONITOR_REMOTE_HOST / --remote-host (CLI wins over env).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import collect_consensus as collector
import snapshot_publisher as publisher


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_COLLECTOR_SCRIPT = BASE_DIR / "scripts" / "collect_consensus.py"
DEFAULT_LOCAL_SNAPSHOT = BASE_DIR / "output" / "consensus_snapshot.json"

DEFAULT_COLLECTOR_IDENTITY = Path.home() / ".ssh" / "besu_monitor_collector"
DEFAULT_PUBLISHER_IDENTITY = publisher.DEFAULT_PUBLISHER_IDENTITY

DEFAULT_REMOTE_USER = publisher.DEFAULT_REMOTE_USER
DEFAULT_REMOTE_DIR = publisher.DEFAULT_REMOTE_DIR
DEFAULT_FINAL_NAME = "consensus_snapshot.json"

DEFAULT_MAX_DRIFT = 2
DEFAULT_WORKERS = 7
DEFAULT_COLLECT_TIMEOUT = 180
DEFAULT_SCP_TIMEOUT = publisher.DEFAULT_SCP_TIMEOUT
DEFAULT_SSH_TIMEOUT = publisher.DEFAULT_SSH_TIMEOUT
DEFAULT_CONNECT_TIMEOUT = publisher.DEFAULT_CONNECT_TIMEOUT

# Re-export shared publish primitives so existing tests keep importing from here.
CommandRunner = publisher.CommandRunner
expand_path = publisher.expand_path
ssh_base_argv = publisher.ssh_base_argv
scp_argv = publisher.scp_argv
default_runner = publisher.default_runner
ensure_identity_exists = publisher.ensure_identity_exists
resolve_remote_host = publisher.resolve_remote_host


class PipelineError(Exception):
    """Pipeline stage failed; remote final snapshot must remain untouched."""


@dataclass(frozen=True)
class PipelineConfig:
    collector_script: Path = DEFAULT_COLLECTOR_SCRIPT
    local_snapshot: Path = DEFAULT_LOCAL_SNAPSHOT
    collector_identity: Path = DEFAULT_COLLECTOR_IDENTITY
    publisher_identity: Path = DEFAULT_PUBLISHER_IDENTITY
    remote_user: str = DEFAULT_REMOTE_USER
    remote_host: str = ""
    remote_dir: str = DEFAULT_REMOTE_DIR
    final_name: str = DEFAULT_FINAL_NAME
    validators_file: Path | None = None
    max_drift: int = DEFAULT_MAX_DRIFT
    workers: int = DEFAULT_WORKERS
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


def remote_target(config: PipelineConfig) -> str:
    return publisher.remote_target(_publish_config(config))


def remote_final_path(config: PipelineConfig) -> str:
    return publisher.remote_final_path(_publish_config(config))


def remote_temp_name(config: PipelineConfig) -> str:
    """Unique temp filename in the same remote directory as the final file."""
    return publisher.remote_temp_name(_publish_config(config))


def remote_temp_path(config: PipelineConfig, temp_name: str) -> str:
    return publisher.remote_temp_path(_publish_config(config), temp_name)


def _format_command_failure(
    stage: str,
    completed,
) -> str:
    return publisher.format_command_failure(stage, completed)


def ensure_identities_separate(config: PipelineConfig) -> None:
    collector_id = expand_path(config.collector_identity).resolve()
    publisher_id = expand_path(config.publisher_identity).resolve()
    if collector_id == publisher_id:
        raise PipelineError(
            "collector and publisher SSH identities must be different files"
        )


def run_collection(
    config: PipelineConfig,
    runner: CommandRunner = default_runner,
) -> Path:
    """Run collect_consensus.py; require success and a local snapshot file."""
    script = expand_path(config.collector_script).resolve()
    if not script.is_file():
        raise PipelineError(f"collector script not found: {script}")

    try:
        collector_identity = ensure_identity_exists(
            config.collector_identity, "collector"
        )
    except publisher.PublishError as exc:
        raise PipelineError(str(exc)) from exc

    if config.validators_file is None:
        raise PipelineError(
            "validators file required: set --validators-file or "
            f"{collector.ENV_VALIDATORS_FILE}"
        )
    validators_file = expand_path(config.validators_file)

    local_snapshot = expand_path(config.local_snapshot)

    argv = [
        sys.executable,
        str(script),
        "--output",
        str(local_snapshot),
        "--validators-file",
        str(validators_file),
        "--max-drift",
        str(config.max_drift),
        "--workers",
        str(config.workers),
        "--ssh-identity",
        str(collector_identity),
    ]

    print(
        f"pipeline: collecting consensus "
        f"(max-drift={config.max_drift}, workers={config.workers})",
        flush=True,
    )
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

    print(f"pipeline: collection ok -> {snapshot}", flush=True)
    return snapshot


def cleanup_remote_temp(
    config: PipelineConfig,
    publisher_identity: Path,
    temp_path: str,
    runner: CommandRunner,
) -> None:
    """Best-effort removal of a remote temp upload."""
    publisher.cleanup_remote_temp(
        _publish_config(config),
        publisher_identity,
        temp_path,
        runner,
    )


def publish_snapshot(
    config: PipelineConfig,
    local_snapshot: Path,
    runner: CommandRunner = default_runner,
) -> None:
    """Atomically publish local snapshot via SCP temp + remote rename."""
    ensure_identities_separate(config)
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
    ensure_identities_separate(config)
    snapshot = run_collection(config, runner=runner)
    publish_snapshot(config, snapshot, runner=runner)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect sanitized consensus snapshot then atomically publish "
            "it to my-backend. Scheduling is not included."
        )
    )
    parser.add_argument(
        "--collector-script",
        type=Path,
        default=DEFAULT_COLLECTOR_SCRIPT,
        help=f"Path to collect_consensus.py (default: {DEFAULT_COLLECTOR_SCRIPT})",
    )
    parser.add_argument(
        "--local-snapshot",
        type=Path,
        default=DEFAULT_LOCAL_SNAPSHOT,
        help=f"Local snapshot path (default: {DEFAULT_LOCAL_SNAPSHOT})",
    )
    parser.add_argument(
        "--validators-file",
        type=Path,
        default=None,
        help=(
            "JSON validator inventory path "
            f"(or {collector.ENV_VALIDATORS_FILE}; required)"
        ),
    )
    parser.add_argument(
        "--collector-identity",
        type=Path,
        default=DEFAULT_COLLECTOR_IDENTITY,
        help=f"Collector SSH key (default: {DEFAULT_COLLECTOR_IDENTITY})",
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
        default=None,
        help=(
            "my-backend host "
            f"(or {publisher.ENV_REMOTE_HOST}; required; no baked-in default)"
        ),
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
        "--max-drift",
        type=int,
        default=DEFAULT_MAX_DRIFT,
        help=f"Passed to collector (default: {DEFAULT_MAX_DRIFT})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Passed to collector (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--collect-timeout",
        type=float,
        default=DEFAULT_COLLECT_TIMEOUT,
        help=f"Collector subprocess timeout seconds (default: {DEFAULT_COLLECT_TIMEOUT})",
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
        help=f"Remote rename/cleanup SSH timeout seconds (default: {DEFAULT_SSH_TIMEOUT})",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=DEFAULT_CONNECT_TIMEOUT,
        help=f"SSH/SCP ConnectTimeout (default: {DEFAULT_CONNECT_TIMEOUT})",
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> PipelineConfig:
    try:
        validators_file = collector.resolve_validators_file(args.validators_file)
        remote_host = resolve_remote_host(args.remote_host)
    except (collector.CollectorError, publisher.PublishError) as exc:
        raise PipelineError(str(exc)) from exc
    return PipelineConfig(
        collector_script=args.collector_script,
        local_snapshot=args.local_snapshot,
        collector_identity=args.collector_identity,
        publisher_identity=args.publisher_identity,
        remote_user=args.remote_user,
        remote_host=remote_host,
        remote_dir=args.remote_dir,
        final_name=args.final_name,
        validators_file=validators_file,
        max_drift=args.max_drift,
        workers=args.workers,
        collect_timeout=args.collect_timeout,
        scp_timeout=args.scp_timeout,
        ssh_timeout=args.ssh_timeout,
        connect_timeout=args.connect_timeout,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = config_from_args(args)
        run_pipeline(config, runner=default_runner)
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
