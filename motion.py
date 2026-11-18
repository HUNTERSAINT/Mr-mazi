"""Motion-graphics product video, drawn frame by frame with Pillow and piped into FFmpeg.

Sections (each animated in and out): title, what, features, steps, benefit, cta.
The palette is taken from the product photo, so every product gets its own look.
"""
import colorsys
import hashlib
import logging
import math
import random
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import config
from music_gen import make_calm_music, make_music

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
FONT_DIR = Path(__file__).parent / "assets" / "fonts"
PAD = 14  # transparent margin around text sprites (room for the shadow)

MIN_SECONDS = {"title": 4.0, "what": 4.5, "benefit": 4.0, "cta": 4.5}


# ---------- easing ----------

def _c(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out(x: float) -> float:
    return 1 - (1 - _c(x)) ** 3


def ease_in(x: float) -> float:
    return _c(x) ** 3


def ease_back(x: float, s: float = 1.7) -> float:
    x = _c(x)
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


# ---------- palette ----------

@dataclass(frozen=True)
class Palette:
    bg1: tuple
    bg2: tuple
    accent: tuple
    on_accent: tuple


def _hsv(h, s, v) -> tuple:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return int(r * 255), int(g * 255), int(b * 255)


def palette_from(photo: str | None, seed: str) -> Palette:
    hue, strong = int(hashlib.md5(seed.encode()).hexdigest(), 16) % 360 / 360, False
    if photo:
        try:
            img = ImageOps.exif_transpose(Image.open(photo)).convert("RGB")
            img.thumbnail((140, 140))
            q = img.quantize(colors=8, method=Image.Quantize.MEDIANCUT).convert("RGB")
            best = 0.0
            for count, rgb in q.getcolors():
                h, s, v = colorsys.rgb_to_hsv(*(x / 255 for x in rgb))
                score = s * (1 if 0.25 < v < 0.97 else 0.2) * (count ** 0.35)
                if score > best and s > 0.28:
                    best, hue, strong = score, h, True
        except Exception:
            log.warning("Could not read colours from the photo", exc_info=True)
    accent = _hsv(hue, 0.78 if strong else 0.70, 1.0)
    lum = 0.299 * accent[0] + 0.587 * accent[1] + 0.114 * accent[2]
    return Palette(bg1=_hsv(hue, 0.60, 0.30), bg2=_hsv(hue + 0.04, 0.70, 0.10), accent=accent,
                   on_accent=(20, 20, 30) if lum > 150 else (255, 255, 255))


# ---------- fonts, text ----------

_VARIABLE = {"serif": ("PlayfairDisplay-Var.ttf", 560), "serifb": ("PlayfairDisplay-Var.ttf", 700),
             "corm": ("CormorantGaramond-Var.ttf", 600)}


@lru_cache(maxsize=64)
def font(weight: str, size: int):
    if weight in _VARIABLE:
        name, wght = _VARIABLE[weight]
        try:
            f = ImageFont.truetype(str(FONT_DIR / name), size)
            f.set_variation_by_axes([wght])
            return f
        except (OSError, AttributeError, ValueError):
            weight = "bold"
    name = "Poppins-Bold.ttf" if weight == "bold" else "Poppins-Medium.ttf"
    for path in (FONT_DIR / name, Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")):
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


@lru_cache(maxsize=1024)
def word_sprite(word: str, weight: str, size: int, color: tuple) -> Image.Image:
    f = font(weight, size)
    asc, desc = f.getmetrics()
    w, h = int(f.getlength(word)) + 2 * PAD, asc + desc + 2 * PAD
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2] > 140:  # light text gets a soft shadow
        ImageDraw.Draw(shadow).text((PAD, PAD + 5), word, font=f, fill=(0, 0, 0, 120))
        shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    ImageDraw.Draw(shadow).text((PAD, PAD), word, font=f, fill=color + (255,))
    return shadow


@dataclass(frozen=True)
class Block:
    items: tuple
    w: int
    h: int
    lines: int


@lru_cache(maxsize=512)
def layout(text: str, weight: str, size: int, color: tuple, max_w: int, align: str = "center",
           gap: float = 0.80) -> Block:
    f = font(weight, size)
    space = f.getlength(" ")
    lines, cur, cur_w = [], [], 0.0
    for wd in text.split():
        ww = f.getlength(wd)
        if cur and cur_w + space + ww > max_w:
            lines.append((cur, cur_w))
            cur, cur_w = [wd], ww
        else:
            cur_w = ww if not cur else cur_w + space + ww
            cur.append(wd)
    if cur:
        lines.append((cur, cur_w))
    asc, desc = f.getmetrics()
    lh = int((asc + desc) * gap)
    items = []
    for li, (ws, lw) in enumerate(lines):
        x = (max_w - lw) / 2 if align == "center" else 0.0
        for wd in ws:
            items.append((word_sprite(wd, weight, size, color), x - PAD, li * lh - PAD))
            x += f.getlength(wd) + space
    return Block(tuple(items), max_w, len(lines) * lh, len(lines))


def fit(text: str, weight: str, sizes: tuple, max_w: int, max_lines: int, color: tuple, align="center") -> Block:
    text = (text or "").strip()
    block = layout(text, weight, sizes[-1], color, max_w, align)
    for s in sizes:
        b = layout(text, weight, s, color, max_w, align)
        widest = max((font(weight, s).getlength(w) for w in text.split()), default=0)
        if b.lines <= max_lines and widest <= max_w:
            return b
        block = b
    return block


def fade(img: Image.Image, o: float) -> Image.Image:
    if o >= 0.995:
        return img
    lut = [int(v * max(0.0, o)) for v in range(256)]
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lut))
    return out


