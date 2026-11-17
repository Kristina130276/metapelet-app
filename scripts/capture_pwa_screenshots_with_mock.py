#!/usr/bin/env python3
"""Capture PWA screenshots with mocked conversation responses."""

import time
import os
import subprocess
import signal
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
    """Set up Chrome driver with mobile viewport."""
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument(f'--window-size={width},{height}')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15')
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(width, height)
    return driver


def capture_setup_page(url, lang_code, width, height, filename):
    """Capture setup page in specified language."""
    print(f"Capturing setup page ({lang_code}) at {width}x{height}...")
    driver = setup_driver(width, height)
    
    try:
        driver.get(f"{url}/setup")
        time.sleep(2)
        
        # Click language button if not Russian
        if lang_code != 'ru':
            wait = WebDriverWait(driver, 10)
            lang_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'button[data-lang-btn="{lang_code}"]'))
            )
            lang_btn.click()
            time.sleep(1)
        
        # Fill in sample data
        name_input = driver.find_element(By.ID, "name-input")
        name_input.send_keys("Мария" if lang_code == "ru" else "מריה" if lang_code == "he" else "Maria")
        
        age_input = driver.find_element(By.ID, "age-input")
        age_input.send_keys("75")
        
        time.sleep(1)
        driver.save_screenshot(filename)
        print(f"✓ Saved {filename}")
        
    finally:
        driver.quit()


def capture_install_instructions(url, lang_code, platform, width, height, filename):
    """Capture install instructions screen."""
    print(f"Capturing install instructions ({lang_code}, {platform}) at {width}x{height}...")
    driver = setup_driver(width, height)
    
    try:
        driver.get(f"{url}/setup")
        time.sleep(2)
        
        # Click language button if not Russian
        if lang_code != 'ru':
            wait = WebDriverWait(driver, 10)
            lang_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'button[data-lang-btn="{lang_code}"]'))
            )
            lang_btn.click()
            time.sleep(1)
        
        # Fill and save
        driver.find_element(By.ID, "name-input").send_keys("Мария" if lang_code == "ru" else "מריה")
        driver.find_element(By.ID, "age-input").send_keys("75")
        driver.find_element(By.ID, "save-btn").click()
        time.sleep(2)
        
        # Show appropriate install instructions via JavaScript
        if platform == "ios":
            driver.execute_script("""
                document.getElementById('ios-instructions').classList.remove('hidden');
                document.getElementById('android-instructions').classList.add('hidden');
            """)
        else:
            driver.execute_script("""
                document.getElementById('android-instructions').classList.remove('hidden');
                document.getElementById('ios-instructions').classList.add('hidden');
            """)
        
        time.sleep(1)
        driver.save_screenshot(filename)
        print(f"✓ Saved {filename}")
        
    finally:
        driver.quit()


def capture_talk_page(url, lang_code, width, height, filename):
    """Capture talk page showing successful conversation."""
    print(f"Capturing talk page ({lang_code}) at {width}x{height}...")
    driver = setup_driver(width, height)
    
    try:
        # Set up localStorage with profile
        driver.get(f"{url}/setup")
        time.sleep(1)
        
        profile_data = {
            'ru': {'name': 'Мария', 'language': 'ru-RU'},
            'he': {'name': 'מריה', 'language': 'he-IL'}
        }
        
        profile = profile_data.get(lang_code, profile_data['ru'])
        
        driver.execute_script(f"""
            localStorage.setItem('metapelet_elder_profile', JSON.stringify({{
                name: "{profile['name']}",
                age: "75",
                language: "{profile['language']}",
                setupComplete: true
            }}));
        """)
        
        # Navigate to talk page
        driver.get(f"{url}/talk")
        time.sleep(4)
        
        # Wait for greeting to appear
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.ID, "reply-box")))
        
        # Let it auto-start and show the reply
        time.sleep(2)
        
        driver.save_screenshot(filename)
        print(f"✓ Saved {filename}")
        
    finally:
        driver.quit()


def main():
    """Capture all PWA screenshots with mocked responses."""
    # Start mock app
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    mock_app_path = script_dir / 'mock_app.py'
    
    print("Starting mock Flask app...")
    mock_proc = subprocess.Popen(
        ['python3', str(mock_app_path)],
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for app to start
    time.sleep(3)
    
    url = 'http://localhost:5000'
    screenshots_dir = repo_root / 'docs' / 'screenshots'
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    
    # Mobile viewport (390x844 is iPhone 12/13/14 size)
    mobile_width = 390
    mobile_height = 844
    
    try:
        print(f"\n📱 Capturing PWA screenshots from {url}\n")
        
        # Setup page screenshots
        capture_setup_page(url, 'ru', mobile_width, mobile_height, 
                          screenshots_dir / 'screenshot-pwa-setup-ru.png')
        capture_setup_page(url, 'he', mobile_width, mobile_height, 
                          screenshots_dir / 'screenshot-pwa-setup-he.png')
        
        # Install instructions
        capture_install_instructions(url, 'ru', 'android', mobile_width, mobile_height,
                                    screenshots_dir / 'screenshot-pwa-install-android-ru.png')
        capture_install_instructions(url, 'he', 'android', mobile_width, mobile_height,
                                    screenshots_dir / 'screenshot-pwa-install-android-he.png')
        capture_install_instructions(url, 'ru', 'ios', mobile_width, mobile_height,
                                    screenshots_dir / 'screenshot-pwa-install-ios-ru.png')
        capture_install_instructions(url, 'he', 'ios', mobile_width, mobile_height,
                                    screenshots_dir / 'screenshot-pwa-install-ios-he.png')
        
        # Talk page screenshots showing successful conversation
        capture_talk_page(url, 'ru', mobile_width, mobile_height,
                         screenshots_dir / 'screenshot-pwa-talk-ru.png')
        capture_talk_page(url, 'he', mobile_width, mobile_height,
                         screenshots_dir / 'screenshot-pwa-talk-he.png')
        
        print("\n✅ All PWA screenshots captured successfully!\n")
        
    finally:
        # Stop mock app
        print("Stopping mock app...")
        mock_proc.send_signal(signal.SIGTERM)
        mock_proc.wait(timeout=5)


if __name__ == '__main__':
    main()
