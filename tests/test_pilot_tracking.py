"""Tests for anonymous pilot tracking (no deploy, no API keys)."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import pilot_tracking as pilot
from app import app


class PilotTrackingModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pilot.DATA_DIR = pilot.Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        # Disable database mode for file-based tests
        self.original_use_db = pilot.USE_DATABASE
        pilot.USE_DATABASE = False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        pilot.USE_DATABASE = self.original_use_db

    def test_event_and_feedback_flow(self):
        ok, _ = pilot.record_event(
            "ps_test1", "site_visit",
            utm_source="youtube", utm_campaign="pilot5", utm_content="f01",
        )
        self.assertTrue(ok)
        pilot.record_event("ps_test1", "anketa_started")
        pilot.record_event("ps_test1", "anketa_saved", meta={"language": "ru-RU"})
        pilot.record_event("ps_test1", "voice_started")
        pilot.record_event("ps_test1", "conversation")

        ok, _ = pilot.record_feedback(
            "ps_test1", "yes", "partial", "yes",
            issues="Всё понятно",
            utm_source="youtube", utm_campaign="pilot5", utm_content="f01",
        )
        self.assertTrue(ok)

        summary = pilot.build_summary()
        self.assertEqual(summary["family_count"], 1)
        fam = summary["families"][0]
        self.assertEqual(fam["family_key"], "f01")
        self.assertTrue(fam["reached_conversation"])
        self.assertEqual(fam["feedback_count"], 1)

    def test_two_families_isolated(self):
        pilot.record_event("ps_a", "conversation", utm_content="f01")
        pilot.record_event("ps_b", "site_visit", utm_content="f02")
        summary = pilot.build_summary()
        keys = {f["family_key"] for f in summary["families"]}
        self.assertEqual(keys, {"f01", "f02"})


class PilotRoutesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pilot.DATA_DIR = pilot.Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        self._old_token = os.environ.get("PILOT_ADMIN_TOKEN")
        os.environ["PILOT_ADMIN_TOKEN"] = "test-secret"
        self.client = app.test_client()
        # Disable database mode for file-based tests
        self.original_use_db = pilot.USE_DATABASE
        pilot.USE_DATABASE = False

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._old_token is None:
            os.environ.pop("PILOT_ADMIN_TOKEN", None)
        else:
            os.environ["PILOT_ADMIN_TOKEN"] = self._old_token
        pilot.USE_DATABASE = self.original_use_db

    def test_full_funnel_via_api(self):
        sid = "ps_route_test"
        steps = [
            "site_visit",
            "anketa_started",
            "anketa_saved",
            "voice_started",
            "conversation",
        ]
        for step in steps:
            resp = self.client.post(
                "/pilot/event",
                json={
                    "session_id": sid,
                    "event": step,
                    "utm_source": "youtube",
                    "utm_campaign": "pilot5",
                    "utm_content": "f03",
                    "meta": {"language": "ru-RU"} if step == "anketa_saved" else {},
                },
            )
            self.assertEqual(resp.status_code, 200, step)
            self.assertTrue(resp.get_json()["ok"], step)

        fb = self.client.post(
            "/pilot/feedback",
            json={
                "session_id": sid,
                "comfortable": "yes",
                "liked": "yes",
                "continue_pilot": "maybe",
                "issues": "",
                "utm_source": "youtube",
                "utm_campaign": "pilot5",
                "utm_content": "f03",
            },
        )
        self.assertEqual(fb.status_code, 200)

        denied = self.client.get("/pilot/summary")
        self.assertEqual(denied.status_code, 403)

        ok = self.client.get("/pilot/summary?token=test-secret&format=json")
        self.assertEqual(ok.status_code, 200)
        data = ok.get_json()
        fam = next(f for f in data["families"] if f["family_key"] == "f03")
        self.assertTrue(fam["reached_conversation"])
        self.assertEqual(fam["feedback_count"], 1)


class DatabaseUrlSanitizationTest(unittest.TestCase):
    """Test _sanitize_database_url function with various messy inputs."""
    
    def test_multi_line_env_block(self):
        """Handle multi-line Neon .env block."""
        raw = """DATABASE_URL=postgresql://user:pass@host.neon.tech/db