def draw_block(frame, block: Block, cx: float, top: float, t: float, start: float = 0.0,
               stagger: float = 0.09, dur: float = 0.5, rise: float = 46, opacity: float = 1.0):
    left = cx - block.w / 2
    for i, (spr, x, y) in enumerate(block.items):
        p = ease_out((t - start - i * stagger) / dur)
        if p <= 0:
            continue
        s = fade(spr, p * opacity)
        frame.paste(s, (int(left + x), int(top + y + (1 - p) * rise)), s)


# ---------- sprites ----------

@lru_cache(maxsize=64)
def panel(w: int, h: int, radius: int, fill: tuple, outline: tuple | None = None, width: int = 2):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=fill, outline=outline, width=width)
    return img


@lru_cache(maxsize=16)
def glow(r: int, color: tuple, alpha: int):
    img = Image.new("RGBA", (r * 2, r * 2), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((r * 0.25, r * 0.25, r * 1.75, r * 1.75), fill=color + (alpha,))
    return img.filter(ImageFilter.GaussianBlur(r * 0.28))


@lru_cache(maxsize=32)
def icon_circle(d: int, color: tuple, on: tuple, label: str = ""):
    s = 4
    img = Image.new("RGBA", (d * s, d * s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.ellipse((0, 0, d * s - 1, d * s - 1), fill=color + (255,))
    if label:
        f = font("bold", int(d * s * 0.5))
        dr.text((d * s / 2, d * s / 2 + d * s * 0.02), label, font=f, fill=on + (255,), anchor="mm")
    else:
        pts = [(0.27 * d * s, 0.52 * d * s), (0.44 * d * s, 0.68 * d * s), (0.74 * d * s, 0.34 * d * s)]
        dr.line(pts, fill=on + (255,), width=int(d * s * 0.11), joint="curve")
        for x, y in (pts[0], pts[2]):
            r = d * s * 0.055
            dr.ellipse((x - r, y - r, x + r, y + r), fill=on + (255,))
    return img.resize((d, d), Image.LANCZOS)


@lru_cache(maxsize=32)
def card_sprite(path: str, size: int, accent: tuple, letter: str = "?"):
    """Product photo as a rounded card with a blurred fill, white border and drop shadow."""
    box = (size, size)
    radius = int(size * 0.07)
    try:
        src = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        src.thumbnail((1600, 1600))
        back = ImageOps.fit(src, box, Image.LANCZOS).filter(ImageFilter.GaussianBlur(26))
        back = Image.blend(back, Image.new("RGB", box, (0, 0, 0)), 0.18)
        inner = int(size * 0.90)
        fg = ImageOps.contain(src, (inner, inner), Image.LANCZOS)
        m = Image.new("L", fg.size, 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, fg.width - 1, fg.height - 1), int(radius * 0.6), fill=255)
        back.paste(fg, ((size - fg.width) // 2, (size - fg.height) // 2), m)
    except Exception:
        back = Image.new("RGB", box, accent)
        ImageDraw.Draw(back).text((size / 2, size / 2), letter.upper(), font=font("bold", int(size * 0.5)),
                                  fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", box, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    card = Image.new("RGBA", box, (0, 0, 0, 0))
    card.paste(back, (0, 0), mask)
    ImageDraw.Draw(card).rounded_rectangle((0, 0, size - 1, size - 1), radius, outline=(255, 255, 255, 235), width=6)
    pad = 90
    out = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 150), (pad, pad + 34), mask)
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(34)))
    out.alpha_composite(card, (pad, pad))
    return out


def xform(img: Image.Image, scale: float = 1.0, angle: float = 0.0, opacity: float = 1.0) -> Image.Image:
    if abs(scale - 1.0) > 0.002:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    if abs(angle) > 0.05:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True)
    return fade(img, opacity)


