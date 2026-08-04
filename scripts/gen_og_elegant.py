# -*- coding: utf-8 -*-
"""3N Finans — klasik & zarif Open Graph / sosyal medya kartı (1200x630).

Tasarım dili: derin lacivert zemin, antik altın ince çift çerçeve, zarif serif
marka, bol negatif alan, ölçülü finans dokunuşu (ince altın trend çizgisi).
Kalite için 2x supersampling → LANCZOS küçültme.
"""
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

S = 2                      # supersampling
W, H = 1200 * S, 630 * S

# ── Palet (antik altın + fildişi + derin lacivert) ──
BG_TOP  = (10, 16, 30)
BG_BOT  = (14, 24, 43)
GOLD    = (200, 164, 78)
GOLD_HI = (226, 197, 128)
IVORY   = (238, 232, 221)
MUTED   = (146, 157, 178)

FONT_DIR = "C:/Windows/Fonts"
def font(names, size):
    """İlk bulunan fontu yükle (zarif serif tercihi)."""
    for n in names:
        try:
            return ImageFont.truetype(f"{FONT_DIR}/{n}", int(size * S))
        except OSError:
            continue
    return ImageFont.load_default()

SERIF   = ["pala.ttf", "georgia.ttf", "times.ttf"]        # gövde serif
SERIF_B = ["palab.ttf", "georgiab.ttf", "timesbd.ttf"]    # kalın serif
SERIF_I = ["palai.ttf", "georgiai.ttf", "timesi.ttf"]     # italik serif
SANS    = ["segoeui.ttf", "arial.ttf"]

f_brand  = font(SERIF_B, 104)
f_brand2 = font(SERIF,   104)
f_tag    = font(SANS,    20)
f_sub    = font(SERIF_I, 26)
f_url    = font(SANS,    19)

# ── Zemin: dikey degrade + hafif merkez ışıması ──
img = Image.new("RGB", (W, H), BG_TOP)
d = ImageDraw.Draw(img)
for y in range(H):
    t = y / H
    d.line([(0, y), (W, y)], fill=tuple(int(BG_TOP[i] + (BG_BOT[i]-BG_TOP[i])*t) for i in range(3)))
img = img.convert("RGBA")

glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse([W*0.5-500*S, H*0.5-340*S, W*0.5+500*S, H*0.5+340*S], fill=(*GOLD, 18))
glow = glow.filter(ImageFilter.GaussianBlur(170*S))
img = Image.alpha_composite(img, glow)

# ── Yumuşak vinyet (elips maske, geniş blur → köşelerde sertlik yok) ──
mask = Image.new("L", (W, H), 0)
md = ImageDraw.Draw(mask)
md.ellipse([-W*0.06, -H*0.10, W*1.06, H*1.10], fill=255)
mask = mask.filter(ImageFilter.GaussianBlur(180*S))
dark = Image.new("RGBA", (W, H), (5, 9, 18, 255))
dark.putalpha(Image.eval(mask, lambda p: int((255 - p) * 0.55)))
img = Image.alpha_composite(img, dark)
img = img.convert("RGB")

CX = W // 2

# ── Ölçülü finans dokunuşu: alt bölgede ince altın trend çizgisi ──
pts = []
n = 44
for i in range(n + 1):
    x = i * (W / n)
    y = H*0.862 - (H*0.070)*(i/n) + math.sin(i*0.55)*6.5*S + math.sin(i*1.6)*3*S
    pts.append((x, y))

# 1) çizginin altına çok hafif altın ışıma (dolgu yok — "duman" etkisi olmasın)
tglow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(tglow).line(pts, fill=(*GOLD, 70), width=max(1, 5*S), joint="curve")
tglow = tglow.filter(ImageFilter.GaussianBlur(7*S))
img = Image.alpha_composite(img.convert("RGBA"), tglow)

# 2) net, ince altın çizgi
tsharp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
ImageDraw.Draw(tsharp).line(pts, fill=(*GOLD_HI, 120), width=max(1, 2*S), joint="curve")
img = Image.alpha_composite(img, tsharp).convert("RGB")
draw = ImageDraw.Draw(img, "RGBA")

