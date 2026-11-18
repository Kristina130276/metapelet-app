"""Tests for stale database connection handling and recovery."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock, call
from pathlib import Path

import pilot_tracking as pilot

# Check if psycopg2 is available for mocking
try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


class StaleConnectionRecoveryTest(unittest.TestCase):
    """Test automatic reconnect and retry on stale database connections."""
    
    def setUp(self):
        """Set up test environment."""
        self.tmp = tempfile.mkdtemp()
        self.original_data_dir = pilot.DATA_DIR
        self.original_use_db = pilot.USE_DATABASE
        self.original_db_url = pilot.DATABASE_URL
        self.original_connection = pilot._db_connection
        
        # Set up temp directory for file fallback
        pilot.DATA_DIR = Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.tmp, ignore_errors=True)
        pilot.DATA_DIR = self.original_data_dir
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        pilot.USE_DATABASE = self.original_use_db
        pilot.DATABASE_URL = self.original_db_url
        pilot._db_connection = self.original_connection
    
    @unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
    @patch('psycopg2.connect')
    def test_stale_connection_detected_and_reconnected(self, mock_connect):
        """Test that a stale connection is detected and reconnection succeeds."""
        pilot.DATABASE_URL = "postgresql://user:pass@host.neon.tech/db"
        pilot.USE_DATABASE = True
        
        # Create a stale connection that raises "connection closed" on execute
        stale_conn = MagicMock()
        stale_cursor = MagicMock()
        stale_cursor.__enter__ = MagicMock(return_value=stale_cursor)
        stale_cursor.__exit__ = MagicMock(return_value=False)
        stale_cursor.execute.side_effect = Exception("server closed the connection unexpectedly")
        stale_conn.cursor.return_value = stale_cursor
        stale_conn.close = MagicMock()
        
        # Create a fresh connection that works
        fresh_conn = MagicMock()
        fresh_cursor = MagicMock()
        fresh_cursor.__enter__ = MagicMock(return_value=fresh_cursor)
        fresh_cursor.__exit__ = MagicMock(return_value=False)
        fresh_cursor.execute = MagicMock()
        fresh_conn.cursor.return_value = fresh_cursor
        fresh_conn.autocommit = True
        
        # First call returns stale connection, second call returns fresh connection
        pilot._db_connection = stale_conn
        mock_connect.return_value = fresh_conn
        
        # Try to record feedback - should detect stale, reconnect, and succeed
        ok, err = pilot.record_feedback(
            session_id="test_stale_123",
            comfortable="yes",
            liked="yes",
            continue_pilot="yes"
        )
        
        # Should succeed after reconnect
        self.assertTrue(ok)
        self.assertEqual(err, "")
        
        # Verify old connection was closed
        stale_conn.close.assert_called_once()
        
        # Verify reconnection happened
        mock_connect.assert_called_once()
        
        # Verify data was inserted with fresh connection
        self.assertTrue(fresh_cursor.execute.called)
    
    @unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
    @patch('psycopg2.connect')
    def test_stale_connection_event_recording(self, mock_connect):
        """Test that event recording handles stale connections."""
        pilot.DATABASE_URL = "postgresql://user:pass@host.neon.tech/db"
        pilot.USE_DATABASE = True
        
        # Stale connection
        stale_conn = MagicMock()
        stale_cursor = MagicMock()
        stale_cursor.__enter__ = MagicMock(return_value=stale_cursor)
        stale_cursor.__exit__ = MagicMock(return_value=False)
        stale_cursor.execute.side_effect = Exception("connection terminated")
        stale_conn.cursor.return_value = stale_cursor
        stale_conn.close = MagicMock()
        
        # Fresh connection
        fresh_conn = MagicMock()
        fresh_cursor = MagicMock()
        fresh_cursor.__enter__ = MagicMock(return_value=fresh_cursor)
        fresh_cursor.__exit__ = MagicMock(return_value=False)
        fresh_cursor.execute = MagicMock()
        fresh_conn.cursor.return_value = fresh_cursor
        fresh_conn.autocommit = True
        
        pilot._db_connection = stale_conn
        mock_connect.return_value = fresh_conn
        
        # Record event
        ok, err = pilot.record_event(
            session_id="test_event_123",
            event="site_visit"
        )
        
        # Should succeed after reconnect
        self.assertTrue(ok)
        self.assertEqual(err, "")
        
        # Verify reconnection happened
        mock_connect.assert_called_once()
        self.assertTrue(fresh_cursor.execute.called)
    
    @unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
    @patch('psycopg2.connect')
    def test_fallback_to_file_when_reconnect_fails(self, mock_connect):
        """Test that file fallback works when database is unavailable."""
        pilot.DATABASE_URL = "postgresql://user:pass@host.neon.tech/db"
        pilot.USE_DATABASE = True
        
        # Stale connection
        stale_conn = MagicMock()
        stale_cursor = MagicMock()
        stale_cursor.__enter__ = MagicMock(return_value=stale_cursor)
        stale_cursor.__exit__ = MagicMock(return_value=False)
        stale_cursor.execute.side_effect = Exception("connection lost")
        stale_conn.cursor.return_value = stale_cursor
        stale_conn.close = MagicMock()
        
        pilot._db_connection = stale_conn
        # Reconnection also fails
        mock_connect.side_effect = Exception("connection refused")
        
        # Record feedback - should fall back to file
        ok, err = pilot.record_feedback(
            session_id="test_fallback_123",
            comfortable="yes",
            liked="yes",
            continue_pilot="yes",
            issues="Test feedback"
        )
        
        # Should succeed via file fallback
        self.assertTrue(ok)
        self.assertEqual(err, "")
        
        # Verify data was written to file
        feedback_file = pilot.FEEDBACK_PATH
        self.assertTrue(feedback_file.exists())
        
        content = feedback_file.read_text()
        self.assertIn("test_fallback_123", content)
        self.assertIn("Test feedback", content)
    
    @unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
    @patch('psycopg2.connect')
    def test_non_stale_errors_dont_retry(self, mock_connect):
        """Test that non-connection errors don't trigger retry."""
        pilot.DATABASE_URL = "postgresql://user:pass@host.neon.tech/db"
        pilot.USE_DATABASE = True
        
        # Connection with constraint violation (not a stale connection error)
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.execute.side_effect = Exception("unique constraint violation")
        conn.cursor.return_value = cursor
        
        pilot._db_connection = conn
        
        # Record feedback - should fail and fall back to file
        ok, err = pilot.record_feedback(
            session_id="test_non_stale_123",
            comfortable="yes",
            liked="yes",
            continue_pilot="yes"
        )
        
        # Should succeed via file fallback (since DB failed)
        self.assertTrue(ok)
        
        # Verify reconnect was NOT attempted (connect should not be called)
        mock_connect.assert_not_called()
    
    @unittest.skipUnless(PSYCOPG2_AVAILABLE, "psycopg2 not installed")
    @patch('psycopg2.connect')
    def test_multiple_stale_keywords_detected(self, mock_connect):
        """Test that various stale connection error messages are recognized."""
        pilot.DATABASE_URL = "postgresql://user:pass@host.neon.tech/db"
        pilot.USE_DATABASE = True
        
        stale_error_messages = [
            "connection closed",
            "server closed the connection",
            "connection terminated",
            "connection broken",
            "connection lost",
            "connection reset",
            "connection timeout",
            "no connection to the server",
        ]
        
        for error_msg in stale_error_messages:
            # Reset state for each test
            pilot._db_connection = None
            
            # Stale connection
            stale_conn = MagicMock()
            stale_cursor = MagicMock()
            stale_cursor.__enter__ = MagicMock(return_value=stale_cursor)
            stale_cursor.__exit__ = MagicMock(return_value=False)
            stale_cursor.execute.side_effect = Exception(error_msg)
            stale_conn.cursor.return_value = stale_cursor
            stale_conn.close = MagicMock()
            
            # Fresh connection
            fresh_conn = MagicMock()
            fresh_cursor = MagicMock()
            fresh_cursor.__enter__ = MagicMock(return_value=fresh_cursor)
            fresh_cursor.__exit__ = MagicMock(return_value=False)
            fresh_cursor.execute = MagicMock()
            fresh_conn.cursor.return_value = fresh_cursor
            fresh_conn.autocommit = True
            
            pilot._db_connection = stale_conn
            mock_connect.return_value = fresh_conn
            mock_connect.reset_mock()
            
            # Record event
            ok, err = pilot.record_event(
                session_id=f"test_{error_msg[:10]}",
                event="site_visit"
            )
            
            # Should succeed after reconnect
            self.assertTrue(ok, f"Failed for error: {error_msg}")
            
            # Verify reconnection happened
            mock_connect.assert_called_once()


