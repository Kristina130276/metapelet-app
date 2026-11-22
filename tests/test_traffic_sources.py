"""Tests for traffic source tracking."""

import unittest
import os
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone

import traffic_sources


class TestTrafficSourceTracking(unittest.TestCase):
    """Test traffic source tracking functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        # Create temporary directory for test data
        self.temp_dir = tempfile.mkdtemp()
        traffic_sources.DATA_DIR = Path(self.temp_dir)
        traffic_sources.TRAFFIC_PATH = traffic_sources.DATA_DIR / "traffic_sources.json"
        
        # Disable database for file-based tests
        self.original_use_db = traffic_sources.USE_DATABASE
        traffic_sources.USE_DATABASE = False
        traffic_sources._db_connection = None
    
    def tearDown(self):
        """Clean up test fixtures."""
        traffic_sources.USE_DATABASE = self.original_use_db
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
    
    def test_sanitize_source_valid(self):
        """Test that valid sources are accepted."""
        self.assertEqual(traffic_sources.sanitize_source("yt"), "yt")
        self.assertEqual(traffic_sources.sanitize_source("ig"), "ig")
        self.assertEqual(traffic_sources.sanitize_source("fb"), "fb")
        self.assertEqual(traffic_sources.sanitize_source("tw"), "tw")
        self.assertEqual(traffic_sources.sanitize_source("li"), "li")
        self.assertEqual(traffic_sources.sanitize_source("other"), "other")
    
    def test_sanitize_source_variations(self):
        """Test that common variations are mapped correctly."""
        self.assertEqual(traffic_sources.sanitize_source("youtube"), "yt")
        self.assertEqual(traffic_sources.sanitize_source("YouTube"), "yt")
        self.assertEqual(traffic_sources.sanitize_source("instagram"), "ig")
        self.assertEqual(traffic_sources.sanitize_source("insta"), "ig")
        self.assertEqual(traffic_sources.sanitize_source("facebook"), "fb")
        self.assertEqual(traffic_sources.sanitize_source("Facebook"), "fb")
        self.assertEqual(traffic_sources.sanitize_source("twitter"), "tw")
        self.assertEqual(traffic_sources.sanitize_source("x"), "tw")
        self.assertEqual(traffic_sources.sanitize_source("linkedin"), "li")
    
    def test_sanitize_source_invalid(self):
        """Test that invalid sources become 'other'."""
        self.assertEqual(traffic_sources.sanitize_source("random123"), "other")
        self.assertEqual(traffic_sources.sanitize_source(""), "other")
        self.assertEqual(traffic_sources.sanitize_source("   "), "other")
        self.assertEqual(traffic_sources.sanitize_source("тест"), "other")
    
    def test_sanitize_source_truncation(self):
        """Test that very long sources are truncated."""
        long_source = "a" * 100
        result = traffic_sources.sanitize_source(long_source)
        self.assertEqual(result, "other")
    
    def test_record_visit(self):
        """Test recording a visit."""
        ok, err = traffic_sources.record_visit("yt")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        
        # Verify data was written
        data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertIn(today, data)
        self.assertIn("yt", data[today])
        self.assertEqual(data[today]["yt"]["visits"], 1)
        self.assertEqual(data[today]["yt"]["conversations_started"], 0)
    
    def test_record_multiple_visits(self):
        """Test recording multiple visits increments count."""
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("yt")
        
        data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertEqual(data[today]["yt"]["visits"], 3)
    
    def test_record_visit_different_sources(self):
        """Test recording visits from different sources."""
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("ig")
        traffic_sources.record_visit("fb")
        
        data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertEqual(data[today]["yt"]["visits"], 1)
        self.assertEqual(data[today]["ig"]["visits"], 1)
        self.assertEqual(data[today]["fb"]["visits"], 1)
    
    def test_record_conversation_started(self):
        """Test recording a conversation started."""
        ok, err = traffic_sources.record_conversation_started("yt")
        self.assertTrue(ok)
        self.assertEqual(err, "")
        
        # Verify data was written
        data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertIn(today, data)
        self.assertIn("yt", data[today])
        self.assertEqual(data[today]["yt"]["visits"], 0)
        self.assertEqual(data[today]["yt"]["conversations_started"], 1)
    
    def test_record_multiple_conversations(self):
        """Test recording multiple conversations increments count."""
        traffic_sources.record_conversation_started("yt")
        traffic_sources.record_conversation_started("yt")
        
        data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertEqual(data[today]["yt"]["conversations_started"], 2)
    
    def test_record_visit_and_conversation(self):
        """Test recording both visits and conversations."""
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("yt")
        traffic_sources.record_conversation_started("yt")
        
        data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertEqual(data[today]["yt"]["visits"], 2)
        self.assertEqual(data[today]["yt"]["conversations_started"], 1)
    
    def test_get_all_traffic_for_export_empty(self):
        """Test export with no data."""
        result = traffic_sources.get_all_traffic_for_export()
        self.assertEqual(result, [])
    
    def test_get_all_traffic_for_export(self):
        """Test export with data."""
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("yt")
        traffic_sources.record_conversation_started("yt")
        traffic_sources.record_visit("ig")
        
        result = traffic_sources.get_all_traffic_for_export()
        
        self.assertEqual(len(result), 2)
        
        # Check structure
        for row in result:
            self.assertIn("date", row)
            self.assertIn("source", row)
            self.assertIn("visits", row)
            self.assertIn("conversations_started", row)
        
        # Find rows by source
        yt_row = next(r for r in result if r["source"] == "yt")
        ig_row = next(r for r in result if r["source"] == "ig")
        
        self.assertEqual(yt_row["visits"], 2)
        self.assertEqual(yt_row["conversations_started"], 1)
        self.assertEqual(ig_row["visits"], 1)
        self.assertEqual(ig_row["conversations_started"], 0)
    
    def test_get_all_traffic_for_export_sorted(self):
        """Test that export is sorted by date DESC, source ASC."""
        # Create data for today
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("ig")
        
        result = traffic_sources.get_all_traffic_for_export()
        
        # Check that sources are in alphabetical order
        sources = [r["source"] for r in result]
        self.assertEqual(sources, sorted(sources))
    
    def test_sanitize_database_url(self):
        """Test database URL sanitization."""
        # Test basic URL
        url = "postgresql://user:pass@host.neon.tech/db"
        result = traffic_sources._sanitize_database_url(url)
        self.assertIn("postgresql://", result)
        self.assertIn("neon.tech", result)
        self.assertIn("sslmode=require", result)
        
        # Test with quotes
        url = '"postgresql://user:pass@host/db"'
        result = traffic_sources._sanitize_database_url(url)
        self.assertNotIn('"', result)
        
        # Test postgres:// is converted to postgresql://
        url = "postgres://user:pass@host/db"
        result = traffic_sources._sanitize_database_url(url)
        self.assertTrue(result.startswith("postgresql://"))
    
    def test_file_persistence(self):
        """Test that data persists across file reads/writes."""
        traffic_sources.record_visit("yt")
        
        # Read the data fresh
        data1 = traffic_sources._read_traffic_file()
        
        # Record more data
        traffic_sources.record_visit("ig")
        
        # Read again
        data2 = traffic_sources._read_traffic_file()
        
        today = traffic_sources._today_date()
        self.assertEqual(data2[today]["yt"]["visits"], 1)
        self.assertEqual(data2[today]["ig"]["visits"], 1)


class TestTrafficSourceEndpoints(unittest.TestCase):
    """Test Flask endpoints for traffic source tracking."""
    
    def setUp(self):
        """Set up test fixtures."""
        from app import app
        self.app = app
        self.client = self.app.test_client()
        
        # Create temporary directory for test data
        self.temp_dir = tempfile.mkdtemp()
        traffic_sources.DATA_DIR = Path(self.temp_dir)
        traffic_sources.TRAFFIC_PATH = traffic_sources.DATA_DIR / "traffic_sources.json"
        
        # Disable database for file-based tests
        self.original_use_db = traffic_sources.USE_DATABASE
        traffic_sources.USE_DATABASE = False
        traffic_sources._db_connection = None
        
        # Store original ADMIN_TOKEN
        self.original_admin_token = os.environ.get("ADMIN_TOKEN")
        os.environ["ADMIN_TOKEN"] = "test_admin_token_123"
    
    def tearDown(self):
        """Clean up test fixtures."""
        traffic_sources.USE_DATABASE = self.original_use_db
        if Path(self.temp_dir).exists():
            shutil.rmtree(self.temp_dir)
        
        # Restore original ADMIN_TOKEN
        if self.original_admin_token is not None:
            os.environ["ADMIN_TOKEN"] = self.original_admin_token
        else:
            os.environ.pop("ADMIN_TOKEN", None)
    
    def test_traffic_visit_endpoint(self):
        """Test /traffic/visit endpoint."""
        response = self.client.post('/traffic/visit', json={
            'source': 'yt'
        })
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertTrue(data['ok'])
        
        # Verify it was recorded
        traffic_data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertEqual(traffic_data[today]['yt']['visits'], 1)
    
    def test_traffic_visit_endpoint_missing_source(self):
        """Test /traffic/visit endpoint with missing source."""
        response = self.client.post('/traffic/visit', json={})
        self.assertEqual(response.status_code, 400)
        data = response.json
        self.assertFalse(data['ok'])
    
    def test_traffic_conversation_endpoint(self):
        """Test /traffic/conversation endpoint."""
        response = self.client.post('/traffic/conversation', json={
            'source': 'yt'
        })
        self.assertEqual(response.status_code, 200)
        data = response.json
        self.assertTrue(data['ok'])
        
        # Verify it was recorded
        traffic_data = traffic_sources._read_traffic_file()
        today = traffic_sources._today_date()
        self.assertEqual(traffic_data[today]['yt']['conversations_started'], 1)
    
    def test_traffic_conversation_endpoint_missing_source(self):
        """Test /traffic/conversation endpoint with missing source."""
        response = self.client.post('/traffic/conversation', json={})
        self.assertEqual(response.status_code, 400)
        data = response.json
        self.assertFalse(data['ok'])
    
    def test_admin_sources_csv_no_token(self):
        """Test /admin/sources.csv requires authentication."""
        response = self.client.get('/admin/sources.csv')
        self.assertEqual(response.status_code, 403)
    
    def test_admin_sources_csv_wrong_token(self):
        """Test /admin/sources.csv rejects wrong token."""
        response = self.client.get('/admin/sources.csv?token=wrong_token')
        self.assertEqual(response.status_code, 403)
    
    def test_admin_sources_csv_valid_token(self):
        """Test /admin/sources.csv with valid token."""
        # Record some data
        traffic_sources.record_visit("yt")
        traffic_sources.record_visit("yt")
        traffic_sources.record_conversation_started("yt")
        traffic_sources.record_visit("ig")
        
        response = self.client.get('/admin/sources.csv?token=test_admin_token_123')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/csv')
        
        # Check CSV content
        csv_content = response.data.decode('utf-8')
        self.assertIn('date,source,visits,conversations_started', csv_content)
        self.assertIn('yt', csv_content)
        self.assertIn('ig', csv_content)
        
        # Check counts in CSV
        lines = csv_content.strip().split('\n')
        self.assertGreaterEqual(len(lines), 3)  # header + at least 2 data rows
    
    def test_admin_sources_csv_empty(self):
        """Test /admin/sources.csv with no data."""
        response = self.client.get('/admin/sources.csv?token=test_admin_token_123')
        self.assertEqual(response.status_code, 200)
        csv_content = response.data.decode('utf-8')
        self.assertIn('No traffic source data available', csv_content)
    
    def test_admin_sources_csv_no_admin_token_env(self):
        """Test /admin/sources.csv when ADMIN_TOKEN is not set."""
        os.environ.pop("ADMIN_TOKEN", None)
        response = self.client.get('/admin/sources.csv?token=any_token')
        self.assertEqual(response.status_code, 404)


if __name__ == '__main__':
    unittest.main()
