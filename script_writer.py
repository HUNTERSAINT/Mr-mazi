"""Turns a business brief into a scene-by-scene script (voiceover + detailed visual prompt) with Gemini."""
import json
import logging
import re

import httpx

import config

log = logging.getLogger(__name__)

PROMPT = """You write scripts for {length}-second vertical social media product videos (TikTok / Reels / Status) for small businesses.

Business name: {business}
Brief from the owner: {brief}
Tone: {tone}
{photo_note}

Return ONLY valid JSON in exactly this shape:
{{
  "product_look": "one sentence describing exactly how the product looks (colour, shape, material, packaging) so every scene stays consistent",
  "scenes": [
    {{
      "voiceover": "spoken line",
      "visual_prompt": "one detailed sentence: what the camera shows and how it moves",
      "keywords": "2-4 plain words describing the shot"
    }}
  ],
  "caption": "ready-to-post caption with 4-6 relevant hashtags"
}}

Rules:
- Exactly {n_scenes} scenes. Each voiceover is 10-14 words (about 5 seconds spoken). Total about {words} words.
- Scene 1 is a scroll-stopping hook. The last scene is a clear call to action that mentions "{business}".
- Cover the product in detail across scenes: close-up of texture/details, it being used, key benefit, who it is for, variety or sizes if the brief mentions them.
- visual_prompt must name the product, describe lighting, background and a camera move (slow push-in, orbit, pan, tilt, handheld). Every scene's visual_prompt must reuse the product_look. No on-screen text, logos or watermarks, no people's faces in close-up.
- Simple spoken English, no emojis, no hashtags inside voiceover.
- Only state facts that are in the brief. Do not invent prices, discounts, phone numbers or claims.
"""


def _count_words(scenes: list[dict]) -> int:
    return sum(len(s["voiceover"].split()) for s in scenes)


def _clean(data: dict, business: str, length: int) -> dict:
    look = str(data.get("product_look", "")).strip()
    scenes = []
    for s in data.get("scenes", []):
        vo = str(s.get("voiceover", "")).strip()
        if not vo:
            continue
        kw = str(s.get("keywords", "")).strip() or business
        vp = str(s.get("visual_prompt", "")).strip() or kw
        scenes.append({"voiceover": vo, "keywords": kw, "visual_prompt": vp})
    if len(scenes) < 3:
        raise ValueError("script has too few scenes")
    n = config.LENGTHS[length]
    scenes = scenes[:n + 1]
    while _count_words(scenes) > length * 3.2 and len(scenes) > 3:
        scenes.pop(-2)  # drop a middle scene, keep hook and call to action
    caption = str(data.get("caption", "")).strip() or business
    return {"scenes": scenes, "caption": caption, "product_look": look}


def _fallback(business: str, brief: str) -> dict:
    """Used when Gemini is not configured or fails, so the bot still produces a video."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", brief) if p.strip()]
    parts = parts[:4] or [brief]
    scenes = [{"voiceover": f"Looking for {business}? You just found it.",
               "keywords": "business storefront", "visual_prompt": f"{business} storefront, slow push-in"}]
    for p in parts:
        words = [w for w in re.findall(r"[A-Za-z]{4,}", p)][:3]
        kw = " ".join(words) or "small business"
        scenes.append({"voiceover": p.rstrip(".") + ".", "keywords": kw,
                       "visual_prompt": f"{kw}, close-up, soft lighting, slow camera move"})
    scenes.append({"voiceover": f"Message {business} today and get started.",
                   "keywords": "happy customer", "visual_prompt": "happy customer holding product, smiling"})
    return {"scenes": scenes, "caption": f"{business}\n\n{brief}", "product_look": ""}


async def write_script(business: str, brief: str, tone_key: str, has_photos: bool, length: int = 30) -> dict:
    if not config.GEMINI_API_KEY:
        log.warning("GEMINI_API_KEY not set, using fallback script")
        return _fallback(business, brief)

    tone = config.TONES[tone_key][1]
    photo_note = ("The owner sent real product photos. Each scene will animate one of them, so describe motion "
                  "and camera moves around the real product." if has_photos else "")
    prompt = PROMPT.format(business=business, brief=brief, tone=tone, photo_note=photo_note,
                           length=length, n_scenes=config.LENGTHS[length], words=int(length * 2.4))
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9},
    }
    # Google retires models often. If the configured one answers 404, try the current ones in turn.
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            for model in models:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                r = await client.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
                if r.status_code == 404:
                    log.warning("Gemini model %s not found, trying the next one", model)
                    continue
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                break
            else:
                raise RuntimeError("none of the Gemini models were available")
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
        return _clean(json.loads(text), business, length)
    except Exception:
        log.exception("Gemini script failed, using fallback")
        return _fallback(business, brief)