def put(frame, img: Image.Image, cx: float, cy: float):
    frame.paste(img, (int(cx - img.width / 2), int(cy - img.height / 2)), img)


# ---------- background ----------

class Background:
    def __init__(self, pal: Palette):
        h2 = H + 420
        ramp = np.linspace(0, 1, h2, dtype=np.float32)[:, None, None]
        top, bot = np.array(pal.bg1, np.float32), np.array(pal.bg2, np.float32)
        self.base = np.ascontiguousarray(np.repeat(top * (1 - ramp) + bot * ramp, W, axis=1).astype(np.uint8))
        lighter = tuple(int(c * 0.6 + 255 * 0.4) for c in pal.accent)
        self.blobs = [(glow(430, pal.accent, 85), 200, 420, 70, 0.5, 0.0),
                      (glow(520, lighter, 60), 900, 1100, 90, 0.35, 1.7),
                      (glow(380, pal.accent, 70), 120, 1500, 60, 0.45, 3.1),
                      (glow(340, lighter, 55), 980, 260, 80, 0.4, 4.4)]

    def frame(self, t: float) -> Image.Image:
        off = int(210 + 160 * math.sin(t * 0.35))
        img = Image.fromarray(self.base[off:off + H])
        for spr, x, y, amp, speed, ph in self.blobs:
            put(img, spr, x + amp * math.sin(t * speed + ph), y + amp * math.cos(t * speed * 0.8 + ph))
        return img


# ---------- sections ----------

class Ctx:
    def __init__(self, pal: Palette, photos: list[str], business: str, logo: str | None):
        self.pal, self.photos, self.business, self.logo = pal, photos, business, logo
        self.white = (255, 255, 255)
        self.muted = (225, 228, 238)

    def card(self, i: int, size: int):
        path = self.photos[i % len(self.photos)] if self.photos else ""
        return card_sprite(path, size, self.pal.accent, (self.business or "?")[:1])


def _out(t: float, T: float) -> float:
    return 1 - ease_in((t - (T - 0.45)) / 0.45)


def sec_title(f, t, T, s, c: Ctx, idx):
    o = _out(t, T)
    put(f, fade(glow(470, c.pal.accent, 120), ease_out(t / 0.8) * o), 540, 760)
    p = _c((t - 0.1) / 0.95)
    card = xform(c.card(idx, 760), 0.55 + 0.45 * ease_back(p), -9 * (1 - ease_out(p)), _c(p * 2.2) * o)
    put(f, card, 540, 760 + (1 - ease_out(p)) * 180 + 12 * math.sin(t * 2.2))
    head = fit(s["headline"], "bold", (92, 80, 70, 60), 940, 3, c.white)
    draw_block(f, head, 540, 1210, t, 0.65, opacity=o)
    if s.get("sub"):
        sub = fit(s["sub"], "medium", (44, 40, 36), 900, 3, c.muted)
        draw_block(f, sub, 540, 1210 + head.h + 36, t, 1.5, stagger=0.04, opacity=o)


