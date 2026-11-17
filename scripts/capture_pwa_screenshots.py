#!/usr/bin/env python3
"""Capture screenshots of PWA setup and talk pages for elderly users."""

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
    """Capture talk page showing the elder-friendly UI."""
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
        time.sleep(3)
        
        # Simulate showing a reply
        driver.execute_script("""
            document.getElementById('status').textContent = '';
            document.getElementById('reply-box').textContent = 
                document.documentElement.dir === 'rtl' 
                ? 'איך הרגשת היום? אשמח לשמוע איך עבר עליך היום.'
                : 'Как вы себя чувствуете сегодня? Расскажите, как прошёл ваш день.';
            document.getElementById('reply-box').classList.add('visible');
        """)
        
        time.sleep(1)
        driver.save_screenshot(filename)
        print(f"✓ Saved {filename}")
        
    finally:
        driver.quit()


def main():
    """Capture all PWA screenshots."""
    url = os.environ.get('APP_URL', 'http://localhost:5000')
    static_dir = Path(__file__).parent / 'static'
    static_dir.mkdir(exist_ok=True)
    
    # Mobile viewport (390x844 is iPhone 12/13/14 size, 360x800 is typical Android)
    mobile_width = 390
    mobile_height = 844
    
    print(f"\n📱 Capturing PWA screenshots from {url}\n")
    
    # Setup page screenshots
    capture_setup_page(url, 'ru', mobile_width, mobile_height, 
                      static_dir / 'screenshot-pwa-setup-ru.png')
    capture_setup_page(url, 'he', mobile_width, mobile_height, 
                      static_dir / 'screenshot-pwa-setup-he.png')
    
    # Install instructions
    capture_install_instructions(url, 'ru', 'android', mobile_width, mobile_height,
                                static_dir / 'screenshot-pwa-install-android-ru.png')
    capture_install_instructions(url, 'he', 'android', mobile_width, mobile_height,
                                static_dir / 'screenshot-pwa-install-android-he.png')
    capture_install_instructions(url, 'ru', 'ios', mobile_width, mobile_height,
                                static_dir / 'screenshot-pwa-install-ios-ru.png')
    capture_install_instructions(url, 'he', 'ios', mobile_width, mobile_height,
                                static_dir / 'screenshot-pwa-install-ios-he.png')
    
    # Talk page screenshots
    capture_talk_page(url, 'ru', mobile_width, mobile_height,
                     static_dir / 'screenshot-pwa-talk-ru.png')
    capture_talk_page(url, 'he', mobile_width, mobile_height,
                     static_dir / 'screenshot-pwa-talk-he.png')
    
    print("\n✅ All PWA screenshots captured successfully!\n")


if __name__ == '__main__':
    main()
