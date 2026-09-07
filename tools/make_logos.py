"""Generate the MAXGEN logo family (lockup 6H, icon 2) as SVG + PNG.

Outputs (both locations get the same files):
  <site>/assets/img/logo/            used by the website
  <session>/marketing/logos/final/   the deliverable set for Garlon

SVGs use Georgia Bold Italic (installed on Windows/Mac; falls back to any serif).
PNGs are rasterised with Pillow using C:\\Windows\\Fonts\\georgiaz.ttf so they look identical everywhere.
"""
from __future__ import annotations
import shutil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SITE = Path(__file__).resolve().parents[1]
OUT_SITE = SITE / "assets" / "img" / "logo"
OUT_MKT = SITE.parent / "marketing" / "logos" / "final"
TREE_SVG = SITE / "assets" / "img" / "logo-transparent.svg"
FONT_BI = r"C:\Windows\Fonts\georgiaz.ttf"   # Georgia Bold Italic
FONT_RG = r"C:\Windows\Fonts\georgia.ttf"

DARK = dict(bg1="#1c1a22", bg2="#0d0d14", border="#8a6010", inner="#6b4c08",
            g=["#ffe88a", "#d4a828", "#8a6010", "#3d2a06"], tag="#d8c690", rule=["#7a5010", "#ffe88a", "#7a5010"],
            hi=(255, 220, 80, 150), lo=(0, 0, 0, 230))
PAPER = dict(bg1="#fffdf6", bg2="#f0ede4", border="#8a6010", inner="#b8871e",
             g=["#e0b83a", "#b8871e", "#7a5010", "#3d2a06"], tag="#6b4c08", rule=["#b8871e", "#6b4c08", "#b8871e"],
             hi=(255, 240, 180, 230), lo=(60, 40, 0, 130))
TAG = "MAXWELL GENEALOGY STANDARD"


# ----------------------------------------------------------------------------- SVG
def defs(t: dict, uid: str) -> str:
    return f"""<defs>
  <radialGradient id="bg{uid}" cx="50%" cy="0%" r="100%"><stop offset="0" stop-color="{t['bg1']}"/><stop offset="1" stop-color="{t['bg2']}"/></radialGradient>
  <linearGradient id="gold{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t['g'][0]}"/><stop offset="0.3" stop-color="{t['g'][1]}"/><stop offset="0.6" stop-color="{t['g'][2]}"/><stop offset="1" stop-color="{t['g'][3]}"/></linearGradient>
  <linearGradient id="rule{uid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{t['rule'][0]}"/><stop offset="0.5" stop-color="{t['rule'][1]}"/><stop offset="1" stop-color="{t['rule'][2]}"/></linearGradient>
  <filter id="engrave{uid}" x="-10%" y="-20%" width="120%" height="140%">
    <feDropShadow dx="1" dy="1" stdDeviation="0" flood-color="{'#ffdc50' if t is DARK else '#fff0b4'}" flood-opacity="{0.6 if t is DARK else 0.9}"/>
    <feDropShadow dx="-1" dy="-1" stdDeviation="0" flood-color="#000" flood-opacity="{0.9 if t is DARK else 0.5}"/>
    <feDropShadow dx="0" dy="0" stdDeviation="{10 if t is DARK else 4}" flood-color="#b48214" flood-opacity="{0.35 if t is DARK else 0.2}"/>
  </filter>
</defs>"""


def plate(t, uid, x, y, w, h, r=8):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="url(#bg{uid})" stroke="{t["border"]}" stroke-width="2"/>'
            f'<rect x="{x+5}" y="{y+5}" width="{w-10}" height="{h-10}" rx="{max(r-3,2)}" fill="none" stroke="{t["inner"]}" stroke-width="1"/>')


FONT_CSS = "font-family:Georgia,'Times New Roman',serif;font-style:italic;font-weight:700"


def word(t, uid, x, y, size, text="MAXGEN", spacing_em=0.22):
    return (f'<text x="{x}" y="{y}" text-anchor="middle" style="{FONT_CSS}" font-size="{size}" '
            f'letter-spacing="{size*spacing_em:.1f}" fill="url(#gold{uid})" filter="url(#engrave{uid})">{text}</text>')


def rule(uid, x, y, w, h=2):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#rule{uid})"/>'


def tagline(t, x, y, size, text=TAG, spacing_em=0.3):
    return (f'<text x="{x}" y="{y}" text-anchor="middle" font-family="Georgia,serif" font-size="{size}" '
            f'letter-spacing="{size*spacing_em:.1f}" fill="{t["tag"]}">{text}</text>')