def sec_what(f, t, T, s, c: Ctx, idx):
    o = _out(t, T)
    p = _c(t / 0.9)
    card = xform(c.card(idx, 560), 0.8 + 0.2 * ease_back(p), 0, _c(p * 2) * o)
    put(f, card, 540, 540 - (1 - ease_out(p)) * 160 + 10 * math.sin(t * 2.0))
    head = fit(s["headline"], "bold", (80, 70, 62, 54), 940, 3, c.white)
    draw_block(f, head, 540, 930, t, 0.35, opacity=o)
    bar_w = int(260 * ease_out((t - 0.7) / 0.5))
    bar_y = 930 + head.h + 30
    if bar_w > 4:
        bar = fade(panel(bar_w, 10, 5, c.pal.accent + (255,)), o)
        f.paste(bar, (540 - bar_w // 2, bar_y), bar)
    if s.get("sub"):
        sub = fit(s["sub"], "medium", (46, 42, 38, 34), 900, 5, c.muted)
        draw_block(f, sub, 540, bar_y + 56, t, 1.0, stagger=0.05, opacity=o)


def _list_header(f, t, T, s, c: Ctx, idx, card_y=590, card_size=350):
    o = _out(t, T)
    head = fit(s["headline"], "bold", (68, 60, 54), 940, 2, c.white)
    draw_block(f, head, 540, 200, t, 0.0, opacity=o)
    p = _c((t - 0.15) / 0.8)
    card = xform(c.card(idx, card_size), 0.7 + 0.3 * ease_back(p), 0, _c(p * 2) * o)
    put(f, card, 540, card_y - (1 - ease_out(p)) * 80 + 8 * math.sin(t * 2.0))
    return o


def sec_features(f, t, T, s, c: Ctx, idx):
    o = _list_header(f, t, T, s, c, idx)
    items = s.get("items", [])[:4]
    row_h, gap, y0 = 150, 20, 830
    for i, it in enumerate(items):
        start = 0.9 + i * 0.85
        p = ease_back((t - start) / 0.6)
        if t < start:
            continue
        y = y0 + i * (row_h + gap)
        x = 60 + (1 - _c(p)) * 760
        pan = fade(panel(960, row_h, 44, (255, 255, 255, 34), c.pal.accent + (150,), 2), _c(p * 1.5) * o)
        f.paste(pan, (int(x), y), pan)
        ic = xform(icon_circle(86, c.pal.accent, c.pal.on_accent), _c(p) if p > 0 else 0.01, 0, o)
        put(f, ic, x + 75, y + row_h / 2)
        title = fit(it["title"], "bold", (44, 40, 36, 32), 760, 1, c.white, "left")
        draw_block(f, title, x + 140 + 380, y + 20, t, start + 0.12, dur=0.35, rise=18, opacity=o)
        if it.get("detail"):
            det = fit(it["detail"], "medium", (32, 29, 26), 760, 1, c.muted, "left")
            draw_block(f, det, x + 140 + 380, y + 20 + title.h + 6, t, start + 0.25, dur=0.35, rise=18, opacity=o)


def sec_steps(f, t, T, s, c: Ctx, idx):
    o = _list_header(f, t, T, s, c, idx)
    items = s.get("items", [])[:4]
    step_h, y0, cx_circle = 175, 830, 130
    starts = [0.9 + i * 1.1 for i in range(len(items))]
    for i, text in enumerate(items):
        yc = y0 + i * step_h + 48
        if i + 1 < len(items):  # connector grows down to the next circle
            grow = ease_out((t - starts[i] - 0.3) / 0.7)
            if grow > 0:
                ln = int((step_h - 96) * grow)
                bar = fade(panel(8, max(ln, 2), 4, c.pal.accent + (200,)), o)
                f.paste(bar, (cx_circle - 4, yc + 52), bar)
        if t < starts[i]:
            continue
        p = ease_back((t - starts[i]) / 0.5)
        ic = xform(icon_circle(96, c.pal.accent, c.pal.on_accent, str(i + 1)), max(_c(p), 0.01), 0, o)
        put(f, ic, cx_circle, yc)
        blk = fit(text, "bold", (44, 40, 36, 32), 770, 2, c.white, "left")
        draw_block(f, blk, 230 + 385, yc - blk.h / 2, t, starts[i] + 0.1, dur=0.4, rise=24, opacity=o)


def sec_benefit(f, t, T, s, c: Ctx, idx):
    o = _out(t, T)
    p = _c(t / 0.9)
    card = xform(c.card(idx, 560), (0.8 + 0.2 * ease_back(p)) * (1 + 0.015 * math.sin(t * 3)), 0, _c(p * 2) * o)
    put(f, card, 540, 600)
    tag = (s.get("tag") or "Made for you").upper()
    tb = layout(tag, "bold", 30, c.pal.on_accent, 600)
    pw = int(font("bold", 30).getlength(tag)) + 70
    pill = fade(panel(pw, 62, 31, c.pal.accent + (255,)), ease_out((t - 0.4) / 0.4) * o)
    f.paste(pill, (540 - pw // 2, 990), pill)
    draw_block(f, tb, 540, 1006, t, 0.5, stagger=0.0, dur=0.4, rise=0, opacity=o)
    head = fit(s["headline"], "bold", (76, 66, 58, 50), 940, 3, c.white)
    draw_block(f, head, 540, 1090, t, 0.7, opacity=o)
    if s.get("sub"):
        sub = fit(s["sub"], "medium", (42, 38, 34), 900, 3, c.muted)
        draw_block(f, sub, 540, 1090 + head.h + 30, t, 1.4, stagger=0.04, opacity=o)


def sec_cta(f, t, T, s, c: Ctx, idx):
    o = _out(t, T)
    p = _c(t / 0.9)
    card = xform(c.card(idx, 440), 0.7 + 0.3 * ease_back(p), 0, _c(p * 2) * o)
    put(f, card, 540, 500 + (1 - ease_out(p)) * 100)
    name = fit(c.business, "bold", (96, 84, 72, 62), 940, 2, c.white)
    draw_block(f, name, 540, 800, t, 0.4, opacity=o)
    top = 800 + name.h + 24
    if s.get("headline"):
        hb = fit(s["headline"], "medium", (56, 50, 44), 900, 2, c.muted)
        draw_block(f, hb, 540, top, t, 0.9, stagger=0.05, opacity=o)
        top += hb.h + 40
    label = s.get("sub") or "Message us to order"
    bw = 800
    pulse = 1 + 0.035 * math.sin(t * 4.2)
    btn = Image.new("RGBA", (bw, 150), (0, 0, 0, 0))
    btn.alpha_composite(panel(bw, 150, 75, c.pal.accent + (255,)))
    lb = fit(label, "bold", (50, 44, 38, 32), bw - 100, 1, c.pal.on_accent)
    for spr, x, y in lb.items:
        btn.alpha_composite(spr, (max(0, int(50 + x)), max(0, int((150 - lb.h) / 2 + y))))
    pb = ease_back((t - 1.3) / 0.6)
    put(f, xform(btn, max(pb, 0.01) * pulse, 0, _c(pb * 2) * o), 540, max(top + 40, 1230) + 75)


SECTIONS = {"title": sec_title, "what": sec_what, "features": sec_features, "steps": sec_steps,
            "benefit": sec_benefit, "cta": sec_cta}


# ---------- persistent overlay ----------

def draw_overlay(f, t_global: float, total: float, c: Ctx):
    track = panel(960, 8, 4, (255, 255, 255, 45))
    f.paste(track, (60, 64), track)
    w = max(int(960 * t_global / total), 0)
    if w > 8:
        fill = panel(w, 8, 4, c.pal.accent + (255,))
        f.paste(fill, (60, 64), fill)
    pill = pill_sprite(c.business, c.pal.accent)
    f.paste(pill, (60, 100), pill)
    if c.logo:
        lg = logo_sprite(c.logo)
        if lg:
            f.paste(lg, (1020 - lg.width, 100 + (66 - lg.height) // 2), lg)


@lru_cache(maxsize=4)
def pill_sprite(name: str, accent: tuple):
    name = name[:26]
    f = font("bold", 30)
    w = int(f.getlength(name)) + 90
    img = panel(w, 66, 33, (0, 0, 0, 90), (255, 255, 255, 60), 2).copy()
    d = ImageDraw.Draw(img)
    d.ellipse((24, 23, 44, 43), fill=accent + (255,))
    d.text((58, 33), name, font=f, fill=(255, 255, 255, 255), anchor="lm")
    return img


@lru_cache(maxsize=4)
def logo_sprite(path: str):
    try:
        img = Image.open(path).convert("RGBA")
        img.thumbnail((260, 66), Image.LANCZOS)
        return img
    except Exception:
        return None


# ---------- timing, rendering, audio ----------

def min_duration(s: dict) -> float:
    n = len(s.get("items") or [])
    if s["type"] == "features":
        base = 0.9 + 0.85 * n + 1.8
    elif s["type"] == "steps":
        base = 0.9 + 1.1 * n + 1.8
    else:
        base = MIN_SECONDS[s["type"]]
    words = len(f"{s.get('headline', '')} {s.get('sub', '')}".split())
    return max(base, words / 2.8 + 1.6)


def _run(cmd, cwd=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed:\n{p.stderr[-800:]}")


def _probe(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                        "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(p.stdout.strip())


def render_silent(job_dir: Path, sections: list[dict], durations: list[float], ctx: Ctx, out: Path, progress=None,
                  theme=None):
    bg = theme.Background(ctx.pal) if theme else Background(ctx.pal)
    sections_map = theme.SECTIONS if theme else SECTIONS
    overlay = theme.draw_overlay if theme else draw_overlay
    total = sum(durations)
    frames_total = int(round(total * FPS))
    log_file = open(job_dir / "ffmpeg_motion.log", "wb")
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE, stderr=log_file)
    n = 0
    try:
        offset = 0.0
        for idx, (sec, T) in enumerate(zip(sections, durations)):
            draw = sections_map[sec["type"]]
            for i in range(int(round(T * FPS))):
                tg = offset + i / FPS
                frame = bg.frame(tg)
                draw(frame, i / FPS, T, sec, ctx, idx)
                overlay(frame, tg, total, ctx)
                proc.stdin.write(frame.tobytes())
                n += 1
                if progress and n % 90 == 0:
                    progress(n / frames_total)
            offset += T
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("FFmpeg failed while encoding the motion graphics")
    finally:
        log_file.close()
        if proc.poll() is None:
            proc.kill()


def build_video(job_dir: Path, sections: list[dict], photos: list[str], business: str, logo: Path | None,
                audio_files: list[Path] | None, progress=None, style: str = "bold") -> Path:
    """sections: [{type, headline, sub, items, tag}], audio_files: one narration clip per section or None."""
    pal = palette_from(photos[0] if photos else None, business)
    ctx = Ctx(pal, photos, business, str(logo) if logo else None)

    theme = None
    if style == "luxury":
        import luxury as theme
    durations = [theme.min_duration(s) if theme else min_duration(s) for s in sections]
    if audio_files:
        durations = [max(d, _probe(a) + 0.5) for d, a in zip(durations, audio_files)]
    total = sum(durations)

    silent = job_dir / "motion.mp4"
    render_silent(job_dir, sections, durations, ctx, silent, progress, theme)

    # narration padded to each section's length, then joined
    voice = None
    if audio_files:
        names = []
        for i, (a, d) in enumerate(zip(audio_files, durations)):
            wav = job_dir / f"seg{i}.wav"
            _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(a), "-af", f"apad=whole_dur={d:.3f}",
                  "-ar", "44100", "-ac", "1", str(wav)], job_dir)
            names.append(wav.name)
        (job_dir / "segs.txt").write_text("".join(f"file '{n}'\n" for n in names))
        voice = job_dir / "voice.wav"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "segs.txt",
              "-c", "copy", str(voice)], job_dir)

    tracks = sorted(config.MUSIC.glob("*.mp3"))
    if tracks:
        music = random.choice(tracks)
    else:
        music = job_dir / "music.wav"
        (make_calm_music if style == "luxury" else make_music)(total, music)

    final = job_dir / "final.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(silent)]
    if voice:
        cmd += ["-i", str(voice), "-stream_loop", "-1", "-i", str(music)]
        fc = ("[2:a]volume=0.14,afade=t=in:d=1[m];[1:a][m]amix=inputs=2:duration=first:"
              "dropout_transition=0:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        fc = f"[1:a]volume=0.9,afade=t=out:st={max(total - 1.2, 0):.2f}:d=1.2,loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-t", f"{total:.3f}", "-movflags", "+faststart", str(final)]
    _run(cmd, job_dir)
    return final
