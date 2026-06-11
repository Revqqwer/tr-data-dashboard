"""3N Finans için artistik Twitter/OG paylaşım kartı üretir (1200x630)."""
import math, random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
random.seed(7)

BG_TOP    = (8, 12, 26)
BG_BOTTOM = (16, 22, 46)
GOLD      = (245, 190, 70)
GREEN     = (52, 211, 153)
RED       = (244, 110, 120)
BLUE      = (96, 165, 250)
WHITE     = (240, 244, 252)
MUTED     = (148, 163, 196)

FONT_DIR = "C:/Windows/Fonts"
def font(path, size):
    return ImageFont.truetype(f"{FONT_DIR}/{path}", size)

f_logo  = font("segoeuib.ttf", 86)
f_tag   = font("segoeui.ttf", 30)
f_pill  = font("segoeuib.ttf", 25)
f_pillsub = font("segoeui.ttf", 19)
f_url   = font("segoeui.ttf", 24)

# ── Background gradient (deep navy -> indigo) ──
img = Image.new("RGB", (W, H), BG_TOP)
draw = ImageDraw.Draw(img)
for y in range(H):
    t = y / H
    r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
    g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
    b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
    draw.line([(0, y), (W, y)], fill=(r, g, b))

img = img.convert("RGBA")

# ── Glow accents ──
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gdraw = ImageDraw.Draw(glow)
gdraw.ellipse([W - 480, -260, W + 200, 300], fill=(245, 190, 70, 55))   # gold top-right
gdraw.ellipse([-260, H - 380, 380, H + 220], fill=(52, 211, 153, 40))   # green bottom-left
gdraw.ellipse([W * 0.35, H * 0.35, W * 0.35 + 500, H * 0.35 + 500], fill=(96, 165, 250, 22))  # blue center
glow = glow.filter(ImageFilter.GaussianBlur(110))
img = Image.alpha_composite(img, glow)

# ── World-map dot grid (artistic, faint) ──
dots = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ddraw = ImageDraw.Draw(dots)
for gx in range(0, W, 26):
    for gy in range(60, H - 120, 26):
        # rough silhouette mask: a few "continent" blobs
        d1 = (gx - 280)**2 / 2.4 + (gy - 230)**2
        d2 = (gx - 760)**2 / 1.6 + (gy - 180)**2
        d3 = (gx - 980)**2 / 2.0 + (gy - 380)**2
        d4 = (gx - 420)**2 / 1.2 + (gy - 420)**2
        if min(d1, d2, d3, d4) < 9000 and random.random() > 0.25:
            ddraw.ellipse([gx-2, gy-2, gx+2, gy+2], fill=(120, 150, 220, 70))
img = Image.alpha_composite(img, dots)

img = img.convert("RGB")
draw = ImageDraw.Draw(img, "RGBA")

# ── Decorative globe (right side, behind content) ──
gx, gy, gr = 980, 320, 220
draw.ellipse([gx-gr, gy-gr, gx+gr, gy+gr], outline=(120, 150, 220, 90), width=2)
# latitude ellipses
for ry_ratio in (0.3, 0.6, 0.85):
    ry = gr * ry_ratio
    draw.ellipse([gx-gr, gy-ry, gx+gr, gy+ry], outline=(120, 150, 220, 55), width=1)
# longitude ellipses (vertical)
for rx_ratio in (0.3, 0.6, 0.85):
    rx = gr * rx_ratio
    draw.ellipse([gx-rx, gy-gr, gx+rx, gy+gr], outline=(120, 150, 220, 55), width=1)
draw.line([(gx-gr, gy), (gx+gr, gy)], fill=(120, 150, 220, 55), width=1)
draw.line([(gx, gy-gr), (gx, gy+gr)], fill=(120, 150, 220, 55), width=1)

# rising trend line + area fill inside the globe (clipped to circle)
trend_pts = [
    (gx - gr + 30, gy + 70),
    (gx - 110, gy + 30),
    (gx - 50, gy + 60),
    (gx + 10, gy - 20),
    (gx + 80, gy - 10),
    (gx + 140, gy - 90),
    (gx + gr - 40, gy - 130),
]