class TestDataFilteringTest(unittest.TestCase):
    """Test that test data is excluded from reports and exports."""
    
    def setUp(self):
        """Set up test environment."""
        self.tmp = tempfile.mkdtemp()
        self.original_data_dir = pilot.DATA_DIR
        self.original_use_db = pilot.USE_DATABASE
        
        pilot.DATA_DIR = Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        pilot.USE_DATABASE = False
    
    def tearDown(self):
        """Clean up test environment."""
        shutil.rmtree(self.tmp, ignore_errors=True)
        pilot.DATA_DIR = self.original_data_dir
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        pilot.USE_DATABASE = self.original_use_db
    
    def test_test_sessions_excluded_from_summary(self):
        """Test that sessions starting with ps_TEST_ are excluded from summary."""
        # Record normal session
        pilot.record_event("ps_real_user_123", "site_visit")
        pilot.record_event("ps_real_user_123", "conversation")
        pilot.record_feedback(
            session_id="ps_real_user_123",
            comfortable="yes",
            liked="yes",
            continue_pilot="yes"
        )
        
        # Record test session (should be excluded)
        pilot.record_event("ps_TEST_AYA_DIAG", "site_visit")
        pilot.record_event("ps_TEST_AYA_DIAG", "conversation")
        pilot.record_feedback(
            session_id="ps_TEST_AYA_DIAG",
            comfortable="yes",
            liked="no",
            continue_pilot="no",
            issues="ТЕСТ АЯ, не учитывать"
        )
        
        # Record another test session
        pilot.record_event("ps_TEST_another", "site_visit")
        
        # Build summary
        summary = pilot.build_summary()
        
        # Only the real session should be in the summary
        self.assertEqual(summary["family_count"], 1)
        self.assertEqual(summary["totals"]["events"], 2)
        self.assertEqual(summary["totals"]["feedback"], 1)
        
        # Verify the real session is present
        session_ids = []
        for family in summary["families"]:
            for feedback in family["feedback"]:
                session_ids.append(feedback["session_id"])
        
        self.assertIn("ps_real_user_123", session_ids)
        self.assertNotIn("ps_TEST_AYA_DIAG", session_ids)
        self.assertNotIn("ps_TEST_another", session_ids)
    
    def test_test_sessions_excluded_from_export(self):
        """Test that test sessions are excluded from CSV export."""
        # Record normal feedback
        pilot.record_feedback(
            session_id="ps_real_user_456",
            comfortable="yes",
            liked="yes",
            continue_pilot="yes",
            issues="Real feedback"
        )
        
        # Record test feedback (should be excluded)
        pilot.record_feedback(
            session_id="ps_TEST_DIAGNOSTIC",
            comfortable="yes",
            liked="yes",
            continue_pilot="yes",
            issues="Test diagnostic feedback"
        )
        
        # Get export data
        export_data = pilot.get_all_feedback_for_export()
        
        # Only real feedback should be exported
        self.assertEqual(len(export_data), 1)
        self.assertEqual(export_data[0]["session_id"], "ps_real_user_456")
        
        # Verify test data is not in export
        test_sessions = [fb for fb in export_data if fb["session_id"].startswith("ps_TEST_")]
        self.assertEqual(len(test_sessions), 0)
    
    def test_multiple_test_prefixes_filtered(self):
        """Test that various ps_TEST_ prefixes are all filtered."""
        test_sessions = [
            "ps_TEST_",
            "ps_TEST_AYA_DIAG",
            "ps_TEST_something",
            "ps_TEST_1234",
        ]
        
        for session_id in test_sessions:
            pilot.record_event(session_id, "site_visit")
        
        # Record one real session
        pilot.record_event("ps_real", "site_visit")
        
        # Build summary
        summary = pilot.build_summary()
        
        # Should only have 1 family (the real session)
        self.assertEqual(summary["family_count"], 1)
        self.assertEqual(summary["totals"]["events"], 1)


class ConnectionHealthCheckTest(unittest.TestCase):
    """Test _is_connection_alive helper function."""
    
    def test_connection_alive(self):
        """Test that a healthy connection is detected as alive."""
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.execute = MagicMock()
        cursor.fetchone = MagicMock(return_value=(1,))
        conn.cursor.return_value = cursor
        
        self.assertTrue(pilot._is_connection_alive(conn))
    
    def test_connection_dead(self):
        """Test that a dead connection is detected as not alive."""
        conn = MagicMock()
        cursor = MagicMock()
        cursor.__enter__ = MagicMock(return_value=cursor)
        cursor.__exit__ = MagicMock(return_value=False)
        cursor.execute.side_effect = Exception("connection closed")
        conn.cursor.return_value = cursor
        
        self.assertFalse(pilot._is_connection_alive(conn))
    
    def test_none_connection_not_alive(self):
        """Test that None connection is detected as not alive."""
        self.assertFalse(pilot._is_connection_alive(None))


if __name__ == "__main__":
    unittest.main()
