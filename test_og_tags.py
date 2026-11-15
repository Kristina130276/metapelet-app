#!/usr/bin/env python3
"""Test that OG tags are present in the HTML and og-image is served."""

import sys
import re
from pathlib import Path

# Read the index.html file
html_path = Path(__file__).parent / "index.html"
html_content = html_path.read_text()

# Check for required OG tags
required_tags = {
    'og:title': r'<meta property="og:title" content="[^"]+"',
    'og:description': r'<meta property="og:description" content="[^"]+"',
    'og:image': r'<meta property="og:image" content="https://www\.metapelet\.org/static/og-image\.jpg"',
    'og:url': r'<meta property="og:url" content="https://www\.metapelet\.org/"',
    'twitter:card': r'<meta name="twitter:card" content="summary_large_image"',
    'description': r'<meta name="description" content="[^"]+"',
    'canonical': r'<link rel="canonical" href="https://www\.metapelet\.org/"',
}

all_passed = True
for tag_name, pattern in required_tags.items():
    if re.search(pattern, html_content):
        print(f"✅ {tag_name} found in HTML")
    else:
        print(f"❌ {tag_name} NOT found in HTML")
        all_passed = False

# Check that og-image file exists
og_image = Path(__file__).parent / "static" / "og-image.jpg"
if og_image.exists():
    print(f"✅ og-image.jpg exists ({og_image.stat().st_size} bytes)")
else:
    print(f"❌ og-image.jpg NOT found")
    all_passed = False

# Check that favicon exists
favicon = Path(__file__).parent / "static" / "favicon.png"
if favicon.exists():
    print(f"✅ favicon.png exists ({favicon.stat().st_size} bytes)")
else:
    print(f"❌ favicon.png NOT found")
    all_passed = False

if all_passed:
    print("\n✅ All OG tag checks passed!")
    sys.exit(0)
else:
    print("\n❌ Some checks failed")
    sys.exit(1)
