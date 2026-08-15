"""Tests for anonymous pilot tracking (no deploy, no API keys)."""

import json
import os
import shutil
import tempfile
import unittest

import pilot_tracking as pilot
from app import app


class PilotTrackingModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        pilot.DATA_DIR = pilot.Path(self.tmp)
        pilot.EVENTS_PATH = pilot.DATA_DIR / "pilot_events.jsonl"
        pilot.FEEDBACK_PATH = pilot.DATA_DIR / "pilot_feedback.jsonl"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

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

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._old_token is None:
            os.environ.pop("PILOT_ADMIN_TOKEN", None)
        else:
            os.environ["PILOT_ADMIN_TOKEN"] = self._old_token

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


if __name__ == "__main__":
    unittest.main()
