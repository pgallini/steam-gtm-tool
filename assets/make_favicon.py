from PIL import Image
from pathlib import Path

source = Path("android-chrome-512x512.png")  # change if needed
output = Path("favicon.ico")

img = Image.open(source).convert("RGBA")

sizes = [(16, 16), (32, 32), (48, 48)]

img.save(
    output,
    format="ICO",
    sizes=sizes
)

print(f"Created {output}")