"""Turns a business brief into a ~30s scene-by-scene script using the Gemini free tier."""
import json
import logging
import re

import httpx

import config

log = logging.getLogger(__name__)

PROMPT = """You write scripts for 30-second vertical social media videos (TikTok / Reels / Status) for small businesses.

Business name: {business}
Brief from the owner: {brief}
Tone: {tone}
{photo_note}

Return ONLY valid JSON in exactly this shape:
{{
  "scenes": [
    {{"voiceover": "spoken line", "keywords": "2-3 plain English words to search stock footage"}}
  ],
  "caption": "ready-to-post caption with 4-6 relevant hashtags"
}}

Rules:
- 5 or 6 scenes. Total voiceover must be 60-75 words (about 30 seconds spoken).
- Scene 1 is a scroll-stopping hook (a question or bold claim). The last scene is a clear call to action that mentions "{business}".
- Simple spoken English, short sentences, no emojis, no hashtags inside voiceover.
- keywords must describe something filmable (e.g. "busy kitchen chef", "happy customer smiling"), never abstract ideas or brand names.
- Only state facts that are in the brief. Do not invent prices, discounts, phone numbers or claims.
"""


def _count_words(scenes: list[dict]) -> int:
    return sum(len(s["voiceover"].split()) for s in scenes)


def _clean(data: dict, business: str) -> dict:
    scenes = []
    for s in data.get("scenes", []):
        vo = str(s.get("voiceover", "")).strip()
        if not vo:
            continue
        kw = str(s.get("keywords", "")).strip() or business
        scenes.append({"voiceover": vo, "keywords": kw})
    if len(scenes) < 3:
        raise ValueError("script has too few scenes")
    scenes = scenes[:7]
    while _count_words(scenes) > 95 and len(scenes) > 3:
        scenes.pop(-2)  # drop a middle scene, keep hook and call to action
    caption = str(data.get("caption", "")).strip() or business
    return {"scenes": scenes, "caption": caption}


def _fallback(business: str, brief: str) -> dict:
    """Used when Gemini is not configured or fails, so the bot still produces a video."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", brief) if p.strip()]
    parts = parts[:3] or [brief]
    scenes = [{"voiceover": f"Looking for {business}? You just found it.", "keywords": "business storefront"}]
    for p in parts:
        words = [w for w in re.findall(r"[A-Za-z]{4,}", p)][:2]
        scenes.append({"voiceover": p.rstrip(".") + ".", "keywords": " ".join(words) or "small business"})
    scenes.append({"voiceover": f"Message {business} today and get started.", "keywords": "happy customer"})
    return {"scenes": scenes, "caption": f"{business}\n\n{brief}"}


async def write_script(business: str, brief: str, tone_key: str, has_photos: bool) -> dict:
    if not config.GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set, using fallback script")
        return _fallback(business, brief)

    tone = config.TONES[tone_key][1]
    photo_note = "The owner also sent product photos, so some scenes will show their real product." if has_photos else ""
    prompt = PROMPT.format(business=business, brief=brief, tone=tone, photo_note=photo_note)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        return _clean(json.loads(text), business)
    except Exception:
        log.exception("Gemini script failed, using fallback")
        return _fallback(business, brief)
