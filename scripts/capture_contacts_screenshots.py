#!/usr/bin/env python3
"""Capture screenshots of the contacts section on mobile."""

import os
import time
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
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(width, height)
    return driver

def capture_contacts(lang_code, width, filename):
    """Capture screenshot of contacts section."""
    print(f"Capturing {lang_code.upper()} contacts section at {width}px...")
    driver = setup_driver(width, 1400)
    
    try:
        # Load page
        driver.get('http://localhost:5000')
        time.sleep(2)
        
        # Switch to language
        if lang_code != 'ru':
            wait = WebDriverWait(driver, 10)
            lang_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'button.lang-btn[data-lang="{lang_code}"]'))
            )
            lang_btn.click()
            time.sleep(1)
        
        # Scroll to contacts section
        contact_section = driver.find_element(By.CSS_SELECTOR, '.contact-section')
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", contact_section)
        time.sleep(0.5)
        
        # Take screenshot
        screenshot_path = f'/workspace/static/{filename}'
        contact_section.screenshot(screenshot_path)
        print(f"  ✅ Saved to {screenshot_path}")
        
    finally:
        driver.quit()

def main():
    # Capture Russian and Hebrew at both 320px and 390px
    captures = [
        ('ru', 320, 'screenshot-contacts-mobile-320-ru.png'),
        ('he', 320, 'screenshot-contacts-mobile-320-he.png'),
        ('ru', 390, 'screenshot-contacts-mobile-390-ru.png'),
        ('he', 390, 'screenshot-contacts-mobile-390-he.png'),
    ]
    
    for lang, width, filename in captures:
        capture_contacts(lang, width, filename)
    
    print("\n✅ All contacts screenshots captured!")

if __name__ == "__main__":
    main()
