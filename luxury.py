"""Luxury theme for the motion-graphics video: near-black, gold, serif type, slow cinematic motion.

Same section types as motion.py (title, what, features, steps, benefit, cta); motion.build_video
switches to this module when style="luxury".
"""
import math
import random
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from motion import (FPS, H, W, _c, _probe, draw_block, ease_out, fade, fit, font, glow, layout,  # noqa: F401
                    logo_sprite, panel, put)

GOLD = (212, 175, 92)
GOLD_LIGHT = (240, 214, 150)
IVORY = (245, 239, 227)
MUTED = (178, 171, 158)
OUT = 0.6  # seconds of fade-out at the end of each section


def _out(t: float, T: float) -> float:
    return 1 - ease_out((t - (T - OUT)) / OUT)


# ---------- text ----------

@lru_cache(maxsize=256)
def tracked(text: str, size: int, color: tuple, spacing: int, key: str = "medium") -> Image.Image:
    """Single-line, letter-spaced text sprite (used for small caps labels)."""
    f = font(key, size)
    widths = [f.getlength(ch) for ch in text]
    w = int(sum(widths) + spacing * (len(text) - 1)) + 20
    asc, desc = f.getmetrics()
    img = Image.new("RGBA", (w, asc + desc + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 10.0
    for ch, cw in zip(text, widths):
        d.text((x, 10), ch, font=f, fill=color + (255,))
        x += cw + spacing
    return img


def show(f, spr: Image.Image, cx: float, y: float, t: float, start: float, dur: float = 0.9,
         rise: float = 22, o: float = 1.0, anchor_left: bool = False):
    p = ease_out((t - start) / dur)
    if p <= 0 or o <= 0:
        return
    s = fade(spr, p * o)
    x = cx if anchor_left else cx - spr.width / 2
    f.paste(s, (int(x), int(y + (1 - p) * rise)), s)


def hairline(f, cx: float, y: float, width: int, t: float, start: float, dur: float = 1.0, o: float = 1.0,
             color: tuple = GOLD, left: bool = False):
    w = int(width * ease_out((t - start) / dur))
    if w < 2 or o <= 0:
        return
    line = fade(panel(w, 2, 0, color + (255,)), o)
    x = cx if left else cx - w / 2
    f.paste(line, (int(x), int(y)), line)


# ---------- background ----------

class Background:
    def __init__(self, pal=None):
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        d = np.sqrt(((xx - W / 2) / (W * 0.75)) ** 2 + ((yy - H * 0.42) / (H * 0.62)) ** 2)
        k = np.clip(1 - d, 0, 1) ** 1.6
        base = np.array((5, 5, 7), np.float32)
        warm = np.array((30, 24, 15), np.float32)
        self.base = np.ascontiguousarray((base + (warm - base) * k[..., None]).astype(np.uint8))
        rnd = random.Random(7)
        self.dust = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.choice((6, 9, 14)), rnd.uniform(14, 36),
                      rnd.uniform(0, 6.28), rnd.uniform(0.6, 1.6)) for _ in range(36)]
        self.halo = glow(560, GOLD, 38)

    def frame(self, t: float) -> Image.Image:
        img = Image.fromarray(self.base.copy())
        put(img, self.halo, 540 + 60 * math.sin(t * 0.22), 780 + 50 * math.cos(t * 0.17))
        for x, y, r, speed, ph, tw in self.dust:
            yy = (y - t * speed) % (H + 40) - 20
            a = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * tw + ph))
            spr = _speck(r)
            put(img, fade(spr, a), x + 18 * math.sin(t * 0.3 + ph), yy)
        return img


