"""Free text-to-speech with edge-tts (Microsoft Edge voices, no API key)."""
from pathlib import Path

import edge_tts


async def synth_scene(text: str, out: Path, voice: str, rate: str = "+0%") -> Path:
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Text-to-speech returned no audio")
    return out
