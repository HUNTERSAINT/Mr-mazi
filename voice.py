"""Voiceovers. Two engines, both free:

- edge-tts: Microsoft Edge voices, no key (the preset voices)
- Pocket TTS (Kyutai, MIT licence): clones the user's own voice from a short voice note, runs on CPU
"""
import asyncio
import logging
import subprocess
import threading
from pathlib import Path

import edge_tts

import config

log = logging.getLogger(__name__)

FALLBACK_VOICE = "en-NG-EzinneNeural"
CLONE_TIMEOUT = 240  # seconds per scene on a slow CPU

_model = None
_states: dict[int, tuple[float, object]] = {}
_lock = threading.Lock()


# ---------- preset voices ----------

async def synth_scene(text: str, out: Path, voice: str, rate: str = "+0%") -> Path:
    await edge_tts.Communicate(text, voice, rate=rate).save(str(out))
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("Text-to-speech returned no audio")
    return out


# ---------- the user's own voice ----------

def sample_path(uid: int) -> Path:
    return config.VOICES_DIR / f"{uid}.wav"


def has_sample(uid: int) -> bool:
    return sample_path(uid).exists()


def delete_sample(uid: int) -> None:
    sample_path(uid).unlink(missing_ok=True)
    _states.pop(uid, None)


def prepare_sample(src: Path, uid: int) -> float:
    """Convert a voice note to a clean 24 kHz mono wav (max 25 s). Returns its length in seconds."""
    dest = sample_path(uid)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-t", "25", "-ac", "1", "-ar", "24000",
                    "-af", "highpass=f=70,loudnorm=I=-20:TP=-2", str(dest)], check=True, timeout=120)
    _states.pop(uid, None)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(dest)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def warmup() -> None:
    """Load the cloning model at startup so the first voice request isn't slow. Never raises."""
    global _model
    try:
        from pocket_tts import TTSModel
        with _lock:
            if _model is None:
                _model = TTSModel.load_model()
        log.info("Voice cloning model ready")
    except Exception as e:
        log.warning("Voice cloning model not available: %s", str(e)[:200])


def _clone_sync(uid: int, text: str, out_wav: Path) -> Path:
    global _model
    import scipy.io.wavfile as wavfile
    from pocket_tts import TTSModel

    with _lock:
        if _model is None:
            _model = TTSModel.load_model()
        sample = sample_path(uid)
        mtime = sample.stat().st_mtime
        cached = _states.get(uid)
        if not cached or cached[0] != mtime:  # building the voice state is slow, so keep it
            _states[uid] = (mtime, _model.get_state_for_audio_prompt(str(sample)))
        audio = _model.generate_audio(_states[uid][1], text)
        wavfile.write(str(out_wav), _model.sample_rate, audio.numpy())
    if not out_wav.exists() or out_wav.stat().st_size < 1000:
        raise RuntimeError("Voice cloning returned no audio")
    return out_wav


async def clone_scene(uid: int, text: str, out_wav: Path) -> Path:
    return await asyncio.wait_for(asyncio.to_thread(_clone_sync, uid, text, out_wav), CLONE_TIMEOUT)


async def synth_all(scenes: list[dict], job_dir: Path, voice_key: str, rate: str, uid: int) -> tuple[list[Path], bool]:
    """Voiceover for every scene. Returns (audio files, used_fallback_voice).

    "mine" uses the user's cloned voice. If cloning fails for any reason, the whole video is
    re-recorded with a preset voice so it never mixes two voices.
    """
    if voice_key == "mine" and has_sample(uid):
        try:
            files = [await clone_scene(uid, s["voiceover"], job_dir / f"v{i}.wav") for i, s in enumerate(scenes)]
            return files, False
        except Exception:
            log.exception("Voice cloning failed, using preset voice")
        voice_name, fell_back = FALLBACK_VOICE, True
    else:
        voice_name = config.VOICES.get(voice_key, config.VOICES["ng_f"])[1] or FALLBACK_VOICE
        fell_back = voice_key == "mine"
    files = [await synth_scene(s["voiceover"], job_dir / f"v{i}.mp3", voice_name, rate)
             for i, s in enumerate(scenes)]
    return files, fell_back
