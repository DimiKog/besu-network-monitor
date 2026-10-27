"""Read atomically published sanitized snapshots from disk.

Files are read on every request so atomic publisher replacements are visible
immediately without caching or process restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


class SnapshotLoadError(Exception):
    """Snapshot missing, unreadable, or not valid JSON."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def load_json_snapshot(path: Path, *, label: str = "snapshot") -> Any:
    """Load and parse JSON from ``path``.

    Raises:
        SnapshotLoadError: missing file, I/O failure, or malformed JSON.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = handle.read()
    except FileNotFoundError as exc:
        raise SnapshotLoadError("snapshot file missing") from exc
    except OSError as exc:
        logger.warning("failed to read %s snapshot: %s", label, exc)
        raise SnapshotLoadError("snapshot file unreadable") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotLoadError("snapshot JSON malformed") from exc


def try_load_json_snapshot(
    path: Path,
    *,
    label: str = "snapshot",
) -> Tuple[Optional[Any], Optional[str]]:
    """Load snapshot or return ``(None, reason)`` without raising."""
    try:
        return load_json_snapshot(path, label=label), None
    except SnapshotLoadError as exc:
        logger.info("%s snapshot unavailable (%s): %s", label, path, exc.reason)
        return None, exc.reason


def load_consensus_snapshot(path: Path) -> Any:
    """Load and parse the consensus snapshot JSON from ``path``."""
    return load_json_snapshot(path, label="consensus")


def try_load_consensus_snapshot(path: Path) -> Tuple[Optional[Any], Optional[str]]:
    """Load consensus snapshot or return ``(None, reason)`` without raising."""
    return try_load_json_snapshot(path, label="consensus")


def load_infrastructure_snapshot(path: Path) -> Any:
    """Load and parse the infrastructure snapshot JSON from ``path``."""
    return load_json_snapshot(path, label="infrastructure")


def try_load_infrastructure_snapshot(
    path: Path,
) -> Tuple[Optional[Any], Optional[str]]:
    """Load infrastructure snapshot or return ``(None, reason)`` without raising."""
    return try_load_json_snapshot(path, label="infrastructure")
