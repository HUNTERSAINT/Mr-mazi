"""Central configuration. Everything comes from environment variables / .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE = Path(__file__).parent
DATA = Path(os.getenv("DATA_DIR", BASE / "data"))
WORK = DATA / "work"          # temporary render folders
UPLOADS = DATA / "uploads"    # product photos per user
LOGOS = DATA / "logos"        # one logo per user
VOICES_DIR = DATA / "voices"  # one cloned-voice sample per user
MUSIC = BASE / "assets" / "music"  # drop royalty-free .mp3 files here

for _p in (WORK, UPLOADS, LOGOS, VOICES_DIR, MUSIC):
    _p.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODELS = ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash"]

# Free-hosting protection
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "5"))              # videos per user per day
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
MAX_PARALLEL_JOBS = int(os.getenv("MAX_PARALLEL_JOBS", "1"))  # renders at the same time
MAX_PHOTOS = 4

TONES = {
    "energetic": ("🔥 Energetic", "high-energy, punchy, exciting, short sentences", "+8%"),
    "professional": ("💼 Professional", "confident, clear, trustworthy, polished", "+0%"),
    "friendly": ("😊 Friendly", "warm, conversational, relatable, like talking to a friend", "+0%"),
    "luxury": ("✨ Luxury", "elegant, calm, premium, aspirational", "-8%"),
}

VOICES = {
    "ng_f": ("🇳🇬 Ezinne (F)", "en-NG-EzinneNeural"),
    "ng_m": ("🇳🇬 Abeo (M)", "en-NG-AbeoNeural"),
    "us_f": ("🇺🇸 Aria (F)", "en-US-AriaNeural"),
    "us_m": ("🇺🇸 Guy (M)", "en-US-GuyNeural"),
    "gb_f": ("🇬🇧 Sonia (F)", "en-GB-SoniaNeural"),
    "none": ("🔇 No voiceover (music + text)", ""),
    "mine": ("🎤 My own voice", ""),  # only offered once the user has saved a sample (/myvoice)
}

# Background colours for scenes that have no footage or photo (fallback cards)
PALETTE = ["0F3D3E", "1B2A49", "5B2A86", "7A1F3D", "14532D", "7C2D12", "0C4A6E", "3F3F46"]

# Video length -> number of ~5 second scenes
LENGTHS = {30: 6, 45: 9, 60: 12}

# Short AI clip generation (see video_gen.py). hf = free Hugging Face Space, veo = paid, none = animated images only
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "hf").lower()
HF_SPACE = os.getenv("HF_SPACE", "Lightricks/ltx-2")
HF_API_NAME = os.getenv("HF_API_NAME", "/generate_video")
HF_TOKEN = os.getenv("HF_TOKEN", "")
VEO_MODEL = os.getenv("VEO_MODEL", "veo-3.1-fast-generate-preview")
CLIP_SECONDS = float(os.getenv("CLIP_SECONDS", "5"))
CLIP_WIDTH = int(os.getenv("CLIP_WIDTH", "544"))
CLIP_HEIGHT = int(os.getenv("CLIP_HEIGHT", "960"))
CLIP_TIMEOUT = int(os.getenv("CLIP_TIMEOUT", "240"))

# motion = animated motion-graphics video (default); slideshow = photos with zoom + voiceover
VIDEO_STYLE = os.getenv("VIDEO_STYLE", "motion").lower()

# looks for the motion video: luxury (black & gold, serif, calm) or bold (colourful, punchy)
THEMES = {"luxury": "🖤 Luxury (black & gold)", "bold": "⚡ Bold (colourful)"}
DEFAULT_THEME = os.getenv("DEFAULT_THEME", "luxury") if os.getenv("DEFAULT_THEME", "luxury") in THEMES else "luxury"
