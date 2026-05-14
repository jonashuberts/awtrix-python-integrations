import sys
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageOps

def _create_matrix_logo(size: int, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> Image.Image:
    # Scale based on size
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    # The matrix is 4 columns wide and 6 rows high.
    matrix_w = 4
    matrix_h = 6
    
    # Make the "A" logo fill more space (tick larger as requested)
    # We want it to be balanced. 
    pixel_size = max(1, size // 7) 
    
    grid_w = matrix_w * pixel_size
    grid_h = matrix_h * pixel_size
    
    offset_x = (size - grid_w) // 2
    offset_y = (size - grid_h) // 2
    
    matrix = [
        "1110",
        "1001",
        "1001",
        "1111",
        "1001",
        "1001",
    ]
    
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell == "1":
                left = offset_x + (x * pixel_size)
                top = offset_y + (y * pixel_size)
                draw.rectangle(
                    (left, top, left + pixel_size - 1, top + pixel_size - 1),
                    fill=color,
                )
    return image

def create_modern_icon(size: int = 1024) -> Image.Image:
    # Modern macOS icons use a specific squircle-like shape and subtle depth
    # We'll simulate this with a rounded rect, a subtle gradient, and a drop shadow.
    
    # 1. Background layer (for the shadow)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    
    # 2. Icon base (the "Squircle")
    # In macOS 11+, icons are 1024x1024 with a content area of ~824x824
    content_size = int(size * 0.82)
    padding = (size - content_size) // 2
    
    icon_base = Image.new("RGBA", (content_size, content_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_base)
    
    # Colors: Modern Deep Blue Gradient
    top_color = (0, 150, 255, 255)    # Lighter blue top
    bottom_color = (0, 80, 200, 255) # Darker blue bottom
    
    # Draw gradient (simulated)
    for y in range(content_size):
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * y / content_size)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * y / content_size)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * y / content_size)
        draw.line([(0, y), (content_size, y)], fill=(r, g, b, 255))
    
    # Mask to rounded rect (The macOS squircle is specific, but a radius of 0.2*size is close)
    mask = Image.new("L", (content_size, content_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, content_size, content_size), radius=int(content_size * 0.22), fill=255)
    
    # Apply mask
    icon_base.putalpha(mask)
    
    # 3. Add inner bevel/glow (subtle)
    glow = Image.new("RGBA", (content_size, content_size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.rounded_rectangle((2, 2, content_size-2, content_size-2), radius=int(content_size * 0.22), outline=(255, 255, 255, 40), width=4)
    icon_base.alpha_composite(glow)

    # 4. Draw the Matrix "A" Logo (Larger and with subtle shadow)
    logo_size = int(content_size * 0.65) # Make it larger
    logo = _create_matrix_logo(logo_size, (255, 255, 255, 255))
    
    # Simple shadow for the logo to make it "pop"
    logo_shadow = _create_matrix_logo(logo_size, (0, 0, 0, 80))
    icon_base.paste(logo_shadow, ((content_size - logo_size) // 2 + 2, (content_size - logo_size) // 2 + 4), logo_shadow)
    icon_base.paste(logo, ((content_size - logo_size) // 2, (content_size - logo_size) // 2), logo)
    
    # 5. Drop shadow for the whole icon
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((padding, padding+8, padding+content_size, padding+content_size+8), radius=int(content_size * 0.22), fill=(0, 0, 0, 60))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=15))
    
    # Combine everything
    canvas.alpha_composite(shadow)
    canvas.paste(icon_base, (padding, padding), icon_base)
    
    return canvas

if __name__ == "__main__":
    # Create the high-res 1024x1024 icon
    icon = create_modern_icon(1024)
    icon.save("app_icon.png")
    
    # Also create a small version for the tray (without background/shadow, as a template)
    # macOS tray icons should be black or white (template)
    tray = _create_matrix_logo(64, (255, 255, 255, 255))
    tray.save("tray_icon.png")
    
    print("Modern icons created: app_icon.png (1024x1024) and tray_icon.png (64x64)")