def tree_group(x, y, w):
    src = TREE_SVG.read_text(encoding="utf-8")
    inner = src[src.index(">", src.index("<svg")) + 1: src.rindex("</svg>")]
    inner = inner.replace('id="rg"', 'id="rgtree"').replace("url(#rg)", "url(#rgtree)")
    s = w / 1000
    return f'<g transform="translate({x},{y}) scale({s:.4f})">{inner}</g>'


def svg_horizontal(t, uid):
    W, H = 900, 340
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="MAXGEN — Maxwell Genealogy Standard">
{defs(t, uid)}
{plate(t, uid, 20, 20, W-40, H-40)}
{word(t, uid, W/2 + 12, 175, 120)}
{rule(uid, 200, 208, W-400)}
{tagline(t, W/2 + 4, 262, 22)}
</svg>"""


def svg_vertical(t, uid, with_tree=True):
    W, H = 520, (760 if with_tree else 560)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-label="MAXGEN — Maxwell Genealogy Standard">',
             defs(t, uid), plate(t, uid, 20, 20, W-40, H-40)]
    y = 40
    if with_tree:
        parts.append(tree_group(60, y, 400))
        y += 250
    parts.append(word(t, uid, W/2 + 8, y + 96, 84))
    parts.append(rule(uid, 120, y + 128, W-240))
    for i, line in enumerate(["MAXWELL", "GENEALOGY", "STANDARD"]):
        parts.append(tagline(t, W/2 + 3, y + 178 + i*40, 20, line))
    parts.append("</svg>")
    return "\n".join(parts)


def svg_icon(t, uid):
    S = 512
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}" role="img" aria-label="MAXGEN">
{defs(t, uid)}
{plate(t, uid, 16, 16, S-32, S-32, 56)}
{word(t, uid, S/2 + 8, 320, 230, "MG", 0.06)}
{rule(uid, 116, 372, S-232, 6)}
</svg>"""


# ----------------------------------------------------------------------------- PNG (Pillow)
def hexrgb(h):
    h = h.lstrip("#"); return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def vgradient(w, h, stops):
    img = Image.new("RGBA", (w, max(h, 1)))
    px = img.load()
    cols = [hexrgb(c) for c in stops]; pos = [0, 0.3, 0.6, 1.0]
    for y in range(h):
        f = y / max(h - 1, 1)
        for i in range(3):
            if f <= pos[i+1]:
                k = (f - pos[i]) / (pos[i+1] - pos[i]); a, b = cols[i], cols[i+1]
                c = tuple(int(a[j] + (b[j]-a[j])*k) for j in range(3)); break
        for x in range(w):
            px[x, y] = c + (255,)
    return img


def hgradient(w, h, stops):
    img = Image.new("RGBA", (max(w, 1), h)); px = img.load()
    cols = [hexrgb(c) for c in stops]
    for x in range(w):
        f = x / max(w - 1, 1); i = 0 if f < 0.5 else 1; k = (f - 0.5*i) / 0.5
        a, b = cols[i], cols[i+1]; c = tuple(int(a[j] + (b[j]-a[j])*k) for j in range(3))
        for y in range(h): px[x, y] = c + (255,)
    return img


def radial_bg(w, h, c1, c2):
    a, b = hexrgb(c1), hexrgb(c2); img = Image.new("RGBA", (w, h)); px = img.load()
    for y in range(h):
        for x in range(w):
            d = min(1.0, (((x - w/2) / w) ** 2 + ((y) / h) ** 2) ** 0.5)
            px[x, y] = tuple(int(a[i] + (b[i]-a[i])*d) for i in range(3)) + (255,)
    return img


def spaced_text_mask(text, font, spacing, pad=6):
    widths = [font.getlength(ch) for ch in text]
    total = int(sum(widths) + spacing * (len(text) - 1)) + pad*2
    asc, desc = font.getmetrics(); h = asc + desc + pad*2
    mask = Image.new("L", (total, h), 0); d = ImageDraw.Draw(mask); x = pad
    for ch, w in zip(text, widths):
        d.text((x, pad), ch, font=font, fill=255); x += w + spacing
    return mask


def draw_gold_text(base, text, font, spacing, cx, cy, t):
    mask = spaced_text_mask(text, font, spacing)
    grad = vgradient(mask.width, mask.height, t["g"])
    x0 = int(cx - mask.width/2); y0 = int(cy - mask.height/2)
    lo = Image.new("RGBA", mask.size, t["lo"]); hi = Image.new("RGBA", mask.size, t["hi"])
    base.paste(lo, (x0-1, y0-1), mask); base.paste(hi, (x0+1, y0+1), mask)
    glow = Image.new("RGBA", mask.size, (180, 130, 20, 90)); g = mask.filter(ImageFilter.GaussianBlur(8))
    base.paste(glow, (x0, y0), g)
    base.paste(grad, (x0, y0), mask)
    return mask.width, mask.height


