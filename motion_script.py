"""Writes the on-screen text (and optional narration) for the motion-graphics video with Gemini."""
import logging
import re

import config
import gemini

log = logging.getLogger(__name__)

PROMPT = """You write the content for a {length}-second vertical motion-graphics product video for social media.

Business: {business}
What we know about the product (from a photo and the owner): {brief}
Tone: {tone}

Return ONLY valid JSON in exactly this shape:
{{
  "caption": "ready-to-post caption with 4-6 relevant hashtags",
  "title":    {{"headline": "scroll-stopping hook, max 7 words", "sub": "the product name, max 8 words", "voiceover": "1 short spoken sentence"}},
  "what":     {{"headline": "max 7 words, e.g. what it is", "sub": "one clear sentence, max 22 words, explaining what it is and what it is for", "voiceover": "1-2 spoken sentences"}},
  "features": {{"headline": "max 5 words", "items": [{{"title": "max 4 words", "detail": "max 6 words"}}], "voiceover": "1-2 spoken sentences"}},
  "steps":    {{"headline": "How it works", "items": ["max 7 words per step"], "voiceover": "1-2 spoken sentences"}},
  "benefit":  {{"tag": "Perfect for", "headline": "who it is for, max 8 words", "sub": "the benefit, max 14 words", "voiceover": "1 spoken sentence"}},
  "cta":      {{"headline": "max 5 words", "sub": "button label, max 4 words", "voiceover": "1 spoken sentence naming {business}"}}
}}

Rules:
- features has exactly {n} items. steps has exactly {n} items, in order.
- steps explain how the product works, or how it is made or prepared, in simple plain words. Use "How it's made" or "How to prepare" as the headline if that fits better than "How it works".
- Only use facts from the product information above, plus general knowledge that is true for this kind of product. Never invent prices, discounts, sizes, phone numbers, certifications or health/performance claims.
- Text on screen must be short and punchy. Simple English, no emojis, no hashtags outside the caption.
- All voiceover lines together are about {words} words, and must match what is on screen.
"""


def _text(v, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def _clean(data: dict, business: str, n: int) -> list[dict]:
    sections = []
    for kind in ("title", "what", "features", "steps", "benefit", "cta"):
        raw = data.get(kind)
        if not isinstance(raw, dict):
            continue
        sec = {"type": kind, "headline": _text(raw.get("headline"), 80), "sub": _text(raw.get("sub"), 160),
               "voiceover": _text(raw.get("voiceover"), 260), "tag": _text(raw.get("tag"), 24)}
        if kind == "features":
            items = []
            for it in raw.get("items") or []:
                if isinstance(it, dict) and _text(it.get("title"), 40):
                    items.append({"title": _text(it.get("title"), 40), "detail": _text(it.get("detail"), 60)})
                elif isinstance(it, str) and it.strip():
                    items.append({"title": _text(it, 40), "detail": ""})
            sec["items"] = items[:n]
        elif kind == "steps":
            sec["items"] = [_text(i, 70) for i in (raw.get("items") or []) if _text(i, 70)][:n]
        if kind in ("features", "steps") and len(sec["items"]) < 2:
            continue
        if kind not in ("features", "steps") and not sec["headline"]:
            continue
        sections.append(sec)
    kinds = {s["type"] for s in sections}
    if not {"title", "what"} <= kinds or "cta" not in kinds:
        raise ValueError("script is missing required sections")
    return sections


def fallback_sections(business: str, a: dict, n: int) -> list[dict]:
    """Built straight from the product analysis when Gemini can't write the script."""
    name = a.get("name") or "Our product"
    feats = [{"title": f[:40], "detail": ""} for f in (a.get("features") or [])][:n]
    steps = [s.strip() for s in re.split(r"(?<=[.!?])\s+", a.get("how_it_works") or "") if s.strip()][:n]
    secs = [
        {"type": "title", "headline": name, "sub": a.get("category", ""), "voiceover": f"Meet {name}."},
        {"type": "what", "headline": "What it is", "sub": a.get("summary", ""), "voiceover": a.get("summary", "")},
    ]
    if len(feats) >= 2:
        secs.append({"type": "features", "headline": "Why it stands out", "items": feats,
                     "voiceover": "Here is what makes it stand out."})
    if len(steps) >= 2:
        secs.append({"type": "steps", "headline": "How it works", "items": [s[:70] for s in steps],
                     "voiceover": a.get("how_it_works", "")})
    if a.get("audience"):
        secs.append({"type": "benefit", "tag": "Made for", "headline": a["audience"][:80], "sub": "",
                     "voiceover": f"Made for {a['audience']}."})
    secs.append({"type": "cta", "headline": "Order today", "sub": "Message us to order",
                 "voiceover": f"Message {business} today to order."})
    return secs


async def write_sections(business: str, brief: str, tone_key: str, length: int, analysis: dict) -> dict:
    n = 3 if length <= 30 else 4
    try:
        prompt = PROMPT.format(length=length, business=business, brief=brief, tone=config.TONES[tone_key][1],
                               n=n, words=int(length * 2.2))
        data = await gemini.generate_json([{"text": prompt}], temperature=0.8)
        return {"sections": _clean(data, business, n), "caption": _text(data.get("caption"), 600) or business}
    except Exception:
        log.exception("Motion script failed, building it from the product analysis")
        return {"sections": fallback_sections(business, analysis or {}, n),
                "caption": f"{(analysis or {}).get('name', business)} | {business}"}