# clip mask = globe circle
clip = Image.new("L", (W, H), 0)
cdraw = ImageDraw.Draw(clip)
cdraw.ellipse([gx-gr, gy-gr, gx+gr, gy+gr], fill=255)

trend_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
tdraw = ImageDraw.Draw(trend_layer)
# area under the line (to globe bottom)
poly = trend_pts + [(gx + gr - 40, gy + gr), (gx - gr + 30, gy + gr)]
tdraw.polygon(poly, fill=(*GREEN, 28))
# glow for the line
tdraw.line(trend_pts, fill=(*GREEN, 160), width=8, joint="curve")
glow_line = trend_layer.filter(ImageFilter.GaussianBlur(10))
img2 = Image.alpha_composite(img.convert("RGBA"), Image.composite(glow_line, Image.new("RGBA", (W, H), (0,0,0,0)), clip)).convert("RGB")
img.paste(img2)

# crisp line + endpoint marker on top
sharp_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sdraw = ImageDraw.Draw(sharp_layer)
sdraw.line(trend_pts, fill=(*GREEN, 255), width=4, joint="curve")
img3 = Image.alpha_composite(img.convert("RGBA"), Image.composite(sharp_layer, Image.new("RGBA", (W, H), (0,0,0,0)), clip)).convert("RGB")
img.paste(img3)
draw = ImageDraw.Draw(img, "RGBA")
ex, ey = trend_pts[-1]
draw.ellipse([ex-7, ey-7, ex+7, ey+7], fill=(*GOLD, 255))
draw.ellipse([ex-14, ey-14, ex+14, ey+14], outline=(*GOLD, 150), width=2)

# ── Top accent line ──
draw.rectangle([0, 0, W, 5], fill=GOLD)

# ── Logo / wordmark ──
logo_x, logo_y = 80, 70
draw.text((logo_x, logo_y), "3N", font=f_logo, fill=GOLD)
bbox = draw.textbbox((logo_x, logo_y), "3N", font=f_logo)
draw.text((bbox[2] + 22, logo_y), "FINANS", font=f_logo, fill=WHITE)

draw.text((logo_x, logo_y + 110), "Global Piyasalar Takip", font=f_tag, fill=MUTED)

# ── Feature cards ──
features = [
    ("🌍", "Yurt Dışı & Yurt İçi Hisse Senedi Analizi",
           "ABD, Avrupa ve BIST piyasalarını aynı ekrandan izleyin"),
    ("💸", "TEFAS Fon Para Giriş / Çıkışları",
           "800+ yatırım fonunda günlük net akış takibi"),
]

card_x0 = 80
card_y = 280
card_w = 740
card_h = 110
gap = 26

for i, (emoji, title, sub) in enumerate(features):
    y0 = card_y + i * (card_h + gap)
    y1 = y0 + card_h
    # glassy card
    draw.rounded_rectangle([card_x0, y0, card_x0 + card_w, y1], radius=16,
                            fill=(255, 255, 255, 14), outline=(255, 255, 255, 35), width=1)
    # accent bar
    draw.rounded_rectangle([card_x0, y0, card_x0 + 6, y1], radius=3, fill=GOLD)
    # emoji circle
    cx, cy, r = card_x0 + 56, (y0 + y1) // 2, 28
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], fill=(245, 190, 70, 35))
    draw.text((cx, cy), emoji, font=font("seguiemj.ttf", 30), anchor="mm")
    # text
    draw.text((card_x0 + 100, y0 + 22), title, font=f_pill, fill=WHITE)
    draw.text((card_x0 + 100, y0 + 60), sub, font=f_pillsub, fill=MUTED)

# ── Footer ──
draw.line([(80, H - 64), (W - 80, H - 64)], fill=(255, 255, 255, 25), width=1)
draw.text((80, H - 46), "www.3nfinans.com", font=f_url, fill=WHITE)

img.convert("RGB").save("static/og-image.png", "PNG")
print("ok")
