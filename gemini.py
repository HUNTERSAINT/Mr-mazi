"""Small Gemini REST helper shared by the product analyzer and the script writer."""
import json
import logging
import re

import httpx

import config

log = logging.getLogger(__name__)


async def generate_json(parts: list[dict], temperature: float = 0.7) -> dict:
    """Send text/image parts, get parsed JSON back. Raises on failure.

    Google retires models often, so a 404 moves on to the next model in the list.
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": temperature},
    }
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    async with httpx.AsyncClient(timeout=120) as client:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            r = await client.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
            if r.status_code == 404:
                log.warning("Gemini model %s not found, trying the next one", model)
                continue
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
            return json.loads(text)
    raise RuntimeError("none of the Gemini models were available")
