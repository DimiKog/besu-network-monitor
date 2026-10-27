#!/usr/bin/env python3
"""Focused CORS policy tests for the Monitor API."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from api.config import (  # noqa: E402
    DEFAULT_CORS_ORIGINS,
    Settings,
    parse_cors_origins,
)
from api.main import create_app  # noqa: E402

APPROVED_ORIGIN = "https://monitor.dimikog.org"
UNAPPROVED_ORIGIN = "https://example.com"


class CorsConfigTests(unittest.TestCase):
    def test_default_origins_are_production_only(self) -> None:
        self.assertEqual(DEFAULT_CORS_ORIGINS, (APPROVED_ORIGIN,))
        self.assertNotIn("*", DEFAULT_CORS_ORIGINS)
        self.assertFalse(
            any("localhost" in origin for origin in DEFAULT_CORS_ORIGINS)
        )
        settings = Settings(
            consensus_snapshot_path=Path("/tmp/unused-consensus.json"),
            stale_after_seconds=900,
            future_tolerance_seconds=60,
        )
        self.assertEqual(settings.cors_origins, (APPROVED_ORIGIN,))
        self.assertNotIn("*", settings.cors_origins)
        self.assertFalse(any("localhost" in o for o in settings.cors_origins))

    def test_parse_comma_separated_origins(self) -> None:
        parsed = parse_cors_origins(
            " https://monitor.dimikog.org , http://localhost:3000 , "
        )
        self.assertEqual(
            parsed,
            ("https://monitor.dimikog.org", "http://localhost:3000"),
        )

    def test_parse_ignores_empty_entries_and_blank_env(self) -> None:
        self.assertEqual(
            parse_cors_origins(" , , "),
            DEFAULT_CORS_ORIGINS,
        )
        self.assertEqual(parse_cors_origins(""), DEFAULT_CORS_ORIGINS)
        self.assertEqual(parse_cors_origins(None), DEFAULT_CORS_ORIGINS)

    def test_settings_from_environ_cors_override(self) -> None:
        previous = os.environ.get("BESU_MONITOR_CORS_ORIGINS")
        try:
            os.environ["BESU_MONITOR_CORS_ORIGINS"] = (
                "https://monitor.dimikog.org, http://localhost:5173"
            )
            settings = Settings.from_environ()
            self.assertEqual(
                settings.cors_origins,
                ("https://monitor.dimikog.org", "http://localhost:5173"),
            )
        finally:
            if previous is None:
                os.environ.pop("BESU_MONITOR_CORS_ORIGINS", None)
            else:
                os.environ["BESU_MONITOR_CORS_ORIGINS"] = previous


class CorsMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        root = Path(self._tmpdir.name)
        self.settings = Settings(
            consensus_snapshot_path=root / "consensus_snapshot.json",
            infrastructure_snapshot_path=root / "infrastructure_snapshot.json",
            stale_after_seconds=900,
            future_tolerance_seconds=60,
            cors_origins=DEFAULT_CORS_ORIGINS,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_approved_origin_get_receives_allow_origin(self) -> None:
        response = self.client.get(
            "/api/v1/health",
            headers={"Origin": APPROVED_ORIGIN},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            APPROVED_ORIGIN,
        )
        # Credentials must remain disabled (header absent or false).
        acac = response.headers.get("access-control-allow-credentials")
        self.assertTrue(acac is None or acac.lower() == "false")

    def test_unapproved_origin_get_has_no_allow_origin(self) -> None:
        response = self.client.get(
            "/api/v1/health",
            headers={"Origin": UNAPPROVED_ORIGIN},
        )
        # API remains reachable; CORS simply withholds the browser allow header.
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.headers.get("access-control-allow-origin"))

    def test_preflight_approved_get_is_accepted(self) -> None:
        response = self.client.options(
            "/api/v1/health",
            headers={
                "Origin": APPROVED_ORIGIN,
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            APPROVED_ORIGIN,
        )
        allow_methods = response.headers.get("access-control-allow-methods", "")
        self.assertIn("GET", allow_methods.upper())
        self.assertNotIn("POST", allow_methods.upper())

    def test_preflight_disallowed_post_is_rejected(self) -> None:
        response = self.client.options(
            "/api/v1/health",
            headers={
                "Origin": APPROVED_ORIGIN,
                "Access-Control-Request-Method": "POST",
            },
        )
        # Starlette returns 400 for disallowed methods; browsers treat this as
        # a failed CORS preflight. Allow-Methods must not advertise POST.
        self.assertEqual(response.status_code, 400)
        self.assertIn("method", response.text.lower())
        allow_methods = response.headers.get("access-control-allow-methods", "")
        self.assertIn("GET", allow_methods.upper())
        self.assertNotIn("POST", allow_methods.upper())

    def test_override_allows_additional_origin(self) -> None:
        local = "http://localhost:3000"
        settings = Settings(
            consensus_snapshot_path=self.settings.consensus_snapshot_path,
            infrastructure_snapshot_path=self.settings.infrastructure_snapshot_path,
            stale_after_seconds=900,
            future_tolerance_seconds=60,
            cors_origins=(APPROVED_ORIGIN, local),
        )
        client = TestClient(create_app(settings))
        response = client.get("/api/v1/health", headers={"Origin": local})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            local,
        )


if __name__ == "__main__":
    unittest.main()
