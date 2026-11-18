"""Looks at the product photo(s) with Gemini vision and works out what the product is and how it works."""
import asyncio
import base64
import logging
import subprocess
from pathlib import Path

import gemini

log = logging.getLogger(__name__)

PROMPT = """You are a product expert helping a small business make a social media video.
Look at the product photo(s) and work out what the product is.

Business that sells it: {business}
Owner's note (may be empty; if present, trust it over your own guess): {hint}

Return ONLY valid JSON in exactly this shape:
{{
  "name": "specific product name, as the owner would say it",
  "category": "type of product",
  "summary": "1-2 plain sentences: what it is and what it is for",
  "features": ["3 to 5 standout things: materials, ingredients, parts, finish, sizes you can see or that are true for this kind of product"],
  "how_it_works": "2-4 simple sentences explaining how it works, or how it is prepared / made / used, in plain words",
  "audience": "who it is for",
  "product_look": "one sentence describing exactly how it looks (colour, shape, material, packaging) for visual consistency",
  "confidence": "high | medium | low"
}}

Rules:
- Describe what is visible. Add general-knowledge facts only when they are true for this type of product.
- Never invent a brand, model, price, size, capacity, certification or health/performance claim that you cannot see or that the owner did not give.
- If a brand or model name is not readable, describe the product generically.
- If you cannot tell what it is, give your best guess and set confidence to "low".
"""


def _shrink(src: str, dest: Path) -> Path:
    """Downscale so uploads to Gemini stay small. Falls back to the original file."""
    try:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-vf",
                        "scale='min(1024,iw)':-2", "-frames:v", "1", str(dest)], check=True, timeout=60)
        return dest
    except Exception:
        return Path(src)


async def analyze_product(photos: list[str], business: str, hint: str = "") -> dict | None:
    """Returns the analysis dict, or None if Gemini is unavailable / failed."""
    try:
        parts = [{"text": PROMPT.format(business=business or "a small business", hint=hint or "none")}]
        for i, p in enumerate(photos[:4]):
            small = await asyncio.to_thread(_shrink, p, Path(p).with_name(f"small{i}.jpg"))
            mime = "image/jpeg" if small.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            parts.append({"inline_data": {"mime_type": mime,
                                          "data": base64.b64encode(small.read_bytes()).decode()}})
        a = await gemini.generate_json(parts, temperature=0.3)
        if not str(a.get("name", "")).strip():
            return None
        a["features"] = [str(f).strip() for f in (a.get("features") or []) if str(f).strip()][:5]
        a["confidence"] = str(a.get("confidence", "medium")).lower()
        return a
    except Exception:
        log.exception("Product analysis failed")
        return None


def manual_analysis(text: str) -> dict:
    """Used when the owner types the product description themselves."""
    text = text.strip()
    return {"name": text.split("\n")[0][:80], "category": "", "summary": text, "features": [],
            "how_it_works": "", "audience": "", "product_look": "", "confidence": "high"}


def to_brief(a: dict, hint: str = "") -> str:
    """Turns the analysis into the brief the script writer uses."""
    bits = [f"Product: {a['name']}" + (f" ({a['category']})" if a.get("category") else "") + ".",
            a.get("summary", "")]
    if a.get("features"):
        bits.append("Key features: " + "; ".join(a["features"]) + ".")
    if a.get("how_it_works"):
        bits.append("How it works: " + a["how_it_works"])
    if a.get("audience"):
        bits.append("Made for: " + a["audience"] + ".")
    if hint and hint not in a.get("summary", ""):
        bits.append("Owner's note: " + hint)
    return " ".join(b for b in bits if b).strip()[:1800]


def summary_text(a: dict) -> str:
    lines = [f"I think this is: {a['name']}"]
    if a.get("summary"):
        lines.append(a["summary"])
    if a.get("features"):
        lines.append("\nStandout points: " + ", ".join(a["features"]))
    if a.get("how_it_works"):
        lines.append("\nHow it works: " + a["how_it_works"])
    if a.get("confidence") == "low":
        lines.append("\nI'm not very sure about this one, so please check it.")
    return "\n".join(lines)
