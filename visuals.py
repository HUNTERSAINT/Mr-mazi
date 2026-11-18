"""Collects a visual for every scene: the owner's photo, an AI-generated image (Pollinations, free,
no API key), or a colour card as the last resort. Every image gets a slow zoom in assemble.py."""
import logging
import random
from pathlib import Path
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

STYLE = "vertical social media ad photo, professional lighting, sharp focus, no text, no watermark"


async def _generate_image(client: httpx.AsyncClient, keywords: str, dest: Path) -> dict | None:
    prompt = quote(f"{keywords}, {STYLE}")
    for attempt in range(2):
        url = (f"https://image.pollinations.ai/prompt/{prompt}"
               f"?width=1080&height=1920&nologo=true&seed={random.randint(1, 10**6)}")
        try:
            r = await client.get(url)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 5000:
                raise ValueError("not an image")
            dest.write_bytes(r.content)
            return {"kind": "image", "path": str(dest)}
        except Exception:
            log.warning("Image generation failed (attempt %d) for %r", attempt + 1, keywords)
    return None


async def fetch_visuals(scenes: list[dict], photos: list[str], job_dir: Path) -> list[dict]:
    """Returns one {"kind": "image"|"card", "path": ...} per scene."""
    results: list[dict] = []
    photo_i = 0
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        for i, scene in enumerate(scenes):
            # With photos: alternate owner photo / generated image so the real product shows up.
            if photos and i % 2 == 1:
                results.append({"kind": "image", "path": photos[photo_i % len(photos)]})
                photo_i += 1
                continue
            img = await _generate_image(client, scene["keywords"], job_dir / f"gen{i}.jpg")
            if img:
                results.append(img)
            elif photos:
                results.append({"kind": "image", "path": photos[photo_i % len(photos)]})
                photo_i += 1
            else:
                results.append({"kind": "card", "path": ""})
    return results
