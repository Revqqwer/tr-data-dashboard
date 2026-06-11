"""3N Finans — yeni animasyonlu landing page temasıyla Twitter/OG kartı (1200x630)."""
import math, random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
random.seed(11)

BG_TOP    = (6, 9, 18)
BG_BOTTOM = (10, 16, 32)
GOLD      = (240, 180, 41)
GOLD_SOFT = (245, 217, 138)
GREEN     = (52, 211, 153)
BLUE      = (96, 165, 250)
WHITE     = (240, 244, 252)
MUTED     = (122, 134, 158)

FONT_DIR = "C:/Windows/Fonts"
def font(path, size):
    return ImageFont.truetype(f"{FONT_DIR}/{path}", size)

f_logo  = font("segoeuib.ttf", 54)
f_eyebrow = font("segoeuib.ttf", 20)
f_h1    = font("seguisb.ttf", 64)
f_h1i   = font("seguisbi.ttf", 64)
f_sub   = font("segoeui.ttf", 24)
f_url   = font("segoeui.ttf", 22)
f_stat  = font("seguisb.ttf", 36)
f_statlbl = font("segoeui.ttf", 16)

# ── Background gradient ──
img = Image.new("RGB", (W, H), BG_TOP)
draw = ImageDraw.Draw(img)
for y in range(H):
    t = y / H
    r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
    g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
    b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
    draw.line([(0, y), (W, y)], fill=(r, g, b))

img = img.convert("RGBA")

# ── Glow blobs (gold top-right, green bottom-left, blue center) ──
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gdraw = ImageDraw.Draw(glow)
gdraw.ellipse([W - 460, -240, W + 220, 320], fill=(*GOLD, 45))
gdraw.ellipse([-260, H - 360, 360, H + 220], fill=(*GREEN, 35))
gdraw.ellipse([W * 0.45, H * 0.15, W * 0.45 + 480, H * 0.15 + 480], fill=(*BLUE, 22))
glow = glow.filter(ImageFilter.GaussianBlur(110))
img = Image.alpha_composite(img, glow)

# ── Particle field ──
particles = Image.new("RGBA", (W, H), (0, 0, 0, 0))
pdraw = ImageDraw.Draw(particles)
for _ in range(90):
    x, y = random.randint(0, W), random.randint(0, H - 120)
    r = random.uniform(0.8, 2.2)
    if random.random() < 0.18:
        c = (*GOLD, random.randint(90, 180))
    else:
        c = (120, 160, 230, random.randint(40, 110))
    pdraw.ellipse([x-r, y-r, x+r, y+r], fill=c)
img = Image.alpha_composite(img, particles)

# ── Faint grid (masked toward center) ──
grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
grdraw = ImageDraw.Draw(grid)
for gx in range(0, W, 64):
    grdraw.line([(gx, 0), (gx, H)], fill=(96, 140, 220, 14), width=1)
for gy in range(0, H, 64):
    grdraw.line([(0, gy), (W, gy)], fill=(96, 140, 220, 14), width=1)
mask = Image.new("L", (W, H), 0)
mdraw = ImageDraw.Draw(mask)
mdraw.ellipse([-200, -250, W + 200, H + 100], fill=140)
grid.putalpha(Image.composite(grid.split()[3], Image.new("L", (W, H), 0), mask))
img = Image.alpha_composite(img, grid)

img = img.convert("RGB")
draw = ImageDraw.Draw(img, "RGBA")

# ── Decorative globe (right side) ──
gx, gy, gr = 990, 300, 230
draw.ellipse([gx-gr, gy-gr, gx+gr, gy+gr], outline=(120, 150, 220, 90), width=2)
for ry_ratio in (0.3, 0.6, 0.85):
    ry = gr * ry_ratio
    draw.ellipse([gx-gr, gy-ry, gx+gr, gy+ry], outline=(120, 150, 220, 50), width=1)
for rx_ratio in (0.3, 0.6, 0.85):
    rx = gr * rx_ratio
    draw.ellipse([gx-rx, gy-gr, gx+rx, gy+gr], outline=(120, 150, 220, 50), width=1)
draw.line([(gx-gr, gy), (gx+gr, gy)], fill=(120, 150, 220, 50), width=1)
draw.line([(gx, gy-gr), (gx, gy+gr)], fill=(120, 150, 220, 50), width=1)

# Istanbul marker (gold) + a couple of blue city markers
city_markers = [(gx - 40, gy - 70, GOLD, 8), (gx + 110, gy - 30, BLUE, 5),
                (gx + 60, gy + 110, BLUE, 5), (gx - 150, gy + 60, BLUE, 5)]
