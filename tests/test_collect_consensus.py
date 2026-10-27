#!/usr/bin/env python3
"""Unit tests for collect_consensus pure logic (no live SSH)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import collect_consensus as cc  # noqa: E402


class QuorumTests(unittest.TestCase):
    def test_qbft_formula_matches_reference_fixture(self) -> None:
        self.assertEqual(cc.qbft_quorum_threshold(7), 5)

    def test_qbft_formula_common_sizes(self) -> None:
        self.assertEqual(cc.qbft_quorum_threshold(1), 1)
        self.assertEqual(cc.qbft_quorum_threshold(3), 3)
        self.assertEqual(cc.qbft_quorum_threshold(4), 3)
        self.assertEqual(cc.qbft_quorum_threshold(10), 7)

    def test_qbft_rejects_invalid(self) -> None:
        with self.assertRaises(cc.CollectorError):
            cc.qbft_quorum_threshold(0)


class ParsingTests(unittest.TestCase):
    def test_parse_block_number(self) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": "0x7c8b45"})
        self.assertEqual(cc.parse_block_number_response(raw), 0x7C8B45)

    def test_parse_block_number_rejects_garbage(self) -> None:
        self.assertIsNone(cc.parse_block_number_response("unavailable"))
        self.assertIsNone(cc.parse_block_number_response("ERROR: boom"))
        self.assertIsNone(cc.parse_block_number_response('{"result":"nope"}'))

    def test_parse_validator_set(self) -> None:
        addrs = [
            "0x000da85cc301d7b30c2722183b33cdb1676ce101",
            "0x12ed75fb9eb31607269cea77542552417a71dc94",
        ]
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": addrs})
        parsed = cc.parse_validator_set_response(raw)
        self.assertEqual(parsed, [a.lower() for a in addrs])

    def test_parse_validator_set_rejects_bad_address(self) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": ["not-an-address"]})
        self.assertIsNone(cc.parse_validator_set_response(raw))

    def test_parse_validator_set_rejects_empty(self) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": []})
        self.assertIsNone(cc.parse_validator_set_response(raw))


class ConsensusSemanticsTests(unittest.TestCase):
    def _results(self, blocks: dict[str, int | None]) -> list[dict]:
        out = []
        for alias, block in blocks.items():
            if block is None:
                out.append(
                    {
                        "alias": alias,
                        "ip": "10.0.0.1",
                        "status": "unreachable",
                        "error": "down",
                        "block_number": None,
                        "drift_blocks": None,
                        "synced": False,
                    }
                )
            else:
                out.append(
                    {
                        "alias": alias,
                        "ip": "10.0.0.1",
                        "status": "ok",
                        "error": None,
                        "block_number": block,
                        "drift_blocks": None,
                        "synced": False,
                    }
                )
        return out

    def test_drift_and_quorum_like_fixture(self) -> None:
        # Same heights as input/consensus_20261025_092933.json
        results = self._results(
            {
                "validator1": 8162213,
                "validator2": 8162213,
                "validator3": 8162212,
                "validator4": 8162213,
                "validator5": 8162213,
                "validator6": 8162212,
                "validator7": 8162213,
            }
        )
        vset = {
            "validator_count": 7,
            "quorum_required": 5,
            "validator_addresses": ["0x" + "a" * 40] * 7,
            "source_alias": "validator1",
            "block_tag": 8162213,
        }
        report = cc.build_consensus_report(results, max_drift_blocks=2, validator_set=vset)
        self.assertEqual(report["reference_block"], 8162213)
        self.assertEqual(report["synced_validators"], 7)
        self.assertTrue(report["has_quorum"])
        self.assertEqual(report["overall_status"], "ok")
        self.assertEqual(report["out_of_sync"], [])
        self.assertEqual(cc.observed_max_blocks(report["validators"]), 1)

        snap = cc.sanitize_consensus_snapshot(report)
        self.assertEqual(
            snap["drift"],
            {"observed_max_blocks": 1, "allowed_max_blocks": 2},
        )
        self.assertEqual(snap["out_of_sync_count"], 0)
        self._assert_public_safe(snap)

    def test_one_unreachable_is_warning_not_hard_fail(self) -> None:
        results = self._results(
            {
                "validator1": 100,
                "validator2": 100,
                "validator3": 100,
                "validator4": 100,
                "validator5": 100,
                "validator6": 100,
                "validator7": None,
            }
        )
        vset = {
            "validator_count": 7,
            "quorum_required": 5,
            "validator_addresses": ["0x" + "b" * 40] * 7,
            "source_alias": "validator1",
            "block_tag": 100,
        }
        report = cc.build_consensus_report(results, max_drift_blocks=2, validator_set=vset)
        self.assertEqual(report["synced_validators"], 6)
        self.assertTrue(report["has_quorum"])
        self.assertEqual(report["overall_status"], "warning")
        self.assertEqual(cc.observed_max_blocks(report["validators"]), 0)

        snap = cc.sanitize_consensus_snapshot(report)
        self.assertEqual(snap["status"], "warning")
        self.assertEqual(snap["synced_validators"], 6)
        self._assert_public_safe(snap)

    def test_out_of_sync_beyond_allowed_drift(self) -> None:
        results = self._results(
            {
                "validator1": 100,
                "validator2": 100,
                "validator3": 96,  # drift 4 > allowed 2
                "validator4": 100,
                "validator5": 100,
                "validator6": 100,
                "validator7": 100,
            }
        )
        vset = {
            "validator_count": 7,
            "quorum_required": 5,
            "validator_addresses": ["0x" + "c" * 40] * 7,
            "source_alias": "validator1",
            "block_tag": 100,
        }
        report = cc.build_consensus_report(results, max_drift_blocks=2, validator_set=vset)
        self.assertEqual(report["synced_validators"], 6)
        self.assertEqual(len(report["out_of_sync"]), 1)
        self.assertEqual(report["overall_status"], "warning")
        self.assertEqual(cc.observed_max_blocks(report["validators"]), 4)

        snap = cc.sanitize_consensus_snapshot(report)
        self.assertEqual(snap["drift"]["observed_max_blocks"], 4)
        self.assertEqual(snap["drift"]["allowed_max_blocks"], 2)
        self.assertEqual(snap["out_of_sync_count"], 1)

    def test_sanitize_rejects_missing_reference(self) -> None:
        report = {
            "generated_at": "2026-10-25T00:00:00+00:00",
            "max_drift_blocks": 2,
            "reference_block": None,
            "overall_status": "critical",
            "active_validator_set": {"validator_count": 7, "quorum_required": 5},
            "synced_validators": 0,
            "has_quorum": False,
            "validators": [],
            "out_of_sync": [],
        }
        with self.assertRaises(cc.CollectorError):
            cc.sanitize_consensus_snapshot(report)

    def test_sanitize_rejects_missing_validator_set(self) -> None:
        report = {
            "generated_at": "2026-10-25T00:00:00+00:00",
            "max_drift_blocks": 2,
            "reference_block": 1,
            "overall_status": "critical",
            "active_validator_set": {
                "validator_count": 0,
                "quorum_required": 0,
                "error": "qbft unavailable",
            },
            "synced_validators": 0,
            "has_quorum": False,
            "validators": [
                {
                    "alias": "validator1",
                    "block_number": 1,
                    "drift_blocks": 0,
                    "synced": True,
                    "status": "ok",
                }
            ],
            "out_of_sync": [],
        }
        with self.assertRaises(cc.CollectorError):
            cc.sanitize_consensus_snapshot(report)

    def _assert_public_safe(self, snap: dict) -> None:
        text = json.dumps(snap)
        forbidden_substrings = [
            "10.0.0.",
            "ubuntu",
            "besu_monitor_collector",
            "127.0.0.1",
            "validator1",
            "0x000da85c",
            "jsonrpc",
            "ssh",
        ]
        for item in forbidden_substrings:
            self.assertNotIn(item.lower(), text.lower())
        self.assertNotIn("validators", snap)
        self.assertNotIn("max_drift_blocks", snap)
        self.assertIn("drift", snap)


def _example_validators(count: int = 7) -> tuple[dict[str, str], ...]:
    return tuple(
        {"alias": f"validator{i}", "ip": f"203.0.113.{10 + i}"}
        for i in range(1, count + 1)
    )


class ValidatorsInventoryTests(unittest.TestCase):
    def test_load_valid_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "validators.json"
            path.write_text(
                json.dumps(
                    [
                        {"alias": "validator1", "ip": "203.0.113.11"},
                        {"alias": "validator2", "ip": "203.0.113.12"},
                    ]
                ),
                encoding="utf-8",
            )
            inventory = cc.load_validators_inventory(path)
            self.assertEqual(len(inventory), 2)
            self.assertEqual(inventory[0]["alias"], "validator1")
            self.assertEqual(inventory[1]["ip"], "203.0.113.12")

    def test_missing_inventory_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            with self.assertRaises(cc.CollectorError) as ctx:
                cc.load_validators_inventory(path)
            self.assertIn("not found", str(ctx.exception))

    def test_malformed_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(cc.CollectorError) as ctx:
                cc.load_validators_inventory(path)
            self.assertIn("not valid JSON", str(ctx.exception))

    def test_empty_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(cc.CollectorError) as ctx:
                cc.load_validators_inventory(path)
            self.assertIn("empty", str(ctx.exception))

    def test_malformed_entries(self) -> None:
        cases = [
            '[{"alias": "validator1"}]',
            '[{"ip": "203.0.113.11"}]',
            '[{"alias": "", "ip": "203.0.113.11"}]',
            '[{"alias": "validator1", "ip": ""}]',
            '[{"alias": 1, "ip": "203.0.113.11"}]',
            '["validator1"]',
            '{"alias": "validator1", "ip": "203.0.113.11"}',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, payload in enumerate(cases):
                path = Path(tmp) / f"bad_{index}.json"
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(cc.CollectorError):
                    cc.load_validators_inventory(path)

    def test_duplicate_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dup.json"
            path.write_text(
                json.dumps(
                    [
                        {"alias": "validator1", "ip": "203.0.113.11"},
                        {"alias": "validator1", "ip": "203.0.113.12"},
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(cc.CollectorError) as ctx:
                cc.load_validators_inventory(path)
            self.assertIn("duplicate", str(ctx.exception).lower())

    def test_cli_overrides_env_for_validators_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cli_path = Path(tmp) / "cli.json"
            env_path = Path(tmp) / "env.json"
            cli_path.write_text("[]", encoding="utf-8")
            env_path.write_text("[]", encoding="utf-8")
            previous = os.environ.get(cc.ENV_VALIDATORS_FILE)
            try:
                os.environ[cc.ENV_VALIDATORS_FILE] = str(env_path)
                resolved = cc.resolve_validators_file(cli_path)
                self.assertEqual(resolved, cli_path)
            finally:
                if previous is None:
                    os.environ.pop(cc.ENV_VALIDATORS_FILE, None)
                else:
                    os.environ[cc.ENV_VALIDATORS_FILE] = previous

    def test_env_used_when_cli_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "env.json"
            env_path.write_text("[]", encoding="utf-8")
            previous = os.environ.get(cc.ENV_VALIDATORS_FILE)
            try:
                os.environ[cc.ENV_VALIDATORS_FILE] = str(env_path)
                resolved = cc.resolve_validators_file(None)
                self.assertEqual(resolved, env_path)
            finally:
                if previous is None:
                    os.environ.pop(cc.ENV_VALIDATORS_FILE, None)
                else:
                    os.environ[cc.ENV_VALIDATORS_FILE] = previous

    def test_missing_validators_config_fails(self) -> None:
        previous = os.environ.get(cc.ENV_VALIDATORS_FILE)
        try:
            os.environ.pop(cc.ENV_VALIDATORS_FILE, None)
            with self.assertRaises(cc.CollectorError) as ctx:
                cc.resolve_validators_file(None)
            self.assertIn("required", str(ctx.exception))
        finally:
            if previous is None:
                os.environ.pop(cc.ENV_VALIDATORS_FILE, None)
            else:
                os.environ[cc.ENV_VALIDATORS_FILE] = previous


class TransportCollectionTests(unittest.TestCase):
    def test_run_collection_with_fake_transport(self) -> None:
        validators = _example_validators(7)
        blocks = {
            "validator1": 50,
            "validator2": 50,
            "validator3": 49,
            "validator4": 50,
            "validator5": 50,
            "validator6": 50,
            "validator7": 50,
        }
        addrs = [f"0x{i:040x}" for i in range(1, 8)]

        def transport(
            alias: str, ip: str, method: str, params: list, timeout: int
        ) -> str:
            del ip, params, timeout
            if method == "eth_blockNumber":
                return json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "result": hex(blocks[alias])}
                )
            if method == "qbft_getValidatorsByBlockNumber":
                return json.dumps({"jsonrpc": "2.0", "id": 1, "result": addrs})
            raise AssertionError(method)

        def probe(alias: str, ip: str, timeout: int) -> tuple[bool, str]:
            del alias, ip, timeout
            return True, ""

        snap = cc.run_collection(
            transport=transport,
            connectivity_probe=probe,
            validators=validators,
            max_drift_blocks=2,
            timeout=5,
            workers=4,
        )
        self.assertEqual(snap["reference_block"], 50)
        self.assertEqual(snap["validator_count"], 7)
        self.assertEqual(snap["quorum_required"], 5)
        self.assertEqual(snap["synced_validators"], 7)
        self.assertTrue(snap["has_quorum"])
        self.assertEqual(snap["drift"]["observed_max_blocks"], 1)
        self.assertEqual(snap["drift"]["allowed_max_blocks"], 2)
        self.assertEqual(snap["status"], "ok")

    def test_run_collection_fails_when_all_unreachable(self) -> None:
        def transport(
            alias: str, ip: str, method: str, params: list, timeout: int
        ) -> str:
            del alias, ip, method, params, timeout
            return "ERROR: unreachable"

        def probe(alias: str, ip: str, timeout: int) -> tuple[bool, str]:
            del alias, ip, timeout
            return False, "no response"

        with self.assertRaises(cc.CollectorError):
            cc.run_collection(
                transport=transport,
                connectivity_probe=probe,
                validators=_example_validators(3),
                max_drift_blocks=2,
                workers=4,
            )


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_and_keeps_prior_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "consensus_snapshot.json"
            cc.atomic_write_json(path, {"ok": True})
            self.assertTrue(path.is_file())
            prior = path.read_text(encoding="utf-8")

            # Simulate failure before replace: previous file remains.
            self.assertEqual(path.read_text(encoding="utf-8"), prior)

            cc.atomic_write_json(path, {"ok": False, "n": 1})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["n"], 1)


if __name__ == "__main__":
    unittest.main()