# ── Zarif çift çerçeve + köşe elmasları ──
m = 46 * S
draw.rectangle([m, m, W-m, H-m], outline=(*GOLD, 155), width=max(1, 2*S))
m2 = 54 * S
draw.rectangle([m2, m2, W-m2, H-m2], outline=(*GOLD, 58), width=max(1, 1*S))
for cx, cy in [(m, m), (W-m, m), (m, H-m), (W-m, H-m)]:
    r = 6*S
    draw.polygon([(cx, cy-r), (cx+r, cy), (cx, cy+r), (cx-r, cy)], fill=(*GOLD_HI, 225))

def tracked(y, text, fnt, fill, tracking, cx=None):
    """Harf aralıklı, yatay ortalı metin. Döner: (sol_x, toplam_genişlik)."""
    cx = CX if cx is None else cx
    tr = tracking * S
    widths = [draw.textlength(ch, font=fnt) for ch in text]
    total = sum(widths) + tr*(len(text)-1)
    x = cx - total/2
    for ch, w in zip(text, widths):
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += w + tr
    return cx - total/2, total

# ═══ Dikey kompozisyon (optik merkez: hafif yukarı) ═══
oy = int(H*0.222)                      # üst süs
# kısa çizgi — elmas — kısa çizgi
seg = 58*S; gap = 17*S; dsz = 5*S
draw.line([(CX-seg-gap-dsz, oy), (CX-gap-dsz, oy)], fill=(*GOLD, 165), width=max(1,1*S))
draw.line([(CX+gap+dsz, oy), (CX+seg+gap+dsz, oy)], fill=(*GOLD, 165), width=max(1,1*S))
draw.polygon([(CX, oy-dsz), (CX+dsz, oy), (CX, oy+dsz), (CX-dsz, oy)], fill=(*GOLD_HI, 235))

# üst etiket
tracked(int(H*0.252), "FİNANSAL VERİ PLATFORMU", f_tag, (*GOLD, 205), 6)

# ── Marka: "3N Finans" ──
by = int(H*0.335)
part1, part2 = "3N ", "Finans"
w1 = draw.textlength(part1, font=f_brand)
w2 = draw.textlength(part2, font=f_brand2)
start = CX - (w1 + w2)/2
draw.text((start, by), part1, font=f_brand, fill=GOLD)
draw.text((start + w1, by), part2, font=f_brand2, fill=IVORY)

# ── Marka altı ince çizgi ──
uy = by + int(136*S)
draw.line([(CX-64*S, uy), (CX+64*S, uy)], fill=(*GOLD, 190), width=max(1,2*S))

# ── Slogan (italik serif) ──
subtxt = "Türkiye'nin finansal nabzı, tek ekranda."
sw = draw.textlength(subtxt, font=f_sub)
sy = uy + 28*S
draw.text((CX - sw/2, sy), subtxt, font=f_sub, fill=(*IVORY, 215))

# ── Kategori satırı + iki yanında ince ayırıcı çizgi ──
cy = sy + 66*S
cat_left, cat_w = tracked(cy, "TEFAS  ·  BIST  ·  TCMB  ·  GLOBAL PİYASALAR", f_tag, (*MUTED, 225), 4)
ly = cy + 11*S
rule = 54*S; pad = 26*S
draw.line([(cat_left - pad - rule, ly), (cat_left - pad, ly)], fill=(*GOLD, 90), width=max(1,1*S))
draw.line([(cat_left + cat_w + pad, ly), (cat_left + cat_w + pad + rule, ly)], fill=(*GOLD, 90), width=max(1,1*S))

# ── Alt: URL ──
uurl = "www.3nfinans.com"
uw = draw.textlength(uurl, font=f_url)
draw.text((CX - uw/2, H - 88*S), uurl, font=f_url, fill=(*IVORY, 195))

# ── Küçült & kaydet ──
out = img.resize((1200, 630), Image.LANCZOS)
out.save("static/og-elegant-preview.png", "PNG")
print("ok -> static/og-elegant-preview.png")
