#!/usr/bin/env python3
"""Test mobile layout - verify language buttons are correctly positioned."""

import sys
import os

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
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(width, height)
    return driver

def test_mobile_buttons(url, lang_code, width, height):
    """Test that mobile language buttons are correctly positioned."""
    print(f"\nTesting {lang_code.upper()} at {width}x{height}...")
    driver = setup_driver(width, height)
    
    try:
        # Load page
        driver.get(url)
        import time
        time.sleep(2)
        
        # Switch to the language
        if lang_code != 'ru':
            wait = WebDriverWait(driver, 10)
            lang_btn = wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, f'button.lang-btn[data-lang="{lang_code}"]'))
            )
            lang_btn.click()
            time.sleep(1)
        
        # Find all three buttons
        buttons = driver.find_elements(By.CSS_SELECTOR, 'button.lang-btn')
        assert len(buttons) == 3, f"Expected 3 buttons, found {len(buttons)}"
        
        # Get bounding boxes
        positions = []
        for btn in buttons:
            rect = btn.rect
            positions.append({
                'text': btn.text,
                'x': rect['x'],
                'y': rect['y'],
                'width': rect['width'],
                'height': rect['height'],
                'right': rect['x'] + rect['width'],
                'bottom': rect['y'] + rect['height']
            })
        
        # Check 1: All buttons must be visible (within viewport)
        for i, pos in enumerate(positions):
            assert pos['x'] >= 0, f"Button {i} ({pos['text']}) clipped on left: x={pos['x']}"
            assert pos['y'] >= 0, f"Button {i} ({pos['text']}) clipped on top: y={pos['y']}"
            assert pos['right'] <= width, f"Button {i} ({pos['text']}) clipped on right: right={pos['right']}, viewport={width}"
            assert pos['bottom'] <= height, f"Button {i} ({pos['text']}) clipped on bottom: bottom={pos['bottom']}, viewport={height}"
            print(f"  ✅ Button {i} ({pos['text']}): x={pos['x']:.1f}, y={pos['y']:.1f}, width={pos['width']:.1f}")
        
        # Check 2: All buttons must have the same top value (horizontal alignment)
        top_values = [pos['y'] for pos in positions]
        max_diff = max(top_values) - min(top_values)
        assert max_diff < 5, f"Buttons not horizontally aligned: tops={top_values}, max_diff={max_diff:.1f}px"
        print(f"  ✅ All buttons horizontally aligned (max diff: {max_diff:.1f}px)")
        
        # Check 3: Buttons must be side by side (not overlapping, not stacked)
        # Sort by x position
        sorted_positions = sorted(positions, key=lambda p: p['x'])
        for i in range(len(sorted_positions) - 1):
            left_btn = sorted_positions[i]
            right_btn = sorted_positions[i + 1]
            
            # Check they don't overlap horizontally
            assert left_btn['right'] <= right_btn['x'] + 2, \
                f"Buttons overlap: {left_btn['text']} right={left_btn['right']}, {right_btn['text']} x={right_btn['x']}"
            
            # Check they're reasonably close (not too far apart, which would indicate stacking)
            gap = right_btn['x'] - left_btn['right']
            assert gap < 50, f"Buttons too far apart (stacked?): gap={gap:.1f}px between {left_btn['text']} and {right_btn['text']}"
        
        print(f"  ✅ Buttons side by side in one row")
        
        # Check 4: All buttons are near the top of the page
        avg_top = sum(pos['y'] for pos in positions) / len(positions)
        assert avg_top < 80, f"Buttons not at top of page: avg_top={avg_top:.1f}px"
        print(f"  ✅ Buttons at top of page (y={avg_top:.1f}px)")
        
        return True
        
    except AssertionError as e:
        print(f"  ❌ FAILED: {e}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        driver.quit()

def main():
    base_url = "http://localhost:5000"
    
    # Test that server is running
    import urllib.request
    try:
        urllib.request.urlopen(base_url, timeout=5)
    except:
        print("❌ Server not running at http://localhost:5000")
        print("Start server first: python3 app.py")
        return 1
    
    all_passed = True
    
    # Test cases: (lang, width, height)
    test_cases = [
        ('ru', 375, 812),
        ('he', 375, 812),
        ('en', 375, 812),
        ('he', 320, 568),
        ('ru', 320, 568),
    ]
    
    for lang, width, height in test_cases:
        passed = test_mobile_buttons(base_url, lang, width, height)
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n✅ All mobile layout tests passed!")
        return 0
    else:
        print("\n❌ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())
