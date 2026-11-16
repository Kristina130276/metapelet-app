"""Tests for pilot tracking with database storage."""

import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pilot_tracking as pilot


class PilotValidationTest(unittest.TestCase):
    """Test that validation is enforced regardless of storage backend."""
    
    def test_invalid_event_rejected(self):
        """Test that invalid event names are rejected."""
        ok, err = pilot.record_event(
            session_id="test",
            event="invalid_event_name"
        )
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_event")
    
    def test_invalid_session_too_long(self):
        """Test that session IDs that are too long are rejected."""
        ok, err = pilot.record_event(
            session_id="x" * 100,
            event="site_visit"
        )
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_session")
    
    def test_invalid_feedback_comfortable(self):
        """Test that invalid feedback values are rejected."""
        ok, err = pilot.record_feedback(
            session_id="test",
            comfortable="invalid",
            liked="yes",
            continue_pilot="yes"
        )
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_comfortable")
    
    def test_invalid_feedback_liked(self):
        """Test that invalid 'liked' values are rejected."""
        ok, err = pilot.record_feedback(
            session_id="test",
            comfortable="yes",
            liked="invalid",
            continue_pilot="yes"
        )
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_liked")
    
    def test_invalid_feedback_continue(self):
        """Test that invalid 'continue_pilot' values are rejected."""
        ok, err = pilot.record_feedback(
            session_id="test",
            comfortable="yes",
            liked="yes",
            continue_pilot="invalid"
        )
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_continue")


class PilotFileFallbackTest(unittest.TestCase):
    """Test that file fallback still works."""
    
    def setUp(self):
        """Set up temporary data directory."""
        self.temp_dir = tempfile.mkdtemp()
        self.original_data_dir = os.environ.get("PILOT_DATA_DIR")
        os.environ["PILOT_DATA_DIR"] = self.temp_dir
        
        # Force reload of pilot_tracking to pick up new DATA_DIR
        import importlib
        import pilot_tracking
        importlib.reload(pilot_tracking)
    
    def tearDown(self):
        """Clean up."""
        if self.original_data_dir:
            os.environ["PILOT_DATA_DIR"] = self.original_data_dir
        else:
            os.environ.pop("PILOT_DATA_DIR", None)
        
        # Clean up temp dir
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
    
    @patch('pilot_tracking.USE_DATABASE', False)
    def test_file_storage_still_works(self):
        """Test that file storage still works when DATABASE_URL is not set."""
        import pilot_tracking as pilot
        
        # Record feedback to file
        ok, err = pilot.record_feedback(
            session_id="file_test_123",
            comfortable="yes",
            liked="partial",
            continue_pilot="maybe",
            issues="Some feedback",
            language="en-US"
        )
        
        self.assertTrue(ok)
        self.assertEqual(err, "")
        
        # Verify file was created
        feedback_file = Path(self.temp_dir) / "pilot_feedback.jsonl"
        self.assertTrue(feedback_file.exists())
        
        # Verify content
        content = feedback_file.read_text()
        self.assertIn("file_test_123", content)
        self.assertIn("Some feedback", content)
        self.assertIn("en-US", content)


class AdminEndpointTest(unittest.TestCase):
    """Test admin CSV export endpoint."""
    
    def setUp(self):
        """Set up Flask test client."""
        # Set admin token for tests
        os.environ["ADMIN_TOKEN"] = "test_admin_token_123"
        
        # Import app after setting env var
        from app import app
        self.app = app
        self.client = app.test_client()
    
    def tearDown(self):
        """Clean up."""
        os.environ.pop("ADMIN_TOKEN", None)
    
    def test_admin_endpoint_requires_token(self):
        """Test that admin endpoint requires valid token."""
        # No token
        response = self.client.get('/admin/feedback.csv')
        self.assertEqual(response.status_code, 403)
        
        # Wrong token
        response = self.client.get('/admin/feedback.csv?token=wrong_token')
        self.assertEqual(response.status_code, 403)
    
    def test_admin_endpoint_with_valid_token(self):
        """Test that admin endpoint works with valid token."""
        response = self.client.get('/admin/feedback.csv?token=test_admin_token_123')
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response.content_type)
    
    def test_admin_endpoint_disabled_without_env_var(self):
        """Test that endpoint is disabled when ADMIN_TOKEN is not set."""
        # Remove env var
        token = os.environ.pop("ADMIN_TOKEN", None)
        
        try:
            response = self.client.get('/admin/feedback.csv?token=any_token')
            self.assertEqual(response.status_code, 404)
            self.assertIn(b'disabled', response.data.lower())
        finally:
            # Restore env var
            if token:
                os.environ["ADMIN_TOKEN"] = token


if __name__ == "__main__":
    unittest.main()
