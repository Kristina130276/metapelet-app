#!/usr/bin/env python3
"""Capture screenshots with proper language switching using Selenium."""

import time
import os
from pathlib import Path

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except ImportError:
    print("Installing selenium...")
    os.system("pip3 install selenium --quiet")
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

def setup_driver(width, height):
    """Set up Chrome driver with specified viewport size."""
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument(f'--window-size={width},{height}')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(width, height)
    return driver

def capture_landing_page(url, lang_code, lang_label, width, height, filename):
    """Capture landing page screenshot in specified language."""
    print(f"Capturing {lang_label} landing page ({width}x{height})...")
    driver = setup_driver(width, height)
    
    try:
        # Load page
        driver.get(url)
        time.sleep(2)
        
        # Find and click the language button
        if lang_code != 'ru':  # Russian is default
            wait = WebDriverWait(driver, 10)
            lang_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'button.lang-btn[data-lang="{lang_code}"]'))
            )
            lang_btn.click()
            time.sleep(2)  # Wait for content to update
        
        # Take screenshot
        driver.save_screenshot(filename)
        print(f"  ✅ Saved to {filename}")
        
        # Verify language in page
        html = driver.page_source
        if lang_code == 'ru':
            assert 'Тёплый голосовой собеседник' in html, "Russian text not found"
        elif lang_code == 'he':
            assert 'בן שיח קולי וחם' in html, "Hebrew text not found"
            # Check RTL
            body_dir = driver.find_element(By.TAG_NAME, 'body').get_attribute('dir')
            assert body_dir == 'rtl', f"Expected RTL, got {body_dir}"
            print(f"  ✅ RTL verified for Hebrew")
        elif lang_code == 'en':
            assert 'A warm voice companion' in html, "English text not found"
        
    finally:
        driver.quit()

def capture_privacy_page(url, lang_code, lang_label, width, height, filename):
    """Capture privacy page screenshot in specified language."""
    print(f"Capturing {lang_label} privacy page ({width}x{height})...")
    driver = setup_driver(width, height)
    
    try:
        # Load privacy page
        driver.get(url + "/privacy")
        time.sleep(2)
        
        # Find and click the language button
        if lang_code != 'ru':  # Russian is default
            wait = WebDriverWait(driver, 10)
            lang_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'button.lang-btn[data-lang="{lang_code}"]'))
            )
            lang_btn.click()
            time.sleep(2)
        
        # Take screenshot
        driver.save_screenshot(filename)
        print(f"  ✅ Saved to {filename}")
        
    finally:
        driver.quit()

def main():
    base_url = "http://localhost:5000"
    static_dir = Path("/workspace/static")
    static_dir.mkdir(exist_ok=True)
    
    # Test that server is running
    import urllib.request
    try:
        urllib.request.urlopen(base_url, timeout=5)
    except:
        print("❌ Server not running at http://localhost:5000")
        print("Start server first: python3 app.py")
        return 1
    
    screenshots = [
        # Desktop landing page
        ('ru', 'Russian', 1920, 1080, static_dir / "screenshot-desktop-ru.png", 'landing'),
        ('he', 'Hebrew', 1920, 1080, static_dir / "screenshot-desktop-he.png", 'landing'),
        ('en', 'English', 1920, 1080, static_dir / "screenshot-desktop-en.png", 'landing'),
        
        # Mobile landing page at 375px
        ('ru', 'Russian', 375, 812, static_dir / "screenshot-mobile-ru.png", 'landing'),
        ('he', 'Hebrew', 375, 812, static_dir / "screenshot-mobile-he.png", 'landing'),
        ('en', 'English', 375, 812, static_dir / "screenshot-mobile-en.png", 'landing'),
        
        # Mobile landing page at 320px (narrow phones)
        ('ru', 'Russian', 320, 568, static_dir / "screenshot-mobile-320-ru.png", 'landing'),
        ('he', 'Hebrew', 320, 568, static_dir / "screenshot-mobile-320-he.png", 'landing'),
        
        # Privacy page mobile
        ('he', 'Hebrew', 375, 812, static_dir / "screenshot-privacy-mobile-he.png", 'privacy'),
    ]
    
    for item in screenshots:
        if len(item) == 6:
            lang_code, lang_label, width, height, filename, page_type = item
        else:
            lang_code, lang_label, width, height, filename = item
            page_type = 'landing'
            
        try:
            if page_type == 'privacy':
                capture_privacy_page(base_url, lang_code, lang_label, width, height, str(filename))
            else:
                capture_landing_page(base_url, lang_code, lang_label, width, height, str(filename))
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            import traceback
            traceback.print_exc()
            return 1
    
    print("\n✅ All screenshots captured successfully!")
    return 0

if __name__ == "__main__":
    exit(main())
