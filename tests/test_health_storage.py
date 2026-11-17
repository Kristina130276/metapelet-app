"""Tests for /health storage reporting (no secrets exposed)."""

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import json

import pilot_tracking as pilot
from app import app


class HealthStorageReportingTest(unittest.TestCase):
    """Test /health endpoint reports storage mode without exposing secrets."""
    
    def setUp(self):
        self.client = app.test_client()
        self.tmp = tempfile.mkdtemp()
        # Save original values
        self._old_database_url = os.environ.get("DATABASE_URL")
        self._old_use_database = pilot.USE_DATABASE
        self._old_db_connection = pilot._db_connection
        self._old_data_dir = pilot.DATA_DIR
        # Set up temp directory for file storage
        pilot.DATA_DIR = pilot.Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
    
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        # Restore original values
        if self._old_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self._old_database_url
        pilot.USE_DATABASE = self._old_use_database
        pilot._db_connection = self._old_db_connection
        pilot.DATA_DIR = self._old_data_dir
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
    
    def test_file_fallback_when_no_database_url(self):
        """When DATABASE_URL is not set, /health reports file storage."""
        os.environ.pop("DATABASE_URL", None)
        pilot.DATABASE_URL = ""
        pilot.USE_DATABASE = False
        pilot._db_connection = None
        
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["storage"], "file")
        self.assertEqual(data["database_configured"], False)
        self.assertEqual(data["database_connected"], False)
        # No error field when database is not configured
        self.assertNotIn("database_error", data)
    
    @patch("pilot_tracking.psycopg2")
    def test_database_connected_success(self, mock_psycopg2):
        """When database connects successfully, /health reports database storage."""
        os.environ["DATABASE_URL"] = "postgresql://user:pass@host:5432/db"
        pilot.DATABASE_URL = pilot._sanitize_database_url(os.environ["DATABASE_URL"])
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        # Mock successful connection
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone = MagicMock(return_value=(1,))
        mock_conn.cursor = MagicMock(return_value=mock_cursor)
        mock_psycopg2.connect = MagicMock(return_value=mock_conn)
        
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["storage"], "database")
        self.assertEqual(data["database_configured"], True)
        self.assertEqual(data["database_connected"], True)
        self.assertNotIn("database_error", data)
    
    @patch("pilot_tracking.psycopg2")
    def test_database_connect_failed(self, mock_psycopg2):
        """When database connection fails, /health reports connect_failed."""
        os.environ["DATABASE_URL"] = "postgresql://user:pass@host:5432/db"
        pilot.DATABASE_URL = pilot._sanitize_database_url(os.environ["DATABASE_URL"])
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        # Mock connection failure
        mock_psycopg2.connect = MagicMock(side_effect=Exception("connection refused"))
        
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["storage"], "database")
        self.assertEqual(data["database_configured"], True)
        self.assertEqual(data["database_connected"], False)
        self.assertEqual(data["database_error"], "connect_failed")
    
    @patch("pilot_tracking.psycopg2")
    def test_database_closed_connection_reconnect(self, mock_psycopg2):
        """When connection is closed, /health reconnects and succeeds."""
        os.environ["DATABASE_URL"] = "postgresql://user:pass@host:5432/db"
        pilot.DATABASE_URL = pilot._sanitize_database_url(os.environ["DATABASE_URL"])
        pilot.USE_DATABASE = True
        
        # Set up a dead connection
        mock_dead_conn = MagicMock()
        mock_dead_cursor = MagicMock()
        mock_dead_cursor.__enter__ = MagicMock(return_value=mock_dead_cursor)
        mock_dead_cursor.__exit__ = MagicMock(return_value=False)
        mock_dead_cursor.execute = MagicMock(side_effect=Exception("connection closed"))
        mock_dead_conn.cursor = MagicMock(return_value=mock_dead_cursor)
        mock_dead_conn.close = MagicMock()
        pilot._db_connection = mock_dead_conn
        
        # Mock successful reconnection
        mock_new_conn = MagicMock()
        mock_new_cursor = MagicMock()
        mock_new_cursor.__enter__ = MagicMock(return_value=mock_new_cursor)
        mock_new_cursor.__exit__ = MagicMock(return_value=False)
        mock_new_cursor.fetchone = MagicMock(return_value=(1,))
        mock_new_conn.cursor = MagicMock(return_value=mock_new_cursor)
        mock_psycopg2.connect = MagicMock(return_value=mock_new_conn)
        
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database_connected"], True)
        self.assertNotIn("database_error", data)
        # Verify old connection was closed
        mock_dead_conn.close.assert_called_once()
    
    def test_sanitize_database_url_strips_quotes(self):
        """DATABASE_URL sanitization removes surrounding quotes."""
        # Single quotes
        result = pilot._sanitize_database_url("'postgresql://user:pass@host/db'")
        self.assertEqual(result, "postgresql://user:pass@host/db")
        
        # Double quotes
        result = pilot._sanitize_database_url('"postgresql://user:pass@host/db"')
        self.assertEqual(result, "postgresql://user:pass@host/db")
        
        # Whitespace
        result = pilot._sanitize_database_url("  postgresql://user:pass@host/db  ")
        self.assertEqual(result, "postgresql://user:pass@host/db")
        
        # Combined
        result = pilot._sanitize_database_url('  "postgresql://user:pass@host/db"  ')
        self.assertEqual(result, "postgresql://user:pass@host/db")
    
    def test_sanitize_database_url_strips_prefix(self):
        """DATABASE_URL sanitization removes 'DATABASE_URL=' prefix."""
        result = pilot._sanitize_database_url("DATABASE_URL=postgresql://user:pass@host/db")
        self.assertEqual(result, "postgresql://user:pass@host/db")
        
        # Case insensitive
        result = pilot._sanitize_database_url("database_url=postgresql://user:pass@host/db")
        self.assertEqual(result, "postgresql://user:pass@host/db")
        
        # With quotes
        result = pilot._sanitize_database_url('DATABASE_URL="postgresql://user:pass@host/db"')
        self.assertEqual(result, "postgresql://user:pass@host/db")
    
    @patch("pilot_tracking.psycopg2")
    def test_health_never_exposes_database_url(self, mock_psycopg2):
        """Verify /health output never contains DSN, password, or host."""
        secret_password = "super_secret_pass_12345"
        secret_host = "secret-db-host.example.com"
        database_url = f"postgresql://user:{secret_password}@{secret_host}:5432/db"
        
        os.environ["DATABASE_URL"] = database_url
        pilot.DATABASE_URL = pilot._sanitize_database_url(database_url)
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        # Mock connection failure to test error case
        mock_psycopg2.connect = MagicMock(side_effect=Exception("connection refused"))
        
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        
        # Get raw response text
        response_text = resp.get_data(as_text=True)
        
        # Verify no secrets in response
        self.assertNotIn(secret_password, response_text,
                        "Password must not appear in /health response")
        self.assertNotIn(secret_host, response_text,
                        "Database host must not appear in /health response")
        self.assertNotIn(database_url, response_text,
                        "Full DATABASE_URL must not appear in /health response")
        self.assertNotIn("postgresql://", response_text,
                        "DSN scheme must not appear in /health response")
        
        # Verify only allowed fields are present
        data = resp.get_json()
        allowed_keys = {"status", "storage", "database_configured", 
                       "database_connected", "database_error"}
        self.assertTrue(set(data.keys()).issubset(allowed_keys),
                       f"Unexpected keys in /health response: {set(data.keys()) - allowed_keys}")
    
    @patch("pilot_tracking.psycopg2")
    def test_health_never_exposes_exception_messages(self, mock_psycopg2):
        """Verify /health output never contains exception messages."""
        database_url = "postgresql://user:pass@host:5432/db"
        
        os.environ["DATABASE_URL"] = database_url
        pilot.DATABASE_URL = pilot._sanitize_database_url(database_url)
        pilot.USE_DATABASE = True
        pilot._db_connection = None
        
        # Mock connection failure with detailed error message
        error_message = "FATAL: password authentication failed for user 'user' host='1.2.3.4'"
        mock_psycopg2.connect = MagicMock(side_effect=Exception(error_message))
        
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        
        response_text = resp.get_data(as_text=True)
        
        # Verify exception message details don't leak
        self.assertNotIn("FATAL", response_text)
        self.assertNotIn("authentication failed", response_text)
        self.assertNotIn("1.2.3.4", response_text)
        
        # Only the error type should be present
        data = resp.get_json()
        if "database_error" in data:
            self.assertIn(data["database_error"], ["connect_failed", "init_failed"])


if __name__ == "__main__":
    unittest.main()
