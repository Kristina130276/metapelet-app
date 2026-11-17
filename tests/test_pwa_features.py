"""Tests for PWA features (elder-friendly setup and talk pages)."""

import json
import unittest
from app import app


class PWARoutesTest(unittest.TestCase):
    """Test PWA routes are accessible."""
    
    def setUp(self):
        self.client = app.test_client()
    
    def test_setup_page_exists(self):
        """Test /setup route returns 200."""
        resp = self.client.get("/setup")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"MetaPelet", resp.data)
    
    def test_talk_page_exists(self):
        """Test /talk route returns 200."""
        resp = self.client.get("/talk")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"MetaPelet", resp.data)
    
    def test_manifest_json_exists(self):
        """Test /static/manifest.json route returns valid JSON."""
        resp = self.client.get("/static/manifest.json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content_type, "application/manifest+json")
        data = json.loads(resp.data)
        self.assertEqual(data["name"], "MetaPelet")
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["start_url"], "/talk")
        self.assertIn("icons", data)
        self.assertGreater(len(data["icons"]), 0)
    
    def test_service_worker_exists(self):
        """Test /static/service-worker.js route returns JavaScript."""
        resp = self.client.get("/static/service-worker.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/javascript", resp.content_type)
        self.assertIn(b"service worker", resp.data.lower())
    
    def test_sitemap_includes_new_pages(self):
        """Test sitemap includes /setup and /talk."""
        resp = self.client.get("/sitemap.xml")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"metapelet.org/setup", resp.data)
        self.assertIn(b"metapelet.org/talk", resp.data)


class PWAContentTest(unittest.TestCase):
    """Test PWA pages contain required elements."""
    
    def setUp(self):
        self.client = app.test_client()
    
    def test_setup_page_has_manifest_link(self):
        """Test setup page links to manifest."""
        resp = self.client.get("/setup")
        self.assertIn(b'rel="manifest"', resp.data)
        self.assertIn(b'/static/manifest.json', resp.data)
    
    def test_setup_page_has_apple_touch_icon(self):
        """Test setup page has apple-touch-icon."""
        resp = self.client.get("/setup")
        self.assertIn(b'rel="apple-touch-icon"', resp.data)
        self.assertIn(b'/static/apple-touch-icon.png', resp.data)
    
    def test_setup_page_has_language_support(self):
        """Test setup page supports multiple languages."""
        resp = self.client.get("/setup")
        html = resp.data.decode('utf-8')
        # Check for Russian, Hebrew, and English content
        self.assertIn('data-lang="ru"', html)
        self.assertIn('data-lang="he"', html)
        self.assertIn('data-lang="en"', html)
    
    def test_talk_page_has_manifest_link(self):
        """Test talk page links to manifest."""
        resp = self.client.get("/talk")
        self.assertIn(b'rel="manifest"', resp.data)
        self.assertIn(b'/static/manifest.json', resp.data)
    
    def test_talk_page_has_apple_touch_icon(self):
        """Test talk page has apple-touch-icon."""
        resp = self.client.get("/talk")
        self.assertIn(b'rel="apple-touch-icon"', resp.data)
        self.assertIn(b'/static/apple-touch-icon.png', resp.data)
    
    def test_talk_page_has_pwa_meta_tags(self):
        """Test talk page has PWA meta tags."""
        resp = self.client.get("/talk")
        html = resp.data.decode('utf-8')
        self.assertIn('apple-mobile-web-app-capable', html)
        self.assertIn('theme-color', html)
    
    def test_talk_page_has_elder_friendly_elements(self):
        """Test talk page has large buttons and high contrast for elderly users."""
        resp = self.client.get("/talk")
        html = resp.data.decode('utf-8')
        # Check for large font sizes in CSS
        self.assertIn('font-size: 2.5rem', html)
        self.assertIn('btn-talk', html)
        # Check for accessibility features
        self.assertIn('user-scalable=yes', html)
    
    def test_talk_page_service_worker_registration(self):
        """Test talk page registers service worker."""
        resp = self.client.get("/talk")
        html = resp.data.decode('utf-8')
        self.assertIn('serviceWorker', html)
        self.assertIn('service-worker.js', html)


class PWAManifestValidationTest(unittest.TestCase):
    """Test manifest.json structure and validity."""
    
    def setUp(self):
        self.client = app.test_client()
    
    def test_manifest_has_required_fields(self):
        """Test manifest contains all required PWA fields."""
        resp = self.client.get("/static/manifest.json")
        data = json.loads(resp.data)
        
        required_fields = [
            "name",
            "short_name",
            "start_url",
            "display",
            "icons"
        ]
        
        for field in required_fields:
            self.assertIn(field, data, f"Manifest missing required field: {field}")
    
    def test_manifest_icons_have_required_sizes(self):
        """Test manifest icons include required sizes."""
        resp = self.client.get("/static/manifest.json")
        data = json.loads(resp.data)
        
        icon_sizes = [icon["sizes"] for icon in data["icons"]]
        self.assertIn("192x192", icon_sizes)
        self.assertIn("512x512", icon_sizes)
    
    def test_manifest_display_is_standalone(self):
        """Test manifest display mode is standalone."""
        resp = self.client.get("/static/manifest.json")
        data = json.loads(resp.data)
        self.assertEqual(data["display"], "standalone")
    
    def test_manifest_start_url_is_talk(self):
        """Test manifest starts at /talk for direct conversation."""
        resp = self.client.get("/static/manifest.json")
        data = json.loads(resp.data)
        self.assertEqual(data["start_url"], "/talk")


if __name__ == "__main__":
    unittest.main()
