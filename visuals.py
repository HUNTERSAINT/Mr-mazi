"""Collects a visual for every scene. Best to worst:

1. a short AI-generated clip, animated from the owner's product photo (or from a generated image)
2. the owner's photo with a slow zoom
3. a Pollinations AI image with a slow zoom (free, no key)
4. a colour card
"""
import logging
import random
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import quote

import httpx

import config
import video_gen

log = logging.getLogger(__name__)

STYLE = "vertical social media ad photo, professional lighting, sharp focus, no text, no watermark"
MAX_CLIP_FAILS_IN_A_ROW = 2  # stop waiting on a broken/overloaded provider after this many failures


async def _generate_image(client: httpx.AsyncClient, prompt_text: str, dest: Path) -> Path | None:
    prompt = quote(f"{prompt_text}, {STYLE}")
    for attempt in range(2):
        url = (f"https://image.pollinations.ai/prompt/{prompt}"
               f"?width=1080&height=1920&nologo=true&seed={random.randint(1, 10**6)}")
        try:
            r = await client.get(url)
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/") or len(r.content) < 5000:
                raise ValueError("not an image")
            dest.write_bytes(r.content)
            return dest
        except Exception:
            log.warning("Image generation failed (attempt %d) for %r", attempt + 1, prompt_text[:60])
    return None


async def fetch_visuals(scenes: list[dict], photos: list[str], job_dir: Path, product_look: str = "",
                        progress: Callable[[str], Awaitable[None]] | None = None) -> list[dict]:
    """Returns one {"kind": "video"|"image"|"card", "path": ...} per scene."""
    results: list[dict] = []
    fails = 0
    use_clips = video_gen.enabled()
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        for i, scene in enumerate(scenes):
            if progress:
                await progress(f"🎞 Creating scene {i + 1} of {len(scenes)}…")

            photo = photos[i % len(photos)] if photos else None
            still = photo
            if not still:
                gen = await _generate_image(client, scene["visual_prompt"], job_dir / f"gen{i}.jpg")
                still = str(gen) if gen else None

            if use_clips and fails < MAX_CLIP_FAILS_IN_A_ROW:
                prompt = scene["visual_prompt"]
                if product_look and product_look.lower() not in prompt.lower():
                    prompt = f"{prompt}. Product: {product_look}"
                clip = await video_gen.generate_clip(prompt, still, job_dir / f"clip{i}.mp4")
                if clip:
                    fails = 0
                    results.append({"kind": "video", "path": str(clip)})
                    continue
                fails += 1

            results.append({"kind": "image", "path": still} if still else {"kind": "card", "path": ""})
    return results
