"""Tests for monthly TTS budget enforcement."""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone

import tts_budget
from app import app


class TTSBudgetModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        tts_budget.DATA_DIR = tts_budget.Path(self.tmp)
        tts_budget.TTS_BUDGET_PATH = tts_budget.DATA_DIR / "tts_monthly_budget.json"
        self.original_use_db = tts_budget.USE_DATABASE
        tts_budget.USE_DATABASE = False
        tts_budget._file_budget = {}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        tts_budget.USE_DATABASE = self.original_use_db
        tts_budget._file_budget = {}

    def test_voice_to_tier_mapping(self):
        """Test that all voice names in presets map to correct tiers."""
        voices_and_tiers = [
            ("ru-RU-Chirp3-HD-Kore", "chirp3hd"),
            ("ru-RU-Chirp3-HD-Aoede", "chirp3hd"),
            ("ru-RU-Wavenet-A", "wavenet_standard"),
            ("ru-RU-Standard-A", "wavenet_standard"),
            ("he-IL-Wavenet-A", "wavenet_standard"),
            ("he-IL-Standard-A", "wavenet_standard"),
            ("en-US-Neural2-F", "neural2"),
            ("en-US-Wavenet-F", "wavenet_standard"),
            ("en-US-Standard-C", "wavenet_standard"),
        ]
        
        for voice, expected_tier in voices_and_tiers:
            with self.subTest(voice=voice):
                self.assertEqual(tts_budget._voice_to_tier(voice), expected_tier)

    def test_check_and_reserve_under_cap(self):
        """Test that voices under cap are allowed."""
        allowed, reason = tts_budget.check_and_reserve("ru-RU-Wavenet-A", 1000)
        self.assertTrue(allowed)
        self.assertEqual(reason, "")

    def test_check_and_reserve_exceeds_cap(self):
        """Test that voices exceeding cap are blocked."""
        month = tts_budget._current_month()
        tts_budget._file_budget[month] = {
            "chirp3hd": 0,
            "neural2": 0,
            "wavenet_standard": tts_budget.TTS_MONTHLY_CAP_WAVENET_STANDARD - 100,
        }
        
        allowed, reason = tts_budget.check_and_reserve("ru-RU-Wavenet-A", 200)
        self.assertFalse(allowed)
        self.assertEqual(reason, "monthly_cap")

    def test_record_success_increments_counter(self):
        """Test that successful calls increment the counter."""
        month = tts_budget._current_month()
        tts_budget.record_success("ru-RU-Wavenet-A", 500)
        
        status = tts_budget.get_status()
        self.assertEqual(status["month"], month)
        self.assertEqual(status["tiers"]["wavenet_standard"]["used"], 500)

    def test_counter_persists_via_file(self):
        """Test that counter persists in file fallback mode."""
        tts_budget.record_success("ru-RU-Chirp3-HD-Kore", 1000)
        tts_budget.record_success("en-US-Neural2-F", 2000)
        tts_budget.record_success("ru-RU-Wavenet-A", 3000)
        
        # Clear in-memory cache
        tts_budget._file_budget = {}
        
        # Load from file
        status = tts_budget.get_status()
        # Note: chirp3hd not recorded in file fallback (degraded mode), so only neural2 and wavenet
        self.assertEqual(status["tiers"]["neural2"]["used"], 2000)
        self.assertEqual(status["tiers"]["wavenet_standard"]["used"], 3000)

    def test_degraded_mode_skips_premium_tiers(self):
        """Test that file fallback (degraded mode) skips chirp3hd and neural2."""
        tts_budget.USE_DATABASE = False
        
        # Premium tiers should be blocked in degraded mode
        allowed_chirp, reason_chirp = tts_budget.check_and_reserve("ru-RU-Chirp3-HD-Kore", 100)
        self.assertFalse(allowed_chirp)
        self.assertEqual(reason_chirp, "degraded_mode")
        
        allowed_neural, reason_neural = tts_budget.check_and_reserve("en-US-Neural2-F", 100)
        self.assertFalse(allowed_neural)
        self.assertEqual(reason_neural, "degraded_mode")
        
        # Standard tier should work
        allowed_standard, reason_standard = tts_budget.check_and_reserve("ru-RU-Wavenet-A", 100)
        self.assertTrue(allowed_standard)
        self.assertEqual(reason_standard, "")

    def test_chars_counted_only_on_success(self):
        """Test that chars are only counted after record_success call."""
        month = tts_budget._current_month()
        
        # Check and reserve doesn't increment
        tts_budget.check_and_reserve("ru-RU-Wavenet-A", 1000)
        status = tts_budget.get_status()
        self.assertEqual(status["tiers"]["wavenet_standard"]["used"], 0)
        
        # Only record_success increments
        tts_budget.record_success("ru-RU-Wavenet-A", 1000)
        status = tts_budget.get_status()
        self.assertEqual(status["tiers"]["wavenet_standard"]["used"], 1000)

    def test_month_rollover_resets(self):
        """Test that new month starts with zero usage."""
        old_month = "2026-10"
        current_month = tts_budget._current_month()
        
        # Set usage for old month
        tts_budget._file_budget[old_month] = {
            "chirp3hd": 100000,
            "neural2": 100000,
            "wavenet_standard": 100000,
        }
        tts_budget._save_file_budget(tts_budget._file_budget)
        
        # Current month should show zero
        status = tts_budget.get_status()
        self.assertEqual(status["month"], current_month)
        self.assertEqual(status["tiers"]["chirp3hd"]["used"], 0)
        self.assertEqual(status["tiers"]["neural2"]["used"], 0)
        self.assertEqual(status["tiers"]["wavenet_standard"]["used"], 0)

    def test_budget_status_shape(self):
        """Test that get_status returns correct structure."""
        status = tts_budget.get_status()
        
        self.assertIn("month", status)
        self.assertIn("tiers", status)
        
        self.assertIn("chirp3hd", status["tiers"])
        self.assertIn("neural2", status["tiers"])
        self.assertIn("wavenet_standard", status["tiers"])
        
        for tier_name, tier_data in status["tiers"].items():
            with self.subTest(tier=tier_name):
                self.assertIn("used", tier_data)
                self.assertIn("cap", tier_data)
                self.assertIsInstance(tier_data["used"], int)
                self.assertIsInstance(tier_data["cap"], int)


class TTSEndpointIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.tmp = tempfile.mkdtemp()
        tts_budget.DATA_DIR = tts_budget.Path(self.tmp)
        tts_budget.TTS_BUDGET_PATH = tts_budget.DATA_DIR / "tts_monthly_budget.json"
        self.original_use_db = tts_budget.USE_DATABASE
        tts_budget.USE_DATABASE = False
        tts_budget._file_budget = {}
        
        # Mock Google API key
        os.environ["GOOGLE_API_KEY"] = "test_key"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        tts_budget.USE_DATABASE = self.original_use_db
        tts_budget._file_budget = {}
        if "GOOGLE_API_KEY" in os.environ:
            del os.environ["GOOGLE_API_KEY"]

    @patch('budget_guard.try_consume_tts')
    @patch('requests.post')
    def test_exhausted_chirp_falls_back_to_wavenet(self, mock_post, mock_budget):
        """Test that when Chirp tier is exhausted, fallback to Wavenet works."""
        mock_budget.return_value = (True, None)
        
        # Set Chirp3-HD to near cap
        month = tts_budget._current_month()
        tts_budget._file_budget[month] = {
            "chirp3hd": tts_budget.TTS_MONTHLY_CAP_CHIRP3HD - 10,
            "neural2": 0,
            "wavenet_standard": 0,
        }
        
        # Mock successful Google response for Wavenet (not Chirp)
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {"audioContent": "ZmFrZWF1ZGlv"}  # base64 "fakeaudio"
        mock_post.return_value = mock_response
        
        # Allow text in budget_guard
        import budget_guard as budget
        budget.allow_tts_for("Test text")
        
        response = self.app.post('/tts', json={
            'text': 'Test text for long message that exceeds chirp cap',
            'language': 'ru-RU'
        })
        
        self.assertEqual(response.status_code, 200)
        # Verify that Google was called (not blocked completely)
        self.assertTrue(mock_post.called)
        
        # Verify that Chirp voices were skipped and Wavenet was used
        calls = [call for call in mock_post.call_args_list]
        voice_names = [call[1]['json']['voice']['name'] for call in calls]
        # Should have tried Wavenet (Chirp skipped due to budget)
        self.assertIn('ru-RU-Wavenet-A', voice_names)

    @patch('budget_guard.try_consume_tts')
    @patch('requests.post')
    def test_all_tiers_exhausted_returns_429(self, mock_post, mock_budget):
        """Test that when all tiers are exhausted, returns 429 without calling Google."""
        mock_budget.return_value = (True, None)
        
        # Set all tiers at cap
        month = tts_budget._current_month()
        tts_budget._file_budget[month] = {
            "chirp3hd": tts_budget.TTS_MONTHLY_CAP_CHIRP3HD,
            "neural2": tts_budget.TTS_MONTHLY_CAP_NEURAL2,
            "wavenet_standard": tts_budget.TTS_MONTHLY_CAP_WAVENET_STANDARD,
        }
        
        # Allow text in budget_guard
        import budget_guard as budget
        budget.allow_tts_for("Test text")
        
        response = self.app.post('/tts', json={
            'text': 'Test text',
            'language': 'ru-RU'
        })
        
        self.assertEqual(response.status_code, 429)
        data = response.get_json()
        self.assertEqual(data["error"], "limit")
        self.assertEqual(data["reason"], "monthly_tts_cap")
        
        # Verify Google was NOT called
        self.assertFalse(mock_post.called)

    def test_budget_status_endpoint_includes_monthly(self):
        """Test that /budget/status includes monthly section."""
        response = self.app.get('/budget/status')
        self.assertEqual(response.status_code, 200)
        
        data = response.get_json()
        self.assertIn("daily", data)
        self.assertIn("monthly", data)
        
        monthly = data["monthly"]
        self.assertIn("month", monthly)
        self.assertIn("tiers", monthly)
        self.assertIn("chirp3hd", monthly["tiers"])
        self.assertIn("neural2", monthly["tiers"])
        self.assertIn("wavenet_standard", monthly["tiers"])


