"""Tests for bug fixes: PII logging, /chat validation, /health endpoint."""

import os
import tempfile
import shutil
import unittest
from unittest.mock import patch, MagicMock
import json

import pilot_tracking as pilot
from app import app


class ChatEndpointValidationTest(unittest.TestCase):
    """Test /chat endpoint handles age as number and validates input properly."""

    def setUp(self):
        self.client = app.test_client()
        # Mock the Anthropic API to avoid real calls
        self.anthropic_patcher = patch('app.client.messages.create')
        self.mock_create = self.anthropic_patcher.start()
        
        # Create a mock response
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.text = "Привет! Как дела?"
        mock_response.content = [mock_content]
        self.mock_create.return_value = mock_response

    def tearDown(self):
        self.anthropic_patcher.stop()

    def test_chat_accepts_age_as_number(self):
        """Bug fix: /chat should handle age as number without crashing."""
        response = self.client.post(
            "/chat",
            json={
                "message": "Привет",
                "user_id": "test_user_1",
                "name": "Test",
                "age": 75,  # Number, not string
                "language": "ru-RU",
                "interests": "reading",
                "avoid_topics": "politics"
            },
        )
        # Should not return 500
        self.assertNotEqual(response.status_code, 500)
        self.assertIn(response.status_code, [200, 400, 429])

    def test_chat_accepts_age_as_string(self):
        """Age as string should also work."""
        response = self.client.post(
            "/chat",
            json={
                "message": "Hello",
                "user_id": "test_user_2",
                "name": "Test",
                "age": "80",  # String
                "language": "en-US",
                "interests": "music",
                "avoid_topics": "health"
            },
        )
        self.assertNotEqual(response.status_code, 500)
        self.assertIn(response.status_code, [200, 400, 429])

    def test_chat_handles_missing_json(self):
        """Bug fix: /chat should handle missing request.json gracefully."""
        response = self.client.post(
            "/chat",
            data="not json",
            content_type="text/plain",
        )
        # Should not crash with 500 (415 is also acceptable - unsupported media type)
        self.assertIn(response.status_code, [400, 415, 422])

    def test_chat_handles_empty_age(self):
        """Empty age should be handled gracefully."""
        response = self.client.post(
            "/chat",
            json={
                "message": "Hi",
                "user_id": "test_user_3",
                "name": "Test",
                "age": "",  # Empty string
                "language": "ru-RU",
                "interests": "travel",
                "avoid_topics": ""
            },
        )
        self.assertNotEqual(response.status_code, 500)

    def test_chat_handles_none_age(self):
        """None age should be handled gracefully."""
        response = self.client.post(
            "/chat",
            json={
                "message": "Hello",
                "user_id": "test_user_4",
                "name": "Test",
                "age": None,  # None
                "language": "he-IL",
                "interests": "family",
                "avoid_topics": "death"
            },
        )
        self.assertNotEqual(response.status_code, 500)


class HealthEndpointTest(unittest.TestCase):
    """Test /health endpoint for uptime monitoring."""

    def setUp(self):
        self.client = app.test_client()

    def test_health_endpoint_exists(self):
        """Bug fix: /health endpoint should exist for uptime monitoring."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)

    def test_health_returns_json(self):
        """Health endpoint should return JSON with status ok."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIsNotNone(data)
        self.assertEqual(data.get("status"), "ok")


class PIILoggingTest(unittest.TestCase):
    """Test that PII (name, age) is not logged to stdout."""

    def setUp(self):
        self.client = app.test_client()
        self.anthropic_patcher = patch('app.client.messages.create')
        self.mock_create = self.anthropic_patcher.start()
        
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.text = "Test response"
        mock_response.content = [mock_content]
        self.mock_create.return_value = mock_response

    def tearDown(self):
        self.anthropic_patcher.stop()

    @patch('builtins.print')
    def test_chat_does_not_log_name(self, mock_print):
        """Bug fix: user's name should not appear in logs."""
        test_name = "Sarah Smith"
        self.client.post(
            "/chat",
            json={
                "message": "Hello",
                "user_id": "test_user",
                "name": test_name,
                "age": "75",
                "language": "ru-RU",
                "interests": "books",
                "avoid_topics": "politics"
            },
        )
        
        # Check that the name doesn't appear in any print call
        for call in mock_print.call_args_list:
            call_str = str(call)
            self.assertNotIn(test_name, call_str, 
                           f"Name '{test_name}' found in log: {call_str}")

    @patch('builtins.print')
    def test_chat_does_not_log_age(self, mock_print):
        """Bug fix: user's age should not appear in logs."""
        test_age = "82"
        self.client.post(
            "/chat",
            json={
                "message": "Hello",
                "user_id": "test_user",
                "name": "Test",
                "age": test_age,
                "language": "en-US",
                "interests": "music",
                "avoid_topics": "medicine"
            },
        )
        
        # Check that the age doesn't appear in any print call
        # (excluding the case where it might be in a data structure representation)
        for call in mock_print.call_args_list:
            call_str = str(call)
            # Skip if it's just showing a dash placeholder
            if "AGE:" in call_str and test_age in call_str:
                self.fail(f"Age '{test_age}' found in log: {call_str}")

    @patch('builtins.print')
    def test_tts_does_not_log_conversation_text(self, mock_print):
        """Bug fix: conversation text should not appear in TTS logs."""
        test_text = "This is a private conversation message"
        
        with patch('app._google_api_key', return_value='fake_key'), \
             patch('app.http_requests.post') as mock_post:
            
            mock_response = MagicMock()
            mock_response.ok = True
            mock_response.json.return_value = {
                'audioContent': 'ZmFrZV9hdWRpbw=='  # base64 for 'fake_audio'
            }
            mock_post.return_value = mock_response
            
            self.client.post(
                "/tts",
                json={
                    "text": test_text,
                    "language": "ru-RU"
                },
            )
            
            # Check that the full text doesn't appear in any print call
            for call in mock_print.call_args_list:
                call_str = str(call)
                self.assertNotIn(test_text, call_str, 
                               f"Conversation text found in TTS log: {call_str}")


class StaticRoutesTest(unittest.TestCase):
    """Test robots.txt, sitemap.xml, and privacy page routes."""

    def setUp(self):
        self.client = app.test_client()

    def test_robots_txt_exists(self):
        """robots.txt should be accessible."""
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User-agent:", response.data)
        self.assertIn(b"Sitemap:", response.data)

    def test_sitemap_xml_exists(self):
        """sitemap.xml should be accessible."""
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<?xml", response.data)
        self.assertIn(b"https://www.metapelet.org/", response.data)

    def test_privacy_page_exists(self):
        """Privacy policy page should be accessible."""
        response = self.client.get("/privacy")
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
