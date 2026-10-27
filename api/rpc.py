"""Public Besu/Ethereum JSON-RPC client and defensive parsers.

Uses only standard Ethereum JSON-RPC methods (no qbft_*).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, List, Optional, Protocol, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")

RpcCall = Tuple[str, List[Any]]


class RpcError(Exception):
    """Base class for RPC transport/parse failures (never expose to clients)."""


class RpcTransportError(RpcError):
    """HTTP or connectivity failure talking to the RPC endpoint."""


class RpcParseError(RpcError):
    """Malformed or incomplete JSON-RPC payload / result."""


class RpcClient(Protocol):
    """Minimal JSON-RPC client protocol (batch-oriented)."""

    def batch_call(self, calls: Sequence[RpcCall]) -> List[Any]:
        """Execute calls in order; return decoded ``result`` values.

        Raises:
            RpcTransportError: unreachable / HTTP failure / invalid envelope.
            RpcParseError: JSON-RPC error object or missing result.
        """


@dataclass(frozen=True)
class HttpxRpcClient:
    """Production JSON-RPC client using httpx."""

    url: str
    timeout_seconds: float

    def batch_call(self, calls: Sequence[RpcCall]) -> List[Any]:
        if not calls:
            return []

        payload: List[dict[str, Any]] = [
            {
                "jsonrpc": "2.0",
                "id": index + 1,
                "method": method,
                "params": list(params),
            }
            for index, (method, params) in enumerate(calls)
        ]

        try:
            # Always send a JSON array so single-call and multi-call batches
            # share one code path. Servers may also return a single object for
            # one-element batches; _extract_batch_results accepts both shapes.
            response = httpx.post(
                self.url,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            logger.info("RPC transport failure: %s", type(exc).__name__)
            raise RpcTransportError("RPC endpoint unreachable") from exc

        if response.status_code != 200:
            logger.info("RPC HTTP status %s", response.status_code)
            raise RpcTransportError("RPC endpoint returned non-200 status")

        try:
            body = response.json()
        except ValueError as exc:
            raise RpcParseError("RPC response is not JSON") from exc

        return _extract_batch_results(body, expected=len(calls))


def _extract_batch_results(body: Any, *, expected: int) -> List[Any]:
    """Normalize single-object or array JSON-RPC responses into ordered results."""
    if expected == 1 and isinstance(body, dict):
        items = [body]
    elif isinstance(body, list):
        items = body
    else:
        raise RpcParseError("RPC response must be a JSON object or array")

    if len(items) != expected:
        raise RpcParseError(
            f"RPC batch size mismatch: expected {expected}, got {len(items)}"
        )

    # Map by id when present; otherwise preserve array order.
    by_id: dict[Any, Any] = {}
    ordered_without_id: List[Any] = []
    for item in items:
        if not isinstance(item, dict):
            raise RpcParseError("RPC batch item must be an object")
        if "id" in item:
            by_id[item["id"]] = item
        else:
            ordered_without_id.append(item)

    results: List[Any] = []
    for index in range(expected):
        rpc_id = index + 1
        if rpc_id in by_id:
            item = by_id[rpc_id]
        elif ordered_without_id:
            item = ordered_without_id.pop(0)
        elif index < len(items) and "id" not in items[index]:
            item = items[index]
        else:
            # Fall back to positional if ids are weird but length matched.
            item = items[index]

        if "error" in item and item["error"] is not None:
            raise RpcParseError("RPC method returned an error")
        if "result" not in item:
            raise RpcParseError("RPC response missing result")
        results.append(item["result"])

    return results


def parse_hex_quantity(value: Any, *, field: str) -> int:
    """Parse an Ethereum JSON-RPC quantity (hex string) to a non-negative int."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise RpcParseError(f"{field} must be a hex quantity string")
    text = value.strip()
    if not text.startswith("0x") and not text.startswith("0X"):
        raise RpcParseError(f"{field} must be a 0x-prefixed hex quantity")
    try:
        number = int(text, 16)
    except ValueError as exc:
        raise RpcParseError(f"{field} is not valid hex") from exc
    if number < 0:
        raise RpcParseError(f"{field} must be non-negative")
    return number


def parse_address(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.match(value.strip()):
        raise RpcParseError(f"{field} must be a 20-byte 0x-prefixed address")
    return value.strip().lower()


def parse_block_number_tag(block_number: int) -> str:
    if isinstance(block_number, bool) or not isinstance(block_number, int):
        raise RpcParseError("block number must be an integer")
    if block_number < 0:
        raise RpcParseError("block number must be non-negative")
    return hex(block_number)


def parse_syncing(value: Any) -> bool:
    """Interpret eth_syncing: false => not syncing; object => syncing."""
    if value is False:
        return False
    if value is True:
        # Non-standard, but treat as syncing rather than inventing "unknown".
        return True
    if isinstance(value, dict):
        return True
    raise RpcParseError("eth_syncing result must be false or an object")


def parse_client_version(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RpcParseError("web3_clientVersion must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ParsedBlock:
    number: int
    timestamp: int  # unix seconds
    proposer: str
    transaction_count: int
    gas_used: int
    gas_limit: int


def parse_block(result: Any) -> ParsedBlock:
    """Parse eth_getBlockByNumber result (transactions as hashes or full txs)."""
    if result is None:
        raise RpcParseError("block result is null")
    if not isinstance(result, dict):
        raise RpcParseError("block result must be an object")

    number = parse_hex_quantity(result.get("number"), field="block.number")
    timestamp = parse_hex_quantity(result.get("timestamp"), field="block.timestamp")
    gas_used = parse_hex_quantity(result.get("gasUsed"), field="block.gasUsed")
    gas_limit = parse_hex_quantity(result.get("gasLimit"), field="block.gasLimit")

    # Besu/QBFT exposes the block proposer in the standard miner field.
    proposer = parse_address(result.get("miner"), field="block.miner")

    transactions = result.get("transactions")
    if not isinstance(transactions, list):
        raise RpcParseError("block.transactions must be an array")
    for index, item in enumerate(transactions):
        if isinstance(item, str):
            continue
        if isinstance(item, dict):
            continue
        raise RpcParseError(f"block.transactions[{index}] has invalid type")

    return ParsedBlock(
        number=number,
        timestamp=timestamp,
        proposer=proposer,
        transaction_count=len(transactions),
        gas_used=gas_used,
        gas_limit=gas_limit,
    )


def gas_utilization_percent(gas_used: int, gas_limit: int) -> Optional[float]:
    """Return utilization percent, or None when gas_limit is zero."""
    if gas_limit == 0:
        return None
    if gas_used < 0 or gas_limit < 0:
        raise RpcParseError("gas values must be non-negative")
    return (gas_used / gas_limit) * 100.0