@lru_cache(maxsize=8)
def _speck(r: int) -> Image.Image:
    img = Image.new("RGBA", (r * 4, r * 4), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((r * 1.4, r * 1.4, r * 2.6, r * 2.6), fill=GOLD_LIGHT + (150,))
    return img.filter(ImageFilter.GaussianBlur(r * 0.35))


# ---------- photo with cinematic push and light sweep ----------

class Ctx:
    def __init__(self, pal, photos, business, logo):
        self.pal, self.photos, self.business, self.logo = pal, photos, business, logo


@lru_cache(maxsize=24)
def _photo_base(path: str, w: int, h: int, letter: str) -> Image.Image:
    bw, bh = int(w * 1.25), int(h * 1.25)
    try:
        src = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        src.thumbnail((1800, 1800))
        back = ImageOps.fit(src, (bw, bh), Image.LANCZOS).filter(ImageFilter.GaussianBlur(30))
        back = Image.blend(back, Image.new("RGB", (bw, bh), (0, 0, 0)), 0.45)
        fg = ImageOps.contain(src, (int(bw * 0.9), int(bh * 0.9)), Image.LANCZOS)
        back.paste(fg, ((bw - fg.width) // 2, (bh - fg.height) // 2))
        return back
    except Exception:
        img = Image.new("RGB", (bw, bh), (24, 20, 14))
        ImageDraw.Draw(img).text((bw / 2, bh / 2), (letter or "?").upper(), font=font("serif", int(bh * 0.45)),
                                 fill=GOLD, anchor="mm")
        return img


@lru_cache(maxsize=2)
def _sweep_band(h: int) -> Image.Image:
    bw = 230
    xs = np.linspace(-1, 1, bw)
    prof = (np.exp(-(xs ** 2) * 5.0) * 120).astype(np.uint8)
    arr = np.zeros((h + 400, bw, 4), np.uint8)
    arr[..., :3] = (255, 244, 220)
    arr[..., 3] = prof[None, :]
    return Image.fromarray(arr, "RGBA").rotate(-14, resample=Image.BICUBIC, expand=True)


def photo(f, t: float, T: float, c: Ctx, idx: int, w: int, h: int, cx: float, cy: float, o: float,
          start: float = 0.1, sweep_at: float = 1.3):
    path = c.photos[idx % len(c.photos)] if c.photos else ""
    base = _photo_base(path, w, h, (c.business or "?")[:1])
    prog = _c(t / max(T, 1))
    z = 1.0 + 0.10 * prog  # slow push-in
    cw, ch = base.width / (1.25 * z), base.height / (1.25 * z)
    box = ((base.width - cw) / 2, (base.height - ch) / 2, (base.width + cw) / 2, (base.height + ch) / 2)
    img = base.resize((w, h), Image.BILINEAR, box=box).convert("RGBA")
    ts = t - sweep_at
    if 0 <= ts <= 1.6:
        x = int(-260 + (w + 360) * ease_out(ts / 1.6))
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        band = _sweep_band(h)
        layer.paste(band, (x, -(band.height - h) // 2))
        img = Image.alpha_composite(img, layer)
    # curtain reveal from the middle outwards
    p = ease_out((t - start) / 1.3)
    if p <= 0.01 or o <= 0:
        return
    vis = max(2, int(h * p))
    top = (h - vis) // 2
    band_img = fade(img.crop((0, top, w, top + vis)), _c(p * 2.5) * o)
    f.paste(band_img, (int(cx - w / 2), int(cy - vis / 2)), band_img)
    frame = fade(panel(w + 40, vis + 40, 0, (0, 0, 0, 0), GOLD + (255,), 2), _c(p * 2) * o)
    f.paste(frame, (int(cx - w / 2 - 20), int(cy - vis / 2 - 20)), frame)


# ---------- sections ----------

def _headline(text, sizes, max_w, lines, key="serif"):
    return fit(text, key, sizes, max_w, lines, IVORY)


def sec_title(f, t, T, s, c, idx):
    o = _out(t, T)
    photo(f, t, T, c, idx, 700, 840, 540, 790, o)
    head = _headline(s["headline"], (88, 78, 68, 58), 900, 3)
    draw_block(f, head, 540, 1290, t, 1.2, stagger=0.16, dur=0.9, rise=26, opacity=o)
    y = 1290 + head.h + 26
    hairline(f, 540, y, 200, t, 1.9, o=o)
    if s.get("sub"):
        sub = fit(s["sub"], "corm", (50, 44, 40), 860, 2, MUTED)
        draw_block(f, sub, 540, y + 30, t, 2.2, stagger=0.06, dur=0.8, rise=14, opacity=o)


def sec_what(f, t, T, s, c, idx):
    o = _out(t, T)
    photo(f, t, T, c, idx, 640, 760, 540, 640, o)
    head = _headline(s["headline"], (78, 68, 60, 52), 900, 3)
    draw_block(f, head, 540, 1150, t, 1.0, stagger=0.14, dur=0.9, rise=24, opacity=o)
    y = 1150 + head.h + 24
    hairline(f, 540, y, 160, t, 1.6, o=o)
    if s.get("sub"):
        sub = fit(s["sub"], "corm", (52, 46, 42, 38), 860, 5, IVORY)
        draw_block(f, sub, 540, y + 36, t, 1.9, stagger=0.05, dur=0.8, rise=14, opacity=o)


def _list_header(f, t, T, s, c, idx):
    o = _out(t, T)
    head = _headline(s["headline"], (68, 60, 54), 900, 2)
    draw_block(f, head, 540, 190, t, 0.1, stagger=0.14, dur=0.9, rise=20, opacity=o)
    hairline(f, 540, 190 + head.h + 18, 140, t, 0.8, o=o)
    photo(f, t, T, c, idx, 400, 470, 540, 690, o, start=0.3, sweep_at=2.0)
    return o


ROMAN = ["I", "II", "III", "IV"]


def sec_features(f, t, T, s, c, idx):
    o = _list_header(f, t, T, s, c, idx)
    items = s.get("items", [])[:4]
    y0, row_h = 1050, 175
    for i, it in enumerate(items):
        st = 1.2 + i * 1.0
        if t < st:
            continue
        y = y0 + i * row_h
        show(f, tracked(f"0{i + 1}", 28, GOLD, 5), 120, y + 16, t, st, 0.8, 16, o, anchor_left=True)
        title = fit(it["title"], "serif", (44, 40, 36, 32), 700, 1, IVORY, "left")
        draw_block(f, title, 540 + 90, y + 6, t, st + 0.15, dur=0.7, rise=16, opacity=o)
        if it.get("detail"):
            det = fit(it["detail"], "corm", (40, 36, 32), 700, 1, MUTED, "left")
            draw_block(f, det, 540 + 90, y + 6 + title.h + 4, t, st + 0.35, dur=0.7, rise=14, opacity=o)
        hairline(f, 110, y + row_h - 22, 860, t, st + 0.4, dur=0.9, o=o * 0.7, left=True)


def sec_steps(f, t, T, s, c, idx):
    o = _list_header(f, t, T, s, c, idx)
    items = s.get("items", [])[:4]
    y0, step_h, cx_c = 1050, 185, 160
    starts = [1.2 + i * 1.1 for i in range(len(items))]
    for i, text in enumerate(items):
        yc = y0 + i * step_h + 50
        if i + 1 < len(items):
            grow = ease_out((t - starts[i] - 0.4) / 0.8)
            if grow > 0:
                ln = int((step_h - 100) * grow)
                bar = fade(panel(2, max(ln, 2), 0, GOLD + (170,)), o)
                f.paste(bar, (cx_c - 1, yc + 52), bar)
        if t < starts[i]:
            continue
        p = ease_out((t - starts[i]) / 0.8)
        ring = fade(_ring(92, ROMAN[i]), p * o)
        put(f, ring, cx_c, yc)
        blk = fit(text, "serif", (42, 38, 34, 30), 720, 2, IVORY, "left")
        draw_block(f, blk, 540 + 130, yc - blk.h / 2, t, starts[i] + 0.15, dur=0.7, rise=16, opacity=o)


@lru_cache(maxsize=8)
def _ring(d: int, label: str) -> Image.Image:
    s = 4
    img = Image.new("RGBA", (d * s, d * s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    dr.ellipse((2 * s, 2 * s, d * s - 2 * s, d * s - 2 * s), outline=GOLD + (255,), width=2 * s)
    dr.text((d * s / 2, d * s / 2), label, font=font("serif", int(d * s * 0.38)), fill=GOLD + (255,), anchor="mm")
    return img.resize((d, d), Image.LANCZOS)


def sec_benefit(f, t, T, s, c, idx):
    o = _out(t, T)
    photo(f, t, T, c, idx, 640, 760, 540, 650, o)
    tag = (s.get("tag") or "Made for you").upper()[:24]
    spr = tracked(tag, 28, GOLD, 9)
    y = 1130
    show(f, spr, 540, y, t, 1.0, 0.9, 10, o)
    gap = spr.width / 2 + 24
    hairline(f, 540 - gap, y + 24, 120, t, 1.2, o=o, left=False)
    hairline(f, 540 + gap + 60, y + 24, 120, t, 1.2, o=o, left=False)
    head = _headline(s["headline"], (72, 64, 56, 50), 900, 3)
    draw_block(f, head, 540, y + 80, t, 1.3, stagger=0.14, dur=0.9, rise=24, opacity=o)
    if s.get("sub"):
        sub = fit(s["sub"], "corm", (48, 42, 38), 860, 3, MUTED)
        draw_block(f, sub, 540, y + 80 + head.h + 24, t, 2.0, stagger=0.05, dur=0.8, rise=14, opacity=o)


def sec_cta(f, t, T, s, c, idx):
    o = _out(t, T)
    photo(f, t, T, c, idx, 500, 600, 540, 520, o)
    name = _headline(c.business, (96, 84, 72, 62), 900, 2, "serifb")
    draw_block(f, name, 540, 920, t, 1.0, stagger=0.16, dur=0.9, rise=24, opacity=o)
    top = 920 + name.h + 20
    hairline(f, 540, top, 180, t, 1.6, o=o)
    top += 30
    if s.get("headline"):
        hb = fit(s["headline"], "corm", (56, 50, 44), 860, 2, MUTED)
        draw_block(f, hb, 540, top, t, 1.8, stagger=0.06, dur=0.8, rise=14, opacity=o)
        top += hb.h + 30
    label = (s.get("sub") or "Message us to order").upper()[:30]
    bw, bh = 780, 128
    by = max(top + 20, 1400)
    p = ease_out((t - 2.2) / 0.9)
    if p > 0:
        btn = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ImageDraw.Draw(btn).rectangle((0, 0, bw - 1, bh - 1), outline=GOLD + (255,), width=2)
        txt = tracked(label, 34, GOLD_LIGHT, 8)
        btn.alpha_composite(txt, (int((bw - txt.width) / 2), int((bh - txt.height) / 2)))
        ts = (t - 3.0) % 3.2
        if ts < 1.2:  # shimmer across the button
            layer = Image.new("RGBA", btn.size, (0, 0, 0, 0))
            band = _sweep_band(bh)
            layer.paste(band, (int(-260 + (bw + 300) * ease_out(ts / 1.2)), -(band.height - bh) // 2))
            btn = Image.alpha_composite(btn, layer)
        put(f, fade(btn, p * o), 540, by + bh / 2 + (1 - p) * 20)


SECTIONS = {"title": sec_title, "what": sec_what, "features": sec_features, "steps": sec_steps,
            "benefit": sec_benefit, "cta": sec_cta}


# ---------- persistent overlay ----------

@lru_cache(maxsize=1)
def _frame_sprite() -> Image.Image:
    return panel(W - 56, H - 56, 0, (0, 0, 0, 0), GOLD + (105,), 1)


def draw_overlay(f, t_global: float, total: float, c: Ctx):
    a = _c(t_global / 1.5)
    fr = fade(_frame_sprite(), a)
    f.paste(fr, (28, 28), fr)
    name = (c.business or "").upper()[:28]
    if name:
        show(f, tracked(name, 24, GOLD, 8), 540, 84, t_global, 0.3, 1.0, 0)
        hairline(f, 540, 140, 60, t_global, 0.6, o=1.0)
    if c.logo:
        lg = logo_sprite(c.logo)
        if lg:
            lg = lg.copy()
            lg.thumbnail((200, 54))
            f.paste(lg, (540 - lg.width // 2, 1815 - lg.height), lg)


def min_duration(s: dict) -> float:
    n = len(s.get("items") or [])
    kind = s["type"]
    if kind == "features":
        base = 1.2 + 1.0 * n + 2.0
    elif kind == "steps":
        base = 1.2 + 1.1 * n + 2.0
    elif kind == "title":
        base = 5.5
    elif kind == "cta":
        base = 6.0
    else:
        base = 5.5
    words = len(f"{s.get('headline', '')} {s.get('sub', '')}".split())
    return max(base, words / 2.5 + 2.4)