class TTSBudgetDatabaseTest(unittest.TestCase):
    """Tests for database mode (mocked)."""
    
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        tts_budget.DATA_DIR = tts_budget.Path(self.tmp)
        tts_budget.TTS_BUDGET_PATH = tts_budget.DATA_DIR / "tts_monthly_budget.json"
        self.original_use_db = tts_budget.USE_DATABASE
        tts_budget._file_budget = {}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        tts_budget.USE_DATABASE = self.original_use_db
        tts_budget._file_budget = {}
        tts_budget._reset_connection()

    @patch('tts_budget._get_db_connection')
    def test_db_mode_increments_atomically(self, mock_get_conn):
        """Test that database mode uses atomic INSERT ... ON CONFLICT DO UPDATE."""
        tts_budget.USE_DATABASE = True
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        month = tts_budget._current_month()
        success, _ = tts_budget._increment_db(month, "wavenet_standard", 500)
        
        self.assertTrue(success)
        self.assertTrue(mock_cursor.execute.called)
        
        # Verify SQL contains ON CONFLICT clause
        sql = mock_cursor.execute.call_args[0][0]
        self.assertIn("ON CONFLICT", sql.upper())
        self.assertIn("DO UPDATE", sql.upper())

    @patch('tts_budget._get_db_connection')
    def test_db_mode_falls_back_to_file_on_error(self, mock_get_conn):
        """Test that database errors fall back to file mode."""
        tts_budget.USE_DATABASE = True
        mock_get_conn.return_value = None
        
        # Should fall back to file
        allowed, reason = tts_budget.check_and_reserve("ru-RU-Wavenet-A", 100)
        # In file fallback, wavenet should work but premium tiers blocked
        self.assertTrue(allowed)
        
        # Premium tiers should be blocked in degraded mode
        allowed_chirp, reason_chirp = tts_budget.check_and_reserve("ru-RU-Chirp3-HD-Kore", 100)
        self.assertFalse(allowed_chirp)
        self.assertEqual(reason_chirp, "degraded_mode")


if __name__ == "__main__":
    unittest.main()
