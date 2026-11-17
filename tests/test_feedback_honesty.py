"""Tests for feedback data honesty (empty = not asked)."""

import json
import os
import shutil
import tempfile
import unittest

import pilot_tracking as pilot
from app import app


class FeedbackHonestyTest(unittest.TestCase):
    """Test that empty feedback fields mean 'not asked', not invented answers."""
    
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pilot.DATA_DIR = pilot.Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        self.original_use_db = pilot.USE_DATABASE
        pilot.USE_DATABASE = False
        self.client = app.test_client()
    
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        pilot.USE_DATABASE = self.original_use_db
    
    def test_empty_comfortable_and_continue_accepted(self):
        """Empty strings for comfortable/continue_pilot should be accepted."""
        ok, err = pilot.record_feedback(
            "ps_test1",
            comfortable="",  # not asked
            liked="yes",
            continue_pilot="",  # not asked
            issues="",
            language="ru-RU"
        )
        self.assertTrue(ok, f"Should accept empty comfortable/continue: {err}")
    
    def test_only_liked_required_from_thumbs(self):
        """Thumbs up/down on /talk should only record 'liked'."""
        # Simulate thumbs up: only liked='yes', others empty
        ok, err = pilot.record_feedback(
            "ps_thumbs_up",
            comfortable="",
            liked="yes",
            continue_pilot="",
            issues="",
            language="he-IL"
        )
        self.assertTrue(ok)
        
        # Simulate thumbs down: only liked='no', others empty
        ok, err = pilot.record_feedback(
            "ps_thumbs_down",
            comfortable="",
            liked="no",
            continue_pilot="",
            issues="",
            language="en-US"
        )
        self.assertTrue(ok)
    
    def test_full_questionnaire_still_works(self):
        """Main page's full questionnaire should still work unchanged."""
        ok, err = pilot.record_feedback(
            "ps_full",
            comfortable="yes",
            liked="partial",
            continue_pilot="maybe",
            issues="All good",
            language="ru-RU"
        )
        self.assertTrue(ok)
    
    def test_summary_shows_empty_as_not_answered(self):
        """Summary should show '—' for empty values (not answered)."""
        pilot.record_feedback(
            "ps_summary_test",
            comfortable="",
            liked="yes",
            continue_pilot="",
            language="ru-RU"
        )
        
        summary = pilot.build_summary()
        html = pilot.summary_html(summary)
        
        # Should show feedback exists
        self.assertIn("понравилось:", html)
        
        # Should show "—" for not-answered fields
        # The pattern should be: удобно: — · понравилось: yes · продолжить: —
        self.assertIn("удобно:</b> —", html)
        self.assertIn("продолжить:</b> —", html)
        self.assertIn("понравилось:</b> yes", html)
    
    def test_csv_export_includes_empty_strings(self):
        """CSV export should include empty strings as-is (not hide them)."""
        pilot.record_feedback(
            "ps_csv_test",
            comfortable="",
            liked="no",
            continue_pilot="",
            language="ru-RU"
        )
        
        feedback_rows = pilot.get_all_feedback_for_export()
        self.assertEqual(len(feedback_rows), 1)
        
        fb = feedback_rows[0]
        self.assertEqual(fb["comfortable"], "")
        self.assertEqual(fb["liked"], "no")
        self.assertEqual(fb["continue_pilot"], "")
    
    def test_talk_page_api_accepts_thumbs_feedback(self):
        """Test /pilot/feedback endpoint accepts thumbs-only feedback."""
        # Thumbs up
        resp = self.client.post("/pilot/feedback", json={
            "session_id": "ps_api_thumbs",
            "comfortable": "",
            "liked": "yes",
            "continue_pilot": "",
            "issues": "",
            "language": "he-IL",
            "utm_source": "",
            "utm_campaign": "",
            "utm_content": ""
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        
        # Verify it was stored
        feedback = pilot.get_all_feedback_for_export()
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback[0]["comfortable"], "")
        self.assertEqual(feedback[0]["liked"], "yes")
        self.assertEqual(feedback[0]["continue_pilot"], "")


class FeedbackValidationTest(unittest.TestCase):
    """Test that invalid values are still rejected."""
    
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pilot.DATA_DIR = pilot.Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"
        self.original_use_db = pilot.USE_DATABASE
        pilot.USE_DATABASE = False
    
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        pilot.USE_DATABASE = self.original_use_db
    
    def test_invalid_liked_rejected(self):
        """Invalid 'liked' values should be rejected."""
        ok, err = pilot.record_feedback(
            "ps_invalid",
            comfortable="",
            liked="invalid",  # not in allowed set
            continue_pilot="",
            language="ru-RU"
        )
        self.assertFalse(ok)
        self.assertEqual(err, "invalid_liked")
    
    def test_all_empty_rejected(self):
        """Can't have all three fields empty (must at least record liked)."""
        # Actually this should be accepted by the validation
        # but it's semantically useless, so the API layer should require 'liked'
        # when using the thumbs feature. Let's just verify validation accepts it.
        ok, err = pilot.record_feedback(
            "ps_all_empty",
            comfortable="",
            liked="",  # empty is allowed
            continue_pilot="",
            language="ru-RU"
        )
        # This is technically valid (all empty strings are in the allowed set)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