DATABASE_URL_UNPOOLED=postgresql://user:pass@host.neon.tech/db?sslmode=require
PGHOST=host.neon.tech"""
        result = pilot._sanitize_database_url(raw)
        self.assertIn("postgresql://", result)
        self.assertIn("host.neon.tech", result)
        # Should prefer DATABASE_URL over DATABASE_URL_UNPOOLED
        self.assertNotIn("UNPOOLED", result)
    
    def test_psql_snippet(self):
        """Extract URL from psql command snippet."""
        raw = """psql 'postgresql://user:pass@host.neon.tech/db?sslmode=require'"""
        result = pilot._sanitize_database_url(raw)
        self.assertIn("postgresql://", result)
        self.assertIn("host.neon.tech", result)
    
    def test_quoted_value(self):
        """Strip quotes from URL."""
        raw = """DATABASE_URL="postgresql://user:pass@host.neon.tech/db" """
        result = pilot._sanitize_database_url(raw)
        self.assertIn("postgresql://", result)
        self.assertNotIn('"', result)
        self.assertNotIn("'", result)
    
    def test_database_url_preference(self):
        """Prefer DATABASE_URL over DATABASE_URL_UNPOOLED."""
        raw = """DATABASE_URL_UNPOOLED=postgresql://unpooled.neon.tech/db
DATABASE_URL=postgresql://pooled.neon.tech/db"""
        result = pilot._sanitize_database_url(raw)
        self.assertIn("pooled.neon.tech", result)
        self.assertNotIn("unpooled.neon.tech", result)
    
    def test_unpooled_only(self):
        """Use DATABASE_URL_UNPOOLED if it's the only one."""
        raw = """DATABASE_URL_UNPOOLED=postgresql://unpooled.neon.tech/db"""
        result = pilot._sanitize_database_url(raw)
        self.assertIn("unpooled.neon.tech", result)
    
    def test_postgres_normalization(self):
        """Normalize postgres:// to postgresql://."""
        raw = "postgres://user:pass@host.neon.tech/db"
        result = pilot._sanitize_database_url(raw)
        self.assertTrue(result.startswith("postgresql://"))
        self.assertNotIn("postgres://", result)
    
    def test_sslmode_add_neon(self):
        """Add sslmode=require for neon.tech hosts without sslmode."""
        raw = "postgresql://user:pass@host.neon.tech/db"
        result = pilot._sanitize_database_url(raw)
        self.assertIn("sslmode=require", result)
    
    def test_sslmode_not_added_when_present(self):
        """Don't add sslmode if already present."""
        raw = "postgresql://user:pass@host.neon.tech/db?sslmode=disable"
        result = pilot._sanitize_database_url(raw)
        self.assertIn("sslmode=disable", result)
        self.assertEqual(result.count("sslmode="), 1)
    
    def test_sslmode_not_added_non_neon(self):
        """Don't add sslmode for non-neon.tech hosts."""
        raw = "postgresql://user:pass@localhost/db"
        result = pilot._sanitize_database_url(raw)
        self.assertNotIn("sslmode=", result)
    
    def test_empty_input(self):
        """Handle empty input."""
        result = pilot._sanitize_database_url("")
        self.assertEqual(result, "")
    
    def test_no_url_found_fallback(self):
        """Fall back to strip() if no URL pattern found."""
        raw = "  some random text  "
        result = pilot._sanitize_database_url(raw)
        self.assertEqual(result, "some random text")


class DatabaseErrorClassificationTest(unittest.TestCase):
    """Test _classify_db_error function."""
    
    def test_invalid_dsn(self):
        """Classify DSN parsing errors."""
        error = Exception("invalid dsn: missing host")
        self.assertEqual(pilot._classify_db_error(error), 'invalid_dsn')
    
    def test_auth_failed(self):
        """Classify authentication errors."""
        error = Exception("password authentication failed for user")
        self.assertEqual(pilot._classify_db_error(error), 'auth_failed')
    
    def test_timeout(self):
        """Classify timeout errors."""
        error = Exception("connection timeout exceeded")
        self.assertEqual(pilot._classify_db_error(error), 'timeout')
    
    def test_host_not_found(self):
        """Classify host resolution errors."""
        error = Exception("could not translate host name to address")
        self.assertEqual(pilot._classify_db_error(error), 'host_not_found')
    
    def test_ssl_error(self):
        """Classify SSL errors."""
        error = Exception("ssl connection has been closed unexpectedly")
        self.assertEqual(pilot._classify_db_error(error), 'ssl')
    
    def test_other_error(self):
        """Classify unknown errors as 'other'."""
        error = Exception("some unknown database error")
        self.assertEqual(pilot._classify_db_error(error), 'other')


