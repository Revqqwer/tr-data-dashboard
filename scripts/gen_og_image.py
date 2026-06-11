"""3N Finans için Twitter/OG paylaşım kartı üretir (1200x630)."""
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H = 1200, 630
BG_TOP = (244, 246, 250)
BG_BOTTOM = (227, 232, 241)
GOLD = (217, 142, 16)
GOLD_DIM = (217, 142, 16)
WHITE = (30, 38, 56)
MUTED = (110, 122, 145)
GREEN = (16, 160, 110)
GREEN_FILL = (205, 235, 224)
CARD_BG = (255, 255, 255)
CARD_BORDER = (224, 229, 238)

FONT_DIR = "C:/Windows/Fonts"

def font(path, size):
    return ImageFont.truetype(f"{FONT_DIR}/{path}", size)

f_logo = font("segoeuib.ttf", 80)
f_sub  = font("segoeuib.ttf", 80)
f_tag  = font("segoeui.ttf", 30)
f_url  = font("segoeui.ttf", 24)
f_pill = font("segoeuib.ttf", 22)
f_card = font("segoeuib.ttf", 24)
f_small = font("segoeui.ttf", 22)

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

# Soft glow accents (blurred overlay, alpha-composited once)
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gdraw = ImageDraw.Draw(glow)
gdraw.ellipse([W - 480, -240, W + 160, 280], fill=(240, 180, 41, 35))
gdraw.ellipse([-220, H - 260, 340, H + 240], fill=(16, 160, 110, 25))
glow = glow.filter(ImageFilter.GaussianBlur(100))
img = Image.alpha_composite(img, glow)

img = img.convert("RGB")
draw = ImageDraw.Draw(img)

# Top accent line
draw.rectangle([0, 0, W, 5], fill=GOLD)

# ── Left: wordmark ──
logo_x, logo_y = 80, 150
draw.text((logo_x, logo_y), "3N", font=f_logo, fill=GOLD)
bbox = draw.textbbox((logo_x, logo_y), "3N", font=f_logo)
draw.text((bbox[2] + 22, logo_y), "FINANS", font=f_sub, fill=WHITE)

draw.text((logo_x, logo_y + 120), "Türkiye'nin Finansal Veri Platformu", font=f_tag, fill=MUTED)
draw.text((logo_x, logo_y + 170), "TEFAS · BIST · TCMB · Global Piyasalar — tek ekranda.",
          font=f_small, fill=(140, 152, 175))

# ── Right: chart card ──
card_x0, card_y0, card_x1, card_y1 = 700, 130, 1120, 400
draw.rounded_rectangle([card_x0, card_y0, card_x1, card_y1], radius=18,
                        fill=CARD_BG, outline=CARD_BORDER, width=1)

draw.text((card_x0 + 28, card_y0 + 24), "BIST 100", font=f_card, fill=WHITE)
draw.text((card_x1 - 28 - draw.textlength("+2.4%", font=f_card), card_y0 + 24),
          "+2.4%", font=f_card, fill=GREEN)

# chart line (upward trend) with filled area
chart_left, chart_right = card_x0 + 28, card_x1 - 28
chart_bottom, chart_top = card_y1 - 36, card_y0 + 80
xs = [chart_left + i * (chart_right - chart_left) / 5 for i in range(6)]
ys_ratio = [0.65, 0.78, 0.55, 0.7, 0.35, 0.15]
points = [(xs[i], chart_top + ys_ratio[i] * (chart_bottom - chart_top)) for i in range(6)]

fill_poly = points + [(points[-1][0], chart_bottom), (points[0][0], chart_bottom)]
draw.polygon(fill_poly, fill=GREEN_FILL)
draw.line(points, fill=GREEN, width=4, joint="curve")
for p in points:
    draw.ellipse([p[0]-4, p[1]-4, p[0]+4, p[1]+4], fill=GREEN)

# ── Bottom: feature pills ──
pills = ["Global Piyasa Takip", "TEFAS Fon Akışları", "BIST Analizi", "Kripto ETF"]
px, py = 80, 460
for label in pills:
    tw = draw.textlength(label, font=f_pill)
    pad_x = 22
    w = tw + pad_x * 2
    h = 46
    draw.rounded_rectangle([px, py, px + w, py + h], radius=23,
                            outline=GOLD_DIM, width=1)
    draw.text((px + pad_x, py + 11), label, font=f_pill, fill=GOLD)
    px += w + 16

# ── Footer ──
draw.line([(80, H - 70), (W - 80, H - 70)], fill=(214, 220, 232), width=1)
draw.text((80, H - 52), "www.3nfinans.com", font=f_url, fill=WHITE)
footer_right = "Bağımsız Finansal Analiz"
tw = draw.textlength(footer_right, font=f_url)
draw.text((W - 80 - tw, H - 52), footer_right, font=f_url, fill=MUTED)

img.save("static/og-image.png", "PNG")
print("ok")
