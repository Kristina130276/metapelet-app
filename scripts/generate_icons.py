#!/usr/bin/env python3
"""Generate clean PWA icons for MetaPelet"""

from PIL import Image, ImageDraw, ImageFont

def create_icon(size, output_path):
    """Create a simple, clean icon with M letter on gradient background"""
    # Create image with gradient background (yellow-gold gradient)
    img = Image.new('RGB', (size, size), '#ffd966')
    draw = ImageDraw.Draw(img)
    
    # Draw gradient-like effect with circles
    for i in range(10):
        alpha = int(255 * (1 - i/10))
        color = f'#{int(244 - i*5):02x}{int(185 - i*5):02x}{int(66):02x}'
        # Convert hex to RGB
        r = int(color[1:3], 16)
        g = int(color[3:5], 16)
        b = int(color[5:7], 16)
        draw.ellipse([i*size//20, i*size//20, size-i*size//20, size-i*size//20], 
                     fill=(r, g, b))
    
    # Draw rounded square for app icon look
    margin = size // 8
    corner_radius = size // 6
    draw.rounded_rectangle(
        [margin, margin, size-margin, size-margin],
        radius=corner_radius,
        fill='#f4b942'
    )
    
    # Draw letter M
    try:
        font_size = int(size * 0.55)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", int(size * 0.55))
        except:
            font = ImageFont.load_default()
    
    text = "M"
    # Get text bounding box
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    
    # Center the text
    x = (size - text_width) // 2 - bbox[0]
    y = (size - text_height) // 2 - bbox[1]
    
    # Draw text with shadow for depth
    shadow_offset = max(2, size // 100)
    draw.text((x + shadow_offset, y + shadow_offset), text, fill='#3d2f1a', font=font)
    draw.text((x, y), text, fill='#4a3b1f', font=font)
    
    # Save with high quality
    img.save(output_path, 'PNG', optimize=True)
    print(f"Created {output_path}")

if __name__ == '__main__':
    create_icon(192, 'static/icon-192.png')
    create_icon(512, 'static/icon-512.png')
    create_icon(180, 'static/apple-touch-icon.png')
    print("All icons generated successfully!")
