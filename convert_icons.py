import os
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM

# Directory containing your SVGs
source_dir = "assets"
target_size = 512

print(f"🔄 Converting SVGs in '{source_dir}' to {target_size}px PNG...")

count = 0
for filename in os.listdir(source_dir):
    if filename.endswith(".svg"):
        svg_path = os.path.join(source_dir, filename)
        png_filename = filename.replace(".svg", ".png")
        png_path = os.path.join(source_dir, png_filename)
        
        try:
            # Load SVG
            drawing = svg2rlg(svg_path)
            
            # Calculate Scale Factor to hit 512px
            # We take the larger dimension to ensure fit
            sx = target_size / drawing.width
            sy = target_size / drawing.height
            scale = min(sx, sy) # Maintain aspect ratio
            
            drawing.width = drawing.width * scale
            drawing.height = drawing.height * scale
            drawing.scale(scale, scale)
            
            # Render to PNG
            renderPM.drawToFile(drawing, png_path, fmt="PNG")
            count += 1
            print(f"✅ Converted HD: {png_filename}")
        except Exception as e:
            print(f"❌ Failed: {filename} ({e})")

print(f"\n🎉 Done! Converted {count} icons to High Res.")