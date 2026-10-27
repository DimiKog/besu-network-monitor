#!/usr/bin/env python3
"""Atomic SCP + remote-rename publisher for sanitized monitor snapshots.

Shared by the consensus and infrastructure pipelines. Uploads a local JSON
snapshot to a temporary name in the remote destination directory, then
renames it to the final filename only after a successful upload.

Stdlib only. No shell=True. Does not collect telemetry and never publishes
raw Prometheus or validator data.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


DEFAULT_PUBLISHER_IDENTITY = Path.home() / ".ssh" / "besu_monitor_publisher"
DEFAULT_REMOTE_USER = "ubuntu"
DEFAULT_REMOTE_DIR = "/var/lib/besu-monitor/current"
DEFAULT_SCP_TIMEOUT = 60
DEFAULT_SSH_TIMEOUT = 30
DEFAULT_CONNECT_TIMEOUT = 10

# Deployment-specific publisher destination is never hardcoded. Supply via
# --remote-host or BESU_MONITOR_REMOTE_HOST.
ENV_REMOTE_HOST = "BESU_MONITOR_REMOTE_HOST"


class PublishError(Exception):
    """Publish stage failed; remote final snapshot must remain untouched."""


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        ...


@dataclass(frozen=True)
class PublishConfig:
    """Generic remote publish settings (no collector-specific fields)."""

    publisher_identity: Path = DEFAULT_PUBLISHER_IDENTITY
    remote_user: str = DEFAULT_REMOTE_USER
    remote_host: str = ""
    remote_dir: str = DEFAULT_REMOTE_DIR
    final_name: str = "snapshot.json"
    scp_timeout: float = DEFAULT_SCP_TIMEOUT
    ssh_timeout: float = DEFAULT_SSH_TIMEOUT
    connect_timeout: int = DEFAULT_CONNECT_TIMEOUT


def expand_path(path: Path) -> Path:
    return path.expanduser()


def resolve_remote_host(cli_value: str | None = None) -> str:
    """Resolve publisher host. CLI wins over env; no production default.

    Raises PublishError when missing or empty after CLI precedence is applied.
    """
    if cli_value is not None:
        host = str(cli_value).strip()
        if not host:
            raise PublishError(
                "remote host is empty: set --remote-host or "
                f"{ENV_REMOTE_HOST}"
            )
        return host
    env = os.environ.get(ENV_REMOTE_HOST, "")
    host = str(env).strip() if env is not None else ""
    if not host:
        raise PublishError(
            "remote host required: set --remote-host or "
            f"{ENV_REMOTE_HOST}"
        )
    return host


def require_remote_host(host: str) -> str:
    """Fail fast if a PublishConfig host is missing/blank before SCP/SSH."""
    cleaned = str(host).strip() if host is not None else ""
    if not cleaned:
        raise PublishError(
            "remote host required: set --remote-host or "
            f"{ENV_REMOTE_HOST}"
        )
    return cleaned


def remote_target(config: PublishConfig) -> str:
    host = require_remote_host(config.remote_host)
    return f"{config.remote_user}@{host}"


def remote_final_path(config: PublishConfig) -> str:
    return f"{config.remote_dir.rstrip('/')}/{config.final_name}"


def remote_temp_name(config: PublishConfig) -> str:
    """Unique temp filename in the same remote directory as the final file."""
    token = f"{os.getpid()}.{int(time.time())}.{uuid.uuid4().hex[:8]}"
    return f".{config.final_name}.{token}.tmp"


def remote_temp_path(config: PublishConfig, temp_name: str) -> str:
    return f"{config.remote_dir.rstrip('/')}/{temp_name}"


def ssh_base_argv(identity: Path, connect_timeout: int, target: str) -> list[str]:
    return [
        "ssh",
        "-i",
        str(identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        target,
    ]


def scp_argv(
    identity: Path,
    connect_timeout: int,
    local_file: Path,
    remote_spec: str,
) -> list[str]:
    return [
        "scp",
        "-i",
        str(identity),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        f"ConnectTimeout={connect_timeout}",
        str(local_file),
        remote_spec,
    ]


def default_runner(
    argv: Sequence[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a command as an argv array. Never uses shell=True."""
    try:
        return subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PublishError(
            f"command timed out after {timeout}s: {argv[0]}"
        ) from exc
    except OSError as exc:
        raise PublishError(f"failed to start {argv[0]}: {exc}") from exc


def format_command_failure(
    stage: str,
    completed: subprocess.CompletedProcess[str],
) -> str:
    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    detail = stderr or stdout or f"exit {completed.returncode}"
    # Avoid dumping huge collector logs; keep operationally useful.
    if len(detail) > 800:
        detail = detail[:800] + "…"
    return f"{stage} failed (exit {completed.returncode}): {detail}"


def ensure_identity_exists(path: Path, label: str) -> Path:
    resolved = expand_path(path)
    if not resolved.is_file():
        raise PublishError(f"{label} SSH identity not found: {resolved}")
    return resolved


def cleanup_remote_temp(
    config: PublishConfig,
    publisher_identity: Path,
    temp_path: str,
    runner: CommandRunner,
) -> None:
    """Best-effort removal of a remote temp upload."""
    argv = ssh_base_argv(
        publisher_identity,
        config.connect_timeout,
        remote_target(config),
    ) + ["rm", "-f", "--", temp_path]
    try:
        runner(argv, timeout=config.ssh_timeout)
    except PublishError as exc:
        print(f"pipeline: warning: temp cleanup failed: {exc}", file=sys.stderr)


def publish_snapshot(
    config: PublishConfig,
    local_snapshot: Path,
    runner: CommandRunner = default_runner,
) -> None:
    """Atomically publish local snapshot via SCP temp + remote rename."""
    require_remote_host(config.remote_host)
    publisher_identity = ensure_identity_exists(
        config.publisher_identity, "publisher"
    )

    temp_name = remote_temp_name(config)
    temp_path = remote_temp_path(config, temp_name)
    final_path = remote_final_path(config)
    target = remote_target(config)
    remote_spec = f"{target}:{temp_path}"

    scp_cmd = scp_argv(
        publisher_identity,
        config.connect_timeout,
        local_snapshot,
        remote_spec,
    )
    print("pipeline: uploading snapshot to remote temporary file", flush=True)
    scp_result = runner(scp_cmd, timeout=config.scp_timeout)
    if scp_result.returncode != 0:
        cleanup_remote_temp(config, publisher_identity, temp_path, runner)
        raise PublishError(format_command_failure("upload", scp_result))

    # Rename only after successful upload; same directory => same filesystem.
    rename_cmd = ssh_base_argv(
        publisher_identity,
        config.connect_timeout,
        target,
    ) + ["mv", "-f", "--", temp_path, final_path]

    print("pipeline: atomically renaming remote temporary file", flush=True)
    rename_result = runner(rename_cmd, timeout=config.ssh_timeout)
    if rename_result.returncode != 0:
        cleanup_remote_temp(config, publisher_identity, temp_path, runner)
        raise PublishError(format_command_failure("remote rename", rename_result))

    print(f"pipeline: published {final_path}", flush=True)