def draw_plate(base, box, t, radius):
    x0, y0, x1, y1 = box
    bg = radial_bg(x1-x0, y1-y0, t["bg1"], t["bg2"])
    m = Image.new("L", bg.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, bg.width-1, bg.height-1), radius=radius, fill=255)
    base.paste(bg, (x0, y0), m)
    d = ImageDraw.Draw(base)
    d.rounded_rectangle(box, radius=radius, outline=hexrgb(t["border"]), width=max(2, radius//14))
    d.rounded_rectangle((x0+5, y0+5, x1-5, y1-5), radius=max(radius-3, 2), outline=hexrgb(t["inner"]), width=1)


def draw_rule(base, x, y, w, h, t):
    base.paste(hgradient(w, h, t["rule"]), (x, y))


def draw_tag(base, text, font, spacing, cx, y, t):
    mask = spaced_text_mask(text, font, spacing, pad=2)
    col = Image.new("RGBA", mask.size, hexrgb(t["tag"]) + (255,))
    base.paste(col, (int(cx - mask.width/2), y), mask)


def png_horizontal(t, scale=2):
    W, H = 900*scale, 340*scale
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_plate(img, (20*scale, 20*scale, W-20*scale, H-20*scale), t, 8*scale)
    f = ImageFont.truetype(FONT_BI, 120*scale)
    draw_gold_text(img, "MAXGEN", f, int(120*0.22*scale), W/2 + 10*scale, 135*scale, t)
    draw_rule(img, 200*scale, 208*scale, W-400*scale, 2*scale, t)
    draw_tag(img, TAG, ImageFont.truetype(FONT_RG, 22*scale), int(22*0.3*scale), W/2, 246*scale, t)
    return img


def png_icon(t, size):
    S = 512; img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    draw_plate(img, (16, 16, S-16, S-16), t, 56)
    f = ImageFont.truetype(FONT_BI, 230)
    draw_gold_text(img, "MG", f, int(230*0.06), S/2 + 6, 250, t)
    draw_rule(img, 116, 372, S-232, 6, t)
    return img.resize((size, size), Image.LANCZOS) if size != S else img


def png_og(t):
    """1200x630 social-preview image: dark plate lockup centred."""
    W, H = 1200, 630
    img = Image.new("RGBA", (W, H), hexrgb(t["bg2"]) + (255,))
    lock = png_horizontal(t, scale=1)
    img.paste(lock, ((W - lock.width)//2, (H - lock.height)//2 - 10), lock)
    d = ImageDraw.Draw(img); f = ImageFont.truetype(FONT_RG, 22)
    msg = "The open, public-domain genealogy data standard  ·  opengenealogyai.org"
    w = f.getlength(msg); d.text(((W - w)/2, H - 80), msg, font=f, fill=hexrgb(t["tag"]))
    return img


def main():
    for out in (OUT_SITE, OUT_MKT):
        out.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, t, uid in (("dark", DARK, "d"), ("parchment", PAPER, "p")):
        files[f"maxgen-horizontal-{name}.svg"] = svg_horizontal(t, uid)
        files[f"maxgen-vertical-{name}.svg"] = svg_vertical(t, uid, True)
        files[f"maxgen-vertical-words-{name}.svg"] = svg_vertical(t, uid, False)
        files[f"maxgen-icon-{name}.svg"] = svg_icon(t, uid)
    for fn, text in files.items():
        (OUT_SITE / fn).write_text(text, encoding="utf-8")
    pngs = {"maxgen-horizontal-dark.png": png_horizontal(DARK), "maxgen-horizontal-parchment.png": png_horizontal(PAPER),
            "maxgen-icon-dark-512.png": png_icon(DARK, 512), "maxgen-icon-parchment-512.png": png_icon(PAPER, 512),
            "maxgen-og-1200x630.png": png_og(DARK)}
    for s in (16, 32, 48, 64, 180, 256):
        pngs[f"favicon-{s}.png"] = png_icon(DARK, s)
    for fn, im in pngs.items():
        im.save(OUT_SITE / fn)
    ico = [png_icon(DARK, s).convert("RGBA") for s in (16, 32, 48)]
    ico[0].save(OUT_SITE / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)], append_images=ico[1:])
    for f in OUT_SITE.iterdir():
        shutil.copy2(f, OUT_MKT / f.name)
    print(f"wrote {len(list(OUT_SITE.iterdir()))} files to {OUT_SITE} and {OUT_MKT}")


if __name__ == "__main__":
    main()
