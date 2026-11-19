"""Tests for Chirp 3 HD audioConfig parameter filtering.

Chirp 3 HD voices do not support the pitch parameter but do support speakingRate.
This test suite ensures:
1. Chirp3-HD voices receive audioConfig without pitch
2. Wavenet/Standard/Neural2 voices receive audioConfig with pitch
3. Fallback still works when Chirp3-HD fails
"""

import unittest
from unittest.mock import patch, MagicMock, call
import json
import base64

from app import app


class Chirp3HDAudioConfigTest(unittest.TestCase):
    """Test that Chirp 3 HD voices get correct audioConfig without pitch."""

    def setUp(self):
        self.client = app.test_client()
        # Mock Anthropic to allow TTS requests
        self.anthropic_patcher = patch('app.client.messages.create')
        self.mock_create = self.anthropic_patcher.start()
        
        mock_response = MagicMock()
        mock_content = MagicMock()
        mock_content.text = "Привет! Как дела?"
        mock_response.content = [mock_content]
        self.mock_create.return_value = mock_response
        
        # Mock budget guard to allow TTS
        self.budget_patcher = patch('app.budget.try_consume_tts')
        self.mock_budget = self.budget_patcher.start()
        self.mock_budget.return_value = (True, None)

    def tearDown(self):
        self.anthropic_patcher.stop()
        self.budget_patcher.stop()

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_chirp3hd_audioconfig_no_pitch(self, mock_post, mock_api_key):
        """Chirp3-HD voices should get audioConfig without pitch parameter."""
        # Mock successful response from Google
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            'audioContent': base64.b64encode(b'fake_audio').decode('utf-8')
        }
        mock_post.return_value = mock_response

        # Make TTS request for ru-RU (first voice is Chirp3-HD-Kore)
        response = self.client.post(
            "/tts",
            json={
                "text": "Привет! Как дела?",
                "language": "ru-RU"
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('X-TTS-Voice', response.headers)
        
        # Verify the first call was for Chirp3-HD and had no pitch
        first_call = mock_post.call_args_list[0]
        payload = first_call[1]['json']
        
        self.assertEqual(payload['voice']['name'], 'ru-RU-Chirp3-HD-Kore')
        self.assertIn('speakingRate', payload['audioConfig'])
        self.assertNotIn('pitch', payload['audioConfig'],
                        "Chirp3-HD audioConfig should not contain pitch parameter")
        self.assertIn('volumeGainDb', payload['audioConfig'])
        self.assertEqual(payload['audioConfig']['audioEncoding'], 'MP3')

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_wavenet_audioconfig_has_pitch(self, mock_post, mock_api_key):
        """Wavenet voices should get audioConfig with pitch parameter."""
        # Mock Chirp3-HD failures, Wavenet success
        chirp_fail = MagicMock()
        chirp_fail.ok = False
        chirp_fail.json.return_value = {
            'error': {'message': 'pitch not supported', 'status': 'INVALID_ARGUMENT'}
        }
        chirp_fail.text = 'Error'
        
        wavenet_success = MagicMock()
        wavenet_success.ok = True
        wavenet_success.json.return_value = {
            'audioContent': base64.b64encode(b'fake_audio').decode('utf-8')
        }
        
        mock_post.side_effect = [chirp_fail, chirp_fail, wavenet_success]

        response = self.client.post(
            "/tts",
            json={
                "text": "Привет! Как дела?",
                "language": "ru-RU"
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-TTS-Voice'), 'ru-RU-Wavenet-A')
        
        # Check the Wavenet call (3rd call)
        wavenet_call = mock_post.call_args_list[2]
        payload = wavenet_call[1]['json']
        
        self.assertEqual(payload['voice']['name'], 'ru-RU-Wavenet-A')
        self.assertIn('speakingRate', payload['audioConfig'])
        self.assertIn('pitch', payload['audioConfig'],
                     "Wavenet audioConfig should contain pitch parameter")
        self.assertEqual(payload['audioConfig']['pitch'], -0.5)

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_fallback_mechanism_still_works(self, mock_post, mock_api_key):
        """Fallback from Chirp3-HD to Wavenet should still work."""
        # Mock first two Chirp3-HD failures, then Wavenet success
        chirp_fail = MagicMock()
        chirp_fail.ok = False
        chirp_fail.json.return_value = {
            'error': {'message': 'Service unavailable', 'status': 'UNAVAILABLE'}
        }
        chirp_fail.text = 'Error'
        
        wavenet_success = MagicMock()
        wavenet_success.ok = True
        wavenet_success.json.return_value = {
            'audioContent': base64.b64encode(b'fake_audio_wavenet').decode('utf-8')
        }
        
        # Two Chirp3-HD fails, then Wavenet succeeds
        mock_post.side_effect = [chirp_fail, chirp_fail, wavenet_success]

        response = self.client.post(
            "/tts",
            json={
                "text": "Тестовый текст",
                "language": "ru-RU"
            },
        )

        # Should succeed with Wavenet
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('X-TTS-Voice'), 'ru-RU-Wavenet-A')
        
        # Verify all three voices were tried
        self.assertEqual(mock_post.call_count, 3)
        
        voice_names = [call[1]['json']['voice']['name'] for call in mock_post.call_args_list]
        self.assertEqual(voice_names[0], 'ru-RU-Chirp3-HD-Kore')
        self.assertEqual(voice_names[1], 'ru-RU-Chirp3-HD-Aoede')
        self.assertEqual(voice_names[2], 'ru-RU-Wavenet-A')

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_neural2_audioconfig_has_pitch(self, mock_post, mock_api_key):
        """Neural2 voices (en-US) should get audioConfig with pitch parameter."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            'audioContent': base64.b64encode(b'fake_audio').decode('utf-8')
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/tts",
            json={
                "text": "Hello there",
                "language": "en-US"
            },
        )

        self.assertEqual(response.status_code, 200)
        
        # Check the Neural2 call
        first_call = mock_post.call_args_list[0]
        payload = first_call[1]['json']
        
        self.assertEqual(payload['voice']['name'], 'en-US-Neural2-F')
        self.assertIn('pitch', payload['audioConfig'],
                     "Neural2 audioConfig should contain pitch parameter")
        self.assertEqual(payload['audioConfig']['pitch'], 0.0)

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_logging_includes_voice_name_on_failure(self, mock_post, mock_api_key):
        """Logging should include which voice failed and the error detail."""
        chirp_fail = MagicMock()
        chirp_fail.ok = False
        chirp_fail.json.return_value = {
            'error': {
                'message': 'Invalid pitch parameter',
                'status': 'INVALID_ARGUMENT'
            }
        }
        chirp_fail.text = 'Error detail'
        mock_post.return_value = chirp_fail

        with patch('builtins.print') as mock_print:
            response = self.client.post(
                "/tts",
                json={
                    "text": "Test",
                    "language": "ru-RU"
                },
            )

            # Should fail after trying all voices
            self.assertEqual(response.status_code, 500)
            
            # Check that voice names were logged
            print_calls = [str(call) for call in mock_print.call_args_list]
            voice_logs = [log for log in print_calls if 'TTS voice' in log and 'failed' in log]
            
            self.assertGreater(len(voice_logs), 0, "Should log which voice failed")
            # Verify specific voice names appear in logs
            all_logs = ' '.join(print_calls)
            self.assertIn('ru-RU-Chirp3-HD-Kore', all_logs)
            self.assertIn('Invalid pitch parameter', all_logs)

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_hebrew_wavenet_has_pitch(self, mock_post, mock_api_key):
        """Hebrew Wavenet voices should get audioConfig with pitch parameter."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            'audioContent': base64.b64encode(b'fake_audio').decode('utf-8')
        }
        mock_post.return_value = mock_response

        response = self.client.post(
            "/tts",
            json={
                "text": "שלום",
                "language": "he-IL"
            },
        )

        self.assertEqual(response.status_code, 200)
        
        first_call = mock_post.call_args_list[0]
        payload = first_call[1]['json']
        
        self.assertEqual(payload['voice']['name'], 'he-IL-Wavenet-A')
        self.assertIn('pitch', payload['audioConfig'])
        self.assertEqual(payload['audioConfig']['pitch'], 0.0)

    @patch('app._google_api_key', return_value='test_api_key')
    @patch('app.http_requests.post')
    def test_all_voices_preserve_speaking_rate(self, mock_post, mock_api_key):
        """All voices should preserve speakingRate parameter."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = {
            'audioContent': base64.b64encode(b'fake_audio').decode('utf-8')
        }
        mock_post.return_value = mock_response

        # Test each language
        languages = [
            ("ru-RU", "Привет", 0.88),
            ("he-IL", "שלום", 0.93),
            ("en-US", "Hello", 0.93),
        ]

        for lang, text, expected_rate in languages:
            mock_post.reset_mock()
            
            response = self.client.post(
                "/tts",
                json={"text": text, "language": lang},
            )

            self.assertEqual(response.status_code, 200)
            
            first_call = mock_post.call_args_list[0]
            payload = first_call[1]['json']
            
            self.assertEqual(payload['audioConfig']['speakingRate'], expected_rate,
                           f"Language {lang} should have speakingRate {expected_rate}")


if __name__ == "__main__":
    unittest.main()
