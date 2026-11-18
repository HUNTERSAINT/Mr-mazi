# Business Video Bot (Telegram)

A Telegram bot that turns a short business brief into a 30-second vertical (9:16) social video. It runs on free tools only.

**Flow:** business name → brief → optional product photos → tone → voice → video + suggested caption.

**Pipeline:** Gemini writes a scene-by-scene script with a detailed visual prompt per scene → each scene becomes a short AI video clip animated from your product photo (or from a generated image) → edge-tts voiceover → FFmpeg stitches all clips into one video with captions, music and logo.

If a clip can't be generated, that scene falls back to the photo or image with a slow zoom, so a video is always delivered. Clips shorter than their voiceover are slowed slightly and the last frame is held.

**Video length:** 30, 45 or 60 seconds (6, 9 or 12 scenes of about 5 seconds).

## 1. Get the free keys

| Key | Where |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → `/newbot` |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `ADMIN_IDS` | Your Telegram user id (message @userinfobot) |

Without Gemini the bot falls back to a simple template script. If image generation fails it uses your photos, or colour cards if there are none.

## 2. Run it

You need Python 3.10+ and **FFmpeg** installed (`ffmpeg -version` should work).

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then fill in the keys
python bot.py
```

**Termux (Android):** `pkg install python ffmpeg fonts-dejavu` (or `ttf-dejavu`), then the same steps. Keep the session alive with `termux-wake-lock`.

**Docker / Render free tier:** the included `Dockerfile` installs FFmpeg and fonts. Set the variables from `.env.example` in the host's dashboard. Free hosts sleep or restart, so expect the bot to go offline sometimes. Run it as a background worker, not a web service, since it uses polling.

## Clip generation (VIDEO_PROVIDER)

| Value | What it uses | Cost |
|---|---|---|
| `hf` (default) | A public Hugging Face Space (`HF_SPACE`, default `Lightricks/ltx-2`) through gradio_client | Free, but shared GPUs: slow, queued, can be down or change its API. Set `HF_TOKEN` (free) for a better quota. |
| `veo` | Google Veo via the Gemini API (`VEO_MODEL`) | Paid, needs billing on the Google project |
| `none` | No clip generation, animated images only | Free |

After two failed clips in a row the bot stops trying for that video and uses animated images, so a broken provider never blocks delivery.

## 3. Add background music (optional)

Drop royalty-free `.mp3` files into `assets/music/`. One is picked at random per video and mixed quietly under the voice. Only use tracks you are allowed to use commercially (for example the YouTube Audio Library or Pixabay Music, check each licence).

## Commands

- `/new` – make a video
- `/logo` – save a logo, shown top-right on every video
- `/cancel` – stop the current flow

## Limits and settings

- `DAILY_LIMIT` videos per user per day (default 5). Admins are unlimited.
- `MAX_PARALLEL_JOBS` renders at once (default 1). Keep it at 1 on free hosts.
- Telegram bots can upload files up to 50 MB. A 30s video is normally 8–20 MB.

## Notes

- Voices are English (including Nigerian English). edge-tts has no Pidgin voice.
- The script prompt only allows facts from the brief, so the bot doesn't invent prices or claims. Still read each video before a client posts it.
- Scenes without a photo use AI-generated images (free, can take 10-30 s each). Photos from the business make videos feel much more specific, so encourage clients to send them.

## Upgrading later

`visuals.py` is the only file that decides where footage comes from. To offer real AI-generated motion as a paid tier, add a function there that calls a video-generation API and returns `{"kind": "video", "path": ...}` (assemble.py already renders that kind) for a scene.

## Files

```
bot.py            Telegram conversation, queue, daily limit
script_writer.py  Gemini script (+ offline fallback)
visuals.py        user photos / Pollinations images / colour cards
voice.py          edge-tts voiceover per scene
assemble.py       FFmpeg: clips, captions, music, logo, export
config.py         Settings, tones, voices
```
