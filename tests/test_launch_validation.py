import unittest
from unittest.mock import patch
from app import app

class LaunchValidation(unittest.TestCase):
    def test_bad_requests_never_call_paid_api(self):
        cases = [[], {'message':'hi','user_id':[]}, {'message':'hi','name':123},
                 {'message':'hi','interests':'x'*1001}, {'message':'hi','language':'ar'},
                 {'message':'hi','age':True}, {'message':'hi','age':17}]
        with patch('app.client.messages.create') as api:
            for body in cases:
                with self.subTest(body_type=type(body).__name__):
                    self.assertEqual(app.test_client().post('/chat',json=body).status_code,400)
            api.assert_not_called()
    def test_oversized_body_and_tts(self):
        c=app.test_client()
        self.assertEqual(c.post('/chat',json={'message':'x'*17000}).status_code,413)
        with patch('app.http_requests.post') as api:
            self.assertEqual(c.post('/tts',json=[]).status_code,400)
            self.assertEqual(c.post('/tts',json={'text':42}).status_code,400)
            api.assert_not_called()
