#!/usr/bin/env python3
"""Unit tests for run_consensus_pipeline orchestration (no live SSH)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_consensus_pipeline as pipe  # noqa: E402


def _completed(
    code: int = 0,
    stdout: str = "",
    stderr: str = "",
    argv: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=argv or [],
        returncode=code,
        stdout=stdout,
        stderr=stderr,
    )


class FakeRunner:
    """Records argv lists and never uses a real shell."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, argv: list[str] | tuple[str, ...], *, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        argv_list = list(argv)
        self.calls.append({"argv": argv_list, "timeout": timeout, "shell": False})
        return self.handler(argv_list, timeout)


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.script = self.root / "collect_consensus.py"
        self.script.write_text("#!/usr/bin/env python3\nprint('stub')\n")
        self.snapshot = self.root / "consensus_snapshot.json"
        self.collector_key = self.root / "besu_monitor_collector"
        self.publisher_key = self.root / "besu_monitor_publisher"
        self.collector_key.write_text("COLLECTOR_KEY_PLACEHOLDER\n")
        self.publisher_key.write_text("PUBLISHER_KEY_PLACEHOLDER\n")
        self.config = pipe.PipelineConfig(
            collector_script=self.script,
            local_snapshot=self.snapshot,
            collector_identity=self.collector_key,
            publisher_identity=self.publisher_key,
            remote_user="ubuntu",
            remote_host="203.0.113.10",
            remote_dir="/var/lib/besu-monitor/current",
            final_name="consensus_snapshot.json",
            max_drift=2,
            workers=7,
            collect_timeout=30,
            scp_timeout=20,
            ssh_timeout=15,
            connect_timeout=5,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _classify(self, argv: list[str]) -> str:
        if argv and argv[0] == sys.executable:
            return "collect"
        if argv and argv[0] == "scp":
            return "scp"
        if argv and argv[0] == "ssh" and "mv" in argv:
            return "rename"
        if argv and argv[0] == "ssh" and "rm" in argv:
            return "cleanup"
        return "other"

    def _argv_has_path(self, argv: list[str], path: Path) -> bool:
        target = path.expanduser().resolve()
        return any(Path(part).expanduser().resolve() == target for part in argv)

    def test_success_collection_then_publish(self) -> None:
        stages: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            stages.append(kind)
            if kind == "collect":
                self.assertTrue(self._argv_has_path(argv, self.collector_key))
                self.assertFalse(self._argv_has_path(argv, self.publisher_key))
                self.snapshot.write_text('{"schema_version":"1.0"}\n')
                return _completed(0, argv=argv)
            if kind == "scp":
                self.assertTrue(self._argv_has_path(argv, self.publisher_key))
                self.assertFalse(self._argv_has_path(argv, self.collector_key))
                self.assertTrue(any(a.endswith(".tmp") for a in argv))
                self.assertFalse(any(a.endswith(":consensus_snapshot.json") for a in argv))
                return _completed(0, argv=argv)
            if kind == "rename":
                self.assertTrue(self._argv_has_path(argv, self.publisher_key))
                self.assertIn("mv", argv)
                self.assertIn(
                    "/var/lib/besu-monitor/current/consensus_snapshot.json",
                    argv,
                )
                return _completed(0, argv=argv)
            self.fail(f"unexpected argv: {argv}")

        runner = FakeRunner(handler)
        pipe.run_pipeline(self.config, runner=runner)
        self.assertEqual(stages, ["collect", "scp", "rename"])
        for call in runner.calls:
            self.assertIs(call["shell"], False)

    def test_failed_collection_skips_publish(self) -> None:
        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                return _completed(1, stderr="collector boom", argv=argv)
            self.fail(f"publish must not run after collection failure: {argv}")

        runner = FakeRunner(handler)
        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=runner)
        self.assertIn("collection failed", str(ctx.exception))
        self.assertEqual([self._classify(c["argv"]) for c in runner.calls], ["collect"])
        self.assertFalse(self.snapshot.exists())

    def test_failed_upload_does_not_rename_final(self) -> None:
        stages: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            stages.append(kind)
            if kind == "collect":
                self.snapshot.write_text('{"ok":true}\n')
                return _completed(0, argv=argv)
            if kind == "scp":
                return _completed(1, stderr="scp denied", argv=argv)
            if kind == "cleanup":
                return _completed(0, argv=argv)
            if kind == "rename":
                self.fail("rename must not run after failed upload")
            self.fail(f"unexpected argv: {argv}")

        runner = FakeRunner(handler)
        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=runner)
        self.assertIn("upload failed", str(ctx.exception))
        self.assertIn("collect", stages)
        self.assertIn("scp", stages)
        self.assertIn("cleanup", stages)
        self.assertNotIn("rename", stages)

    def test_successful_upload_performs_atomic_rename(self) -> None:
        rename_argv: list[str] | None = None
        temp_path: str | None = None

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            nonlocal rename_argv, temp_path
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                self.snapshot.write_text('{"ok":true}\n')
                return _completed(0, argv=argv)
            if kind == "scp":
                remote = argv[-1]
                self.assertIn(":", remote)
                temp_path = remote.split(":", 1)[1]
                self.assertTrue(temp_path.startswith("/var/lib/besu-monitor/current/."))
                self.assertTrue(temp_path.endswith(".tmp"))
                self.assertNotEqual(
                    temp_path,
                    "/var/lib/besu-monitor/current/consensus_snapshot.json",
                )
                return _completed(0, argv=argv)
            if kind == "rename":
                rename_argv = argv
                return _completed(0, argv=argv)
            self.fail(f"unexpected argv: {argv}")

        pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        assert rename_argv is not None and temp_path is not None
        self.assertIn("mv", rename_argv)
        self.assertIn("-f", rename_argv)
        self.assertIn(temp_path, rename_argv)
        self.assertIn(
            "/var/lib/besu-monitor/current/consensus_snapshot.json",
            rename_argv,
        )

    def test_failed_rename_exits_nonzero(self) -> None:
        stages: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            stages.append(kind)
            if kind == "collect":
                self.snapshot.write_text('{"ok":true}\n')
                return _completed(0, argv=argv)
            if kind == "scp":
                return _completed(0, argv=argv)
            if kind == "rename":
                return _completed(1, stderr="mv failed", argv=argv)
            if kind == "cleanup":
                return _completed(0, argv=argv)
            self.fail(f"unexpected argv: {argv}")

        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertIn("remote rename failed", str(ctx.exception))
        self.assertEqual(stages.count("rename"), 1)
        self.assertIn("cleanup", stages)

    def test_cli_maps_pipeline_error_to_exit_1(self) -> None:
        original = pipe.run_pipeline

        def boom(config: pipe.PipelineConfig, runner=None) -> None:
            del config, runner
            raise pipe.PipelineError("simulated failure")

        pipe.run_pipeline = boom  # type: ignore[assignment]
        try:
            code = pipe.main(
                [
                    "--collector-script",
                    str(self.script),
                    "--local-snapshot",
                    str(self.snapshot),
                    "--collector-identity",
                    str(self.collector_key),
                    "--publisher-identity",
                    str(self.publisher_key),
                ]
            )
        finally:
            pipe.run_pipeline = original  # type: ignore[assignment]
        self.assertEqual(code, 1)

    def test_identities_remain_separate(self) -> None:
        # Same path rejected.
        bad = pipe.PipelineConfig(
            collector_script=self.script,
            local_snapshot=self.snapshot,
            collector_identity=self.collector_key,
            publisher_identity=self.collector_key,
        )
        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.ensure_identities_separate(bad)
        self.assertIn("must be different", str(ctx.exception))

        # Successful path uses collector key only for collect, publisher only for scp/ssh.
        seen_collect: list[str] = []
        seen_publish: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            joined = " ".join(argv)
            if kind == "collect":
                seen_collect.append(joined)
                self.snapshot.write_text("{}\n")
                return _completed(0, argv=argv)
            if kind in {"scp", "rename"}:
                seen_publish.append(joined)
                return _completed(0, argv=argv)
            self.fail(argv)

        pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertTrue(seen_collect and seen_publish)
        for line in seen_collect:
            parts = line.split()
            self.assertTrue(self._argv_has_path(parts, self.collector_key))
            self.assertFalse(self._argv_has_path(parts, self.publisher_key))
        for line in seen_publish:
            parts = line.split()
            self.assertTrue(self._argv_has_path(parts, self.publisher_key))
            self.assertFalse(self._argv_has_path(parts, self.collector_key))

    def test_subprocess_helpers_never_enable_shell(self) -> None:
        # Inspect default_runner source contract via a monkeypatch around subprocess.run.
        recorded: dict[str, Any] = {}

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            recorded["args"] = args
            recorded["kwargs"] = kwargs
            return _completed(0)

        original = subprocess.run
        subprocess.run = fake_run  # type: ignore[assignment]
        try:
            result = pipe.default_runner(["/bin/echo", "ok"], timeout=5)
        finally:
            subprocess.run = original  # type: ignore[assignment]

        self.assertEqual(result.returncode, 0)
        self.assertIs(recorded["kwargs"].get("shell"), False)
        self.assertIsInstance(recorded["args"][0], list)

        # Builder helpers produce argv arrays, not shell strings.
        scp = pipe.scp_argv(
            self.publisher_key,
            5,
            self.snapshot,
            "ubuntu@host:/tmp/x.tmp",
        )
        self.assertEqual(scp[0], "scp")
        self.assertIsInstance(scp, list)
        ssh = pipe.ssh_base_argv(self.publisher_key, 5, "ubuntu@host")
        self.assertEqual(ssh[0], "ssh")
        self.assertIsInstance(ssh, list)


if __name__ == "__main__":
    unittest.main()