glow2 = Image.new("RGBA", (W, H), (0, 0, 0, 0))
g2draw = ImageDraw.Draw(glow2)
for mx, my, c, _ in city_markers:
    g2draw.ellipse([mx-20, my-20, mx+20, my+20], fill=(*c, 90))
glow2 = glow2.filter(ImageFilter.GaussianBlur(16))
img2 = Image.alpha_composite(img.convert("RGBA"), glow2).convert("RGB")
img.paste(img2)
draw = ImageDraw.Draw(img, "RGBA")
for mx, my, c, rr in city_markers:
    draw.ellipse([mx-rr/2, my-rr/2, mx+rr/2, my+rr/2], fill=(*c, 230))

# ── Self-drawing trend line across bottom ──
trend_pts = []
n = 40
for i in range(n + 1):
    x = i * (W / n)
    drift = -(H * 0.16) * (i / n)
    wave = math.sin(i * 0.55) * 16 + math.sin(i * 1.3) * 8
    y = H * 0.92 + drift + wave
    trend_pts.append((x, y))

trend_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
tdraw = ImageDraw.Draw(trend_layer)
poly = trend_pts + [(W, H), (0, H)]
ag_top = (*GREEN, 45)
tdraw.polygon(poly, fill=(*GREEN, 26))
tdraw.line(trend_pts, fill=(*GREEN, 200), width=6, joint="curve")
glow_line = trend_layer.filter(ImageFilter.GaussianBlur(8))
img2 = Image.alpha_composite(img.convert("RGBA"), glow_line).convert("RGB")
img.paste(img2)

sharp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
sdraw = ImageDraw.Draw(sharp)
sdraw.line(trend_pts, fill=(*GREEN, 255), width=3, joint="curve")
img3 = Image.alpha_composite(img.convert("RGBA"), sharp).convert("RGB")
img.paste(img3)

draw = ImageDraw.Draw(img, "RGBA")
ex, ey = trend_pts[-1]
ex -= 8
draw.ellipse([ex-7, ey-7, ex+7, ey+7], fill=(*GOLD, 255))
draw.ellipse([ex-15, ey-15, ex+15, ey+15], outline=(*GOLD, 150), width=2)

# ── Top accent line ──
draw.rectangle([0, 0, W, 4], fill=GOLD)

# ── Logo ──
logo_x, logo_y = 80, 64
draw.text((logo_x, logo_y), "3N", font=f_logo, fill=GOLD)
bbox = draw.textbbox((logo_x, logo_y), "3N", font=f_logo)
draw.text((bbox[2] + 16, logo_y), "FİNANS", font=f_logo, fill=WHITE)

# ── Eyebrow with live dot ──
eb_y = logo_y + 92
draw.ellipse([logo_x, eb_y + 8, logo_x + 10, eb_y + 18], fill=GREEN)
draw.text((logo_x + 22, eb_y), "GLOBAL PİYASALAR TAKİP  ·  CANLI VERİ", font=f_eyebrow, fill=GOLD)

# ── Headline ──
h1_y = eb_y + 50
draw.text((logo_x, h1_y), "Türkiye'nin finansal", font=f_h1, fill=WHITE)
draw.text((logo_x, h1_y + 76), "verisini ", font=f_h1, fill=WHITE)
bbox2 = draw.textbbox((logo_x, h1_y + 76), "verisini ", font=f_h1)
draw.text((bbox2[2], h1_y + 76), "tek ekranda", font=f_h1i, fill=GOLD_SOFT)
bbox3 = draw.textbbox((bbox2[2], h1_y + 76), "tek ekranda", font=f_h1i)
draw.text((bbox3[2], h1_y + 76), " görün.", font=f_h1, fill=WHITE)

# ── Subtitle ──
sub_y = h1_y + 76 + 90
draw.text((logo_x, sub_y), "TEFAS fon akışları · TCMB rezervleri · BIST endeksleri · Global piyasalar",
          font=f_sub, fill=MUTED)

# ── Stats row ──
stats = [("800+", "FON TAKİBİ"), ("44", "GLOBAL ENSTRÜMAN"), ("12", "MAKRO VERİ SETİ")]
sx = logo_x
sy = sub_y + 64
for val, lbl in stats:
    draw.text((sx, sy), val, font=f_stat, fill=WHITE)
    draw.text((sx, sy + 48), lbl, font=f_statlbl, fill=MUTED)
    w = draw.textlength(val, font=f_stat)
    sx += max(w, draw.textlength(lbl, font=f_statlbl)) + 70

# ── Footer ──
draw.line([(80, H - 56), (W - 80, H - 56)], fill=(255, 255, 255, 22), width=1)
draw.text((80, H - 40), "www.3nfinans.com", font=f_url, fill=WHITE)

img.convert("RGB").save("static/og-image-v2-preview.png", "PNG")
print("ok")
