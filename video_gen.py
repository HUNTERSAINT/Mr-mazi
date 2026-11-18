"""Short AI video clip generation (image-to-video). Providers are swappable via VIDEO_PROVIDER:

  hf    free: a public Hugging Face Space through gradio_client (shared GPU, can be slow or down)
  veo   paid: Google Veo through the Gemini API (needs billing enabled on the Google project)
  none  no clip generation, the bot animates still images instead

generate_clip() returns the path of a short mp4 or None. Callers must always have a fallback.
"""
import asyncio
import logging
import time
from pathlib import Path

import config

log = logging.getLogger(__name__)
_hf_client = None


def enabled() -> bool:
    return config.VIDEO_PROVIDER in ("hf", "veo")


def _hf_clip(prompt: str, image: str | None, dest: Path) -> Path | None:
    global _hf_client
    from gradio_client import Client, handle_file

    if _hf_client is None:
        _hf_client = Client(config.HF_SPACE, token=config.HF_TOKEN or None)
    kwargs = dict(prompt=prompt, duration=config.CLIP_SECONDS, width=config.CLIP_WIDTH,
                  height=config.CLIP_HEIGHT, api_name=config.HF_API_NAME)
    if image:
        kwargs["input_image"] = handle_file(image)
    result = _hf_client.predict(**kwargs)

    video = result[0] if isinstance(result, (list, tuple)) else result
    if isinstance(video, dict):
        video = video.get("video") or video.get("path")
    if not video:
        return None
    dest.write_bytes(Path(video).read_bytes())
    return dest


def _veo_clip(prompt: str, image: str | None, dest: Path) -> Path | None:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    kwargs = {}
    if image:
        mime = "image/png" if image.lower().endswith(".png") else "image/jpeg"
        kwargs["image"] = types.Image(image_bytes=Path(image).read_bytes(), mime_type=mime)
    op = client.models.generate_videos(
        model=config.VEO_MODEL, prompt=prompt,
        config=types.GenerateVideosConfig(aspect_ratio="9:16"), **kwargs)
    deadline = time.time() + config.CLIP_TIMEOUT
    while not op.done:
        if time.time() > deadline:
            raise TimeoutError("Veo took too long")
        time.sleep(10)
        op = client.operations.get(op)
    video = op.response.generated_videos[0]
    client.files.download(file=video.video)
    video.video.save(str(dest))
    return dest


async def generate_clip(prompt: str, image: str | None, dest: Path) -> Path | None:
    """Generate one short clip. Never raises; returns None on any failure."""
    fn = {"hf": _hf_clip, "veo": _veo_clip}.get(config.VIDEO_PROVIDER)
    if fn is None:
        return None
    try:
        path = await asyncio.wait_for(asyncio.to_thread(fn, prompt, image, dest), config.CLIP_TIMEOUT)
        if path and path.exists() and path.stat().st_size > 10_000:
            return path
    except Exception:
        log.exception("Clip generation failed (%s)", config.VIDEO_PROVIDER)
    return None
