"""Generate Universal Remote brand icons.

Produces 4 PNGs with the exact specs required by home-assistant/brands and HACS:
- icon.png       256x256, transparent background
- icon@2x.png    512x512, transparent background
- logo.png       (variable width) x 256, transparent background
- logo@2x.png    (variable width) x 512, transparent background

Design: a stylised TV/remote silhouette with the four classic colour buttons
(red/green/yellow/blue) — visually communicates "remote control across vendors".
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "universal_remote" / "brand"
OUT_DIR.mkdir(exist_ok=True)

# Home Assistant palette
HA_BLUE = (3, 169, 244, 255)        # primary
HA_DARK = (24, 32, 41, 255)         # frame
WHITE   = (255, 255, 255, 255)
RED     = (244, 67, 54, 255)
GREEN   = (76, 175, 80, 255)
YELLOW  = (255, 193, 7, 255)
BLUE    = (33, 150, 243, 255)


def _rounded_rect(draw: ImageDraw.ImageDraw, box, radius, fill=None, outline=None, width=0):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def make_icon(size: int) -> Image.Image:
    """Square icon — stylised remote silhouette."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Outer rounded remote body (vertical pill).
    margin = int(size * 0.18)
    body_left, body_right = margin, size - margin
    body_top, body_bottom = int(size * 0.08), int(size * 0.92)
    body_radius = int(size * 0.18)
    _rounded_rect(
        d,
        (body_left, body_top, body_right, body_bottom),
        radius=body_radius,
        fill=HA_DARK,
    )

    # Top screen / power area (small accent).
    screen_top = int(size * 0.16)
    screen_bottom = int(size * 0.30)
    _rounded_rect(
        d,
        (body_left + int(size * 0.08), screen_top, body_right - int(size * 0.08), screen_bottom),
        radius=int(size * 0.04),
        fill=HA_BLUE,
    )

    # D-pad ring (large circle) below the screen.
    dpad_cx, dpad_cy = size // 2, int(size * 0.50)
    dpad_r = int(size * 0.16)
    d.ellipse(
        (dpad_cx - dpad_r, dpad_cy - dpad_r, dpad_cx + dpad_r, dpad_cy + dpad_r),
        fill=(60, 70, 80, 255),
    )
    # OK button in centre
    ok_r = int(size * 0.06)
    d.ellipse(
        (dpad_cx - ok_r, dpad_cy - ok_r, dpad_cx + ok_r, dpad_cy + ok_r),
        fill=WHITE,
    )

    # Four colour buttons in a row at the bottom.
    row_y_center = int(size * 0.76)
    btn_r = int(size * 0.045)
    btn_spacing = int(size * 0.13)
    start_x = size // 2 - int(btn_spacing * 1.5)
    for i, color in enumerate((RED, GREEN, YELLOW, BLUE)):
        cx = start_x + i * btn_spacing
        d.ellipse((cx - btn_r, row_y_center - btn_r, cx + btn_r, row_y_center + btn_r), fill=color)

    return img


def make_logo(height: int) -> Image.Image:
    """Horizontal logo: icon on the left, wordmark on the right.

    Width is computed to fit the text exactly.
    """
    text = "Universal Remote"
    font_size = int(height * 0.42)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Measure text first.
    tmp = Image.new("RGBA", (1, 1))
    bbox = ImageDraw.Draw(tmp).textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    gap = int(height * 0.10)
    width = height + gap + text_w + int(height * 0.1)  # icon + gap + text + right margin

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    icon = make_icon(height)
    img.paste(icon, (0, 0), icon)

    d = ImageDraw.Draw(img)
    text_x = height + gap - bbox[0]
    text_y = (height - text_h) // 2 - bbox[1]
    d.text((text_x, text_y), text, fill=HA_DARK, font=font)

    return img


def main() -> None:
    # Square icons
    make_icon(256).save(OUT_DIR / "icon.png", "PNG")
    make_icon(512).save(OUT_DIR / "icon@2x.png", "PNG")

    # Horizontal logos — width is computed automatically to fit the wordmark.
    make_logo(256).save(OUT_DIR / "logo.png", "PNG")
    make_logo(512).save(OUT_DIR / "logo@2x.png", "PNG")

    print("Generated:")
    for p in sorted(OUT_DIR.iterdir()):
        print(f"  {p.name}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