class DatabaseHealthTest(unittest.TestCase):
    """Test check_database_health function."""
    
    def setUp(self):
        # Save original state
        self.original_url = pilot.DATABASE_URL
        self.original_use_db = pilot.USE_DATABASE
        self.original_connection = pilot._db_connection
        self.original_error = pilot._db_connection_error
        self.original_error_kind = pilot._db_error_kind
    
    def tearDown(self):
        # Restore original state
        pilot.DATABASE_URL = self.original_url
        pilot.USE_DATABASE = self.original_use_db
        pilot._db_connection = self.original_connection
        pilot._db_connection_error = self.original_error
        pilot._db_error_kind = self.original_error_kind
    
    def test_no_database_configured(self):
        """Health check when DATABASE_URL is not set."""
        pilot.DATABASE_URL = ""
        pilot.USE_DATABASE = False
        health = pilot.check_database_health()
        self.assertEqual(health["storage"], "file")
        self.assertFalse(health["database_configured"])
        self.assertFalse(health["database_connected"])
        self.assertNotIn("database_error", health)
    
    @patch('pilot_tracking.psycopg2')
    def test_database_connected(self, mock_psycopg2):
        """Health check when database is connected."""
        pilot.DATABASE_URL = "postgresql://user:pass@host/db"
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone = MagicMock(return_value=(1,))
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_psycopg2.connect.return_value = mock_conn
        
        health = pilot.check_database_health()
        self.assertEqual(health["storage"], "database")
        self.assertTrue(health["database_configured"])
        self.assertTrue(health["database_connected"])
        self.assertNotIn("database_error", health)
    
    @patch('pilot_tracking.psycopg2')
    def test_database_connection_failed(self, mock_psycopg2):
        """Health check when database connection fails."""
        pilot.DATABASE_URL = "postgresql://user:pass@host/db"
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        mock_psycopg2.connect.side_effect = Exception("password authentication failed")
        
        health = pilot.check_database_health()
        self.assertEqual(health["storage"], "database")
        self.assertTrue(health["database_configured"])
        self.assertFalse(health["database_connected"])
        self.assertEqual(health["database_error"], "connect_failed")
        self.assertIn("database_error_kind", health)


class LeakPreventionTest(unittest.TestCase):
    """Test that URLs and sensitive data never leak in logs or responses."""
    
    @patch('pilot_tracking.psycopg2')
    def test_no_url_in_error_classification(self, mock_psycopg2):
        """Ensure _classify_db_error never returns URL."""
        url = "postgresql://secret_user:secret_pass@host.neon.tech/db"
        error = Exception(f"Failed to connect to {url}")
        
        classification = pilot._classify_db_error(error)
        self.assertNotIn("secret_user", classification)
        self.assertNotIn("secret_pass", classification)
        self.assertNotIn(url, classification)
        self.assertIn(classification, ['invalid_dsn', 'auth_failed', 'timeout', 
                                       'host_not_found', 'ssl', 'other'])
    
    @patch('pilot_tracking.psycopg2')
    def test_no_url_in_health_response(self, mock_psycopg2):
        """Ensure check_database_health never returns URL."""
        url = "postgresql://secret_user:secret_pass@host.neon.tech/db"
        pilot.DATABASE_URL = url
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        mock_psycopg2.connect.side_effect = Exception("connection failed")
        
        health = pilot.check_database_health()
        health_str = str(health)
        
        self.assertNotIn("secret_user", health_str)
        self.assertNotIn("secret_pass", health_str)
        self.assertNotIn(url, health_str)


if __name__ == "__main__":
    unittest.main()
