import sys
from pathlib import Path
from PIL import Image, ImageDraw

def _create_matrix_logo(size: int, color: tuple[int, int, int, int] = (255, 255, 255, 255)) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    matrix_w = 4
    matrix_h = 6
    
    # Balanced size (approx 65% of the canvas)
    pixel_size = max(1, int(size * 0.65 // 6)) # Using 6 rows as the height scale
    
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

if __name__ == "__main__":
    # Create the high-res icon: White 'A' logo, transparent background.
    size = 1024
    icon = _create_matrix_logo(size, (255, 255, 255, 255))
    icon.save("app_icon.png")
    
    # Tray icon (Template white)
    tray = _create_matrix_logo(64, (255, 255, 255, 255))
    tray.save("tray_icon.png")
    
    print(f"Minimalist white icons created: app_icon.png ({size}x{size}) and tray_icon.png (64x64)")
