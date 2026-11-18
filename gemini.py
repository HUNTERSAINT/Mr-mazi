"""Small Gemini REST helper shared by the product analyzer and the script writer."""
import asyncio
import json
import logging
import re

import httpx

import config

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}
ATTEMPTS_PER_MODEL = 2


async def generate_json(parts: list[dict], temperature: float = 0.7) -> dict:
    """Send text/image parts, get parsed JSON back. Raises only if every model fails.

    Google retires models often (404) and its newest models are often overloaded (503), so each
    model gets a quick retry and then the next model in the list is tried.
    """
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": temperature},
    }
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACK_MODELS if m != config.GEMINI_MODEL]
    last_error = "no model tried"
    async with httpx.AsyncClient(timeout=90) as client:
        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for attempt in range(ATTEMPTS_PER_MODEL):
                try:
                    r = await client.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
                except httpx.TransportError as e:
                    last_error = f"{model}: {type(e).__name__}"
                    log.warning("Gemini %s network error (%s), attempt %d", model, type(e).__name__, attempt + 1)
                    await asyncio.sleep(2)
                    continue
                if r.status_code == 404:
                    last_error = f"{model}: not found"
                    log.warning("Gemini model %s not found, trying the next one", model)
                    break
                if r.status_code in RETRY_STATUS:
                    last_error = f"{model}: HTTP {r.status_code}"
                    log.warning("Gemini %s returned %s, attempt %d", model, r.status_code, attempt + 1)
                    await asyncio.sleep(2 + attempt * 2)
                    continue
                r.raise_for_status()
                try:
                    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
                    return json.loads(text)
                except (KeyError, IndexError, ValueError) as e:
                    last_error = f"{model}: bad response ({type(e).__name__})"
                    log.warning("Gemini %s gave an unusable response, attempt %d", model, attempt + 1)
    raise RuntimeError(f"all Gemini models failed (last: {last_error})")
