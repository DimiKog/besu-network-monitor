#!/usr/bin/env python3
"""Unit tests for run_infrastructure_pipeline orchestration (no live SSH/Prom)."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_infrastructure_pipeline as pipe  # noqa: E402
import snapshot_publisher as publisher  # noqa: E402


VALID_SNAPSHOT = {
    "schema_version": 1,
    "generated_at": "2026-10-26T12:00:00.000000Z",
    "status": "healthy",
    "systems": {"monitored": 16, "up": 16, "down": 0},
    "validators": {"monitored": 7, "up": 7, "down": 0},
    "rpc": {"primary_up": True, "backup_up": True},
    "services": {
        "bootnode_up": True,
        "block_explorer_endpoint_up": True,
        "faucet_up": True,
        "identity_up": True,
        "backend_up": True,
        "prometheus_up": True,
        "grafana_endpoint_up": True,
    },
}


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


class InfrastructurePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.script = self.root / "collect_infrastructure.py"
        self.script.write_text("#!/usr/bin/env python3\nprint('stub')\n")
        self.snapshot = self.root / "infrastructure_snapshot.json"
        self.publisher_key = self.root / "besu_monitor_publisher"
        self.publisher_key.write_text("PUBLISHER_KEY_PLACEHOLDER\n")
        self.config = pipe.PipelineConfig(
            collector_script=self.script,
            local_snapshot=self.snapshot,
            publisher_identity=self.publisher_key,
            remote_user="ubuntu",
            remote_host="203.0.113.10",
            remote_dir="/var/lib/besu-monitor/current",
            final_name="infrastructure_snapshot.json",
            prometheus_url="http://127.0.0.1:9090",
            query_timeout=10.0,
            collect_timeout=30,
            scp_timeout=20,
            ssh_timeout=15,
            connect_timeout=5,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_valid_snapshot(self) -> None:
        self.snapshot.write_text(json.dumps(VALID_SNAPSHOT) + "\n")

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
                self.assertTrue(self._argv_has_path(argv, self.script))
                self.assertIn("--prometheus-url", argv)
                self.assertIn("http://127.0.0.1:9090", argv)
                self.assertFalse(any("besu_monitor_collector" in a for a in argv))
                self._write_valid_snapshot()
                return _completed(0, argv=argv)
            if kind == "scp":
                self.assertTrue(self._argv_has_path(argv, self.publisher_key))
                self.assertTrue(any(a.endswith(".tmp") for a in argv))
                self.assertFalse(
                    any(a.endswith(":infrastructure_snapshot.json") for a in argv)
                )
                return _completed(0, argv=argv)
            if kind == "rename":
                self.assertTrue(self._argv_has_path(argv, self.publisher_key))
                self.assertIn("mv", argv)
                self.assertIn(
                    "/var/lib/besu-monitor/current/infrastructure_snapshot.json",
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
                return _completed(1, stderr="Prometheus unreachable", argv=argv)
            self.fail(f"publish must not run after collection failure: {argv}")

        runner = FakeRunner(handler)
        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=runner)
        self.assertIn("collection failed", str(ctx.exception))
        self.assertEqual([self._classify(c["argv"]) for c in runner.calls], ["collect"])
        self.assertFalse(self.snapshot.exists())

    def test_incomplete_collection_skips_publish(self) -> None:
        """Exit 0 with incomplete schema must not publish (last-known-good safe)."""

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                incomplete = {
                    "schema_version": 1,
                    "status": "healthy",
                    # missing systems/validators/rpc/services/generated_at
                }
                self.snapshot.write_text(json.dumps(incomplete) + "\n")
                return _completed(0, argv=argv)
            self.fail(f"publish must not run after incomplete collection: {argv}")

        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertIn("incomplete snapshot", str(ctx.exception))

    def test_empty_snapshot_skips_publish(self) -> None:
        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                self.snapshot.write_text("")
                return _completed(0, argv=argv)
            self.fail(f"publish must not run after empty snapshot: {argv}")

        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertIn("empty snapshot", str(ctx.exception))

    def test_missing_snapshot_after_success_skips_publish(self) -> None:
        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                # Claim success but never write the file.
                return _completed(0, argv=argv)
            self.fail(f"publish must not run when snapshot missing: {argv}")

        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertIn("snapshot missing", str(ctx.exception))

    def test_publishing_failure_nonzero(self) -> None:
        stages: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            stages.append(kind)
            if kind == "collect":
                self._write_valid_snapshot()
                return _completed(0, argv=argv)
            if kind == "scp":
                return _completed(1, stderr="scp denied", argv=argv)
            if kind == "cleanup":
                return _completed(0, argv=argv)
            if kind == "rename":
                self.fail("rename must not run after failed upload")
            self.fail(f"unexpected argv: {argv}")

        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertIn("upload failed", str(ctx.exception))
        self.assertIn("collect", stages)
        self.assertIn("scp", stages)
        self.assertNotIn("rename", stages)

        code = None
        original = pipe.run_pipeline

        def boom(config: pipe.PipelineConfig, runner=None) -> None:
            del config, runner
            raise pipe.PipelineError("simulated publish failure")

        pipe.run_pipeline = boom  # type: ignore[assignment]
        try:
            code = pipe.main(
                [
                    "--collector-script",
                    str(self.script),
                    "--local-snapshot",
                    str(self.snapshot),
                    "--publisher-identity",
                    str(self.publisher_key),
                    "--remote-host",
                    "203.0.113.10",
                ]
            )
        finally:
            pipe.run_pipeline = original  # type: ignore[assignment]
        self.assertEqual(code, 1)

    def test_remote_atomic_temp_and_rename(self) -> None:
        rename_argv: list[str] | None = None
        temp_path: str | None = None

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            nonlocal rename_argv, temp_path
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                self._write_valid_snapshot()
                return _completed(0, argv=argv)
            if kind == "scp":
                remote = argv[-1]
                self.assertIn(":", remote)
                temp_path = remote.split(":", 1)[1]
                self.assertTrue(
                    temp_path.startswith("/var/lib/besu-monitor/current/.")
                )
                self.assertTrue(temp_path.endswith(".tmp"))
                self.assertIn("infrastructure_snapshot.json", temp_path)
                self.assertNotEqual(
                    temp_path,
                    "/var/lib/besu-monitor/current/infrastructure_snapshot.json",
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
            "/var/lib/besu-monitor/current/infrastructure_snapshot.json",
            rename_argv,
        )

    def test_correct_final_remote_filename(self) -> None:
        self.assertEqual(
            pipe.remote_final_path(self.config),
            "/var/lib/besu-monitor/current/infrastructure_snapshot.json",
        )
        self.assertEqual(
            self.config.final_name,
            "infrastructure_snapshot.json",
        )

    def test_publisher_uses_configured_dedicated_key(self) -> None:
        seen_publish: list[list[str]] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                self._write_valid_snapshot()
                return _completed(0, argv=argv)
            if kind in {"scp", "rename"}:
                seen_publish.append(argv)
                self.assertTrue(self._argv_has_path(argv, self.publisher_key))
                self.assertIn("-i", argv)
                idx = argv.index("-i")
                self.assertEqual(
                    Path(argv[idx + 1]).resolve(),
                    self.publisher_key.resolve(),
                )
                return _completed(0, argv=argv)
            self.fail(argv)

        pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertEqual(len(seen_publish), 2)

    def test_collection_failure_does_not_overwrite_last_known_good(self) -> None:
        """Remote final path is never targeted when collection fails."""
        # Simulate a pre-existing local last-known-good; collection fails and
        # must not trigger scp/mv against the remote final name.
        prior = dict(VALID_SNAPSHOT)
        prior["generated_at"] = "2026-10-25T00:00:00.000000Z"
        self.snapshot.write_text(json.dumps(prior) + "\n")
        prior_text = self.snapshot.read_text()

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                # Collector failed: leave previous local file untouched.
                return _completed(1, stderr="missing series", argv=argv)
            self.fail(f"must not touch remote after collection failure: {argv}")

        runner = FakeRunner(handler)
        with self.assertRaises(pipe.PipelineError):
            pipe.run_pipeline(self.config, runner=runner)

        self.assertEqual(self.snapshot.read_text(), prior_text)
        # No scp/ssh calls at all => remote last-known-good untouched.
        self.assertEqual(
            [self._classify(c["argv"]) for c in runner.calls],
            ["collect"],
        )
        self.assertFalse(
            any(self._classify(c["argv"]) in {"scp", "rename", "cleanup"} for c in runner.calls)
        )

    def test_pipeline_logs_omit_sensitive_prometheus_details(self) -> None:
        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                # Collector may print sanitized summary only; pipeline must not
                # echo raw PromQL / labels / metric dumps.
                self._write_valid_snapshot()
                return _completed(
                    0,
                    stdout=(
                        "Created: /tmp/infrastructure_snapshot.json\n"
                        "status=healthy systems=16/16 validators=7/7 "
                        "primary_rpc=True backup_rpc=True\n"
                    ),
                    argv=argv,
                )
            if kind in {"scp", "rename"}:
                return _completed(0, argv=argv)
            self.fail(argv)

        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            pipe.run_pipeline(self.config, runner=FakeRunner(handler))

        combined = stdout_buf.getvalue() + stderr_buf.getvalue()
        forbidden = (
            "besu_validators_nginx",
            "node_exporter",
            "instance_name",
            "up{",
            "validator1",
            "keycloak",
            "blackbox",
            "/api/v1/query",
            "Authorization",
            "password",
            "PromQL",
            "raw sample",
        )
        for token in forbidden:
            self.assertNotIn(token, combined)
        self.assertIn("pipeline: collecting infrastructure", combined)
        self.assertIn("pipeline: published", combined)

    def test_failed_rename_cleans_temp_and_fails(self) -> None:
        stages: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            stages.append(kind)
            if kind == "collect":
                self._write_valid_snapshot()
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
        self.assertIn("cleanup", stages)

    def test_default_publisher_identity_is_dedicated_key(self) -> None:
        self.assertEqual(
            pipe.DEFAULT_PUBLISHER_IDENTITY,
            Path.home() / ".ssh" / "besu_monitor_publisher",
        )
        self.assertEqual(
            pipe.DEFAULT_PUBLISHER_IDENTITY,
            publisher.DEFAULT_PUBLISHER_IDENTITY,
        )

    def test_missing_remote_host_fails_before_transport(self) -> None:
        previous = os.environ.get(publisher.ENV_REMOTE_HOST)
        try:
            os.environ.pop(publisher.ENV_REMOTE_HOST, None)
            with self.assertRaises(pipe.PipelineError) as ctx:
                pipe.config_from_args(
                    pipe.parse_args(
                        [
                            "--collector-script",
                            str(self.script),
                            "--local-snapshot",
                            str(self.snapshot),
                            "--publisher-identity",
                            str(self.publisher_key),
                        ]
                    )
                )
            self.assertIn("remote host", str(ctx.exception).lower())
        finally:
            if previous is None:
                os.environ.pop(publisher.ENV_REMOTE_HOST, None)
            else:
                os.environ[publisher.ENV_REMOTE_HOST] = previous

        calls: list[list[str]] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            calls.append(list(argv))
            return _completed(0, argv=argv)

        bare = pipe.PipelineConfig(
            collector_script=self.script,
            local_snapshot=self.snapshot,
            publisher_identity=self.publisher_key,
            remote_host="",
        )
        self._write_valid_snapshot()
        with self.assertRaises(pipe.PipelineError) as ctx:
            pipe.publish_snapshot(bare, self.snapshot, runner=FakeRunner(handler))
        self.assertIn("remote host", str(ctx.exception).lower())
        self.assertEqual(calls, [])

    def test_cli_remote_host_overrides_env(self) -> None:
        previous = os.environ.get(publisher.ENV_REMOTE_HOST)
        try:
            os.environ[publisher.ENV_REMOTE_HOST] = "203.0.113.99"
            cfg = pipe.config_from_args(
                pipe.parse_args(
                    [
                        "--collector-script",
                        str(self.script),
                        "--local-snapshot",
                        str(self.snapshot),
                        "--publisher-identity",
                        str(self.publisher_key),
                        "--remote-host",
                        "203.0.113.10",
                    ]
                )
            )
            self.assertEqual(cfg.remote_host, "203.0.113.10")
        finally:
            if previous is None:
                os.environ.pop(publisher.ENV_REMOTE_HOST, None)
            else:
                os.environ[publisher.ENV_REMOTE_HOST] = previous

    def test_configured_publisher_host_used_in_scp(self) -> None:
        seen_scp: list[str] = []

        def handler(argv: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
            del timeout
            kind = self._classify(argv)
            if kind == "collect":
                self._write_valid_snapshot()
                return _completed(0, argv=argv)
            if kind == "scp":
                seen_scp.extend(argv)
                return _completed(0, argv=argv)
            if kind == "rename":
                return _completed(0, argv=argv)
            self.fail(argv)

        pipe.run_pipeline(self.config, runner=FakeRunner(handler))
        self.assertTrue(any("203.0.113.10:" in part for part in seen_scp))


class SharedPublisherRegressionTests(unittest.TestCase):
    """Consensus pipeline still publishes via the shared helper."""

    def test_consensus_pipeline_delegates_publish_config(self) -> None:
        import run_consensus_pipeline as consensus

        self.assertIs(consensus.default_runner, publisher.default_runner)
        self.assertIs(consensus.scp_argv, publisher.scp_argv)
        self.assertIs(consensus.ssh_base_argv, publisher.ssh_base_argv)

        cfg = consensus.PipelineConfig(
            final_name="consensus_snapshot.json",
            remote_dir="/var/lib/besu-monitor/current",
            remote_user="ubuntu",
            remote_host="203.0.113.10",
        )
        publish_cfg = consensus._publish_config(cfg)
        self.assertIsInstance(publish_cfg, publisher.PublishConfig)
        self.assertEqual(publish_cfg.final_name, "consensus_snapshot.json")
        self.assertEqual(
            publisher.remote_final_path(publish_cfg),
            "/var/lib/besu-monitor/current/consensus_snapshot.json",
        )


if __name__ == "__main__":
    unittest.main()
