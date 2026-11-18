"""Telegram bot: businesses describe what they sell, get back a 30s vertical video."""
import asyncio
import datetime as dt
import hashlib
import logging
import shutil
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

import config
from assemble import render_video
from script_writer import write_script
from visuals import fetch_visuals
from voice import synth_scene

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

NAME, BRIEF, PHOTOS, TONE, VOICE, LENGTH = range(6)

WELCOME = (
    "Hi! I make 30-second social media videos for your business.\n\n"
    "/new  – create a video\n"
    "/logo – save your logo (shown in the corner of every video)\n"
    "/cancel – stop what you're doing\n\n"
    "Videos are vertical (9:16), ready for TikTok, Reels, Shorts and WhatsApp Status."
)


# ---------- helpers ----------

def _uploads(uid: int) -> Path:
    d = config.UPLOADS / str(uid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _used_today(app: Application, uid: int) -> int:
    day, n = app.bot_data.setdefault("usage", {}).get(uid, (None, 0))
    return n if day == dt.date.today() else 0


def _count_use(app: Application, uid: int) -> None:
    app.bot_data["usage"][uid] = (dt.date.today(), _used_today(app, uid) + 1)


async def _save_image(msg, dest_stem: Path) -> Path | None:
    if msg.photo:
        f, ext = await msg.photo[-1].get_file(), ".jpg"
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        f = await msg.document.get_file()
        ext = Path(msg.document.file_name or "x.png").suffix or ".png"
    else:
        return None
    dest = dest_stem.with_suffix(ext)
    await f.download_to_drive(dest)
    return dest


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in row] for row in rows])


# ---------- conversation ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME)


async def new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in context.application.bot_data.setdefault("busy", set()):
        await update.message.reply_text("I'm still making your last video. Please wait for it first.")
        return ConversationHandler.END
    if uid not in config.ADMIN_IDS and _used_today(context.application, uid) >= config.DAILY_LIMIT:
        await update.message.reply_text(f"You've used your {config.DAILY_LIMIT} free videos for today. Come back tomorrow!")
        return ConversationHandler.END
    shutil.rmtree(_uploads(uid), ignore_errors=True)
    context.user_data.clear()
    await update.message.reply_text("What's your business name?")
    return NAME


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["business"] = update.message.text.strip()[:80]
    await update.message.reply_text(
        "Tell me about the video in a few lines: what you sell or offer, who it's for, "
        "and anything special (location, delivery, opening hours). I only use facts you give me."
    )
    return BRIEF


async def got_brief(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["brief"] = update.message.text.strip()[:1200]
    context.user_data["photos"] = []
    await update.message.reply_text(
        f"Send up to {config.MAX_PHOTOS} photos of your product or shop so they appear in the video, "
        "then tap /done. Or tap /skip to use stock footage only."
    )
    return PHOTOS


async def got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data.setdefault("photos", [])
    saved = await _save_image(update.message, _uploads(update.effective_user.id) / f"p{len(photos)}")
    if not saved:
        await update.message.reply_text("Please send an image, or /skip.")
        return PHOTOS
    photos.append(str(saved))
    if len(photos) >= config.MAX_PHOTOS:
        return await ask_tone(update, context)
    await update.message.reply_text(f"Got {len(photos)}. Send more or tap /done.")
    return PHOTOS


async def ask_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[(label, f"tone:{k}")] for k, (label, _, _) in config.TONES.items()]
    await update.effective_message.reply_text("What tone should the video have?", reply_markup=_kb(rows))
    return TONE


async def got_tone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["tone"] = q.data.split(":")[1]
    rows = [[(label, f"voice:{k}")] for k, (label, _) in config.VOICES.items()]
    await q.edit_message_text("Pick a voice:", reply_markup=_kb(rows))
    return VOICE


async def got_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["voice"] = q.data.split(":")[1]
    rows = [[(f"⏱ {s} seconds", f"len:{s}")] for s in config.LENGTHS]
    await q.edit_message_text("How long should the video be? Longer videos take longer to make.",
                              reply_markup=_kb(rows))
    return LENGTH


async def got_length(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["length"] = int(q.data.split(":")[1])
    await q.edit_message_text("Great, starting your video. This can take a few minutes.")
    keys = ("business", "brief", "photos", "tone", "voice", "length")
    params = {k: context.user_data[k] for k in keys}
    context.application.bot_data.setdefault("last", {})[update.effective_user.id] = params
    context.application.create_task(run_job(context.application, q.message.chat_id, update.effective_user.id, params))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send /new whenever you're ready.")
    return ConversationHandler.END


# ---------- logo ----------

async def logo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_logo"] = True
    await update.message.reply_text("Send your logo as an image (a PNG with transparent background looks best).")


async def logo_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.pop("awaiting_logo", False):
        await update.message.reply_text("Send /new to make a video, or /logo to save your logo.")
        return
    uid = update.effective_user.id
    for old in config.LOGOS.glob(f"{uid}.*"):
        old.unlink(missing_ok=True)
    saved = await _save_image(update.message, config.LOGOS / str(uid))
    await update.message.reply_text("Logo saved ✅" if saved else "That wasn't an image. Try /logo again.")


# ---------- the job ----------

async def _edit(msg, text: str):
    try:
        await msg.edit_text(text)
    except Exception:
        pass


async def run_job(app: Application, chat_id: int, uid: int, p: dict):
    busy = app.bot_data.setdefault("busy", set())
    if uid in busy:
        return
    busy.add(uid)
    sem: asyncio.Semaphore = app.bot_data["sem"]
    status = await app.bot.send_message(chat_id, "⏳ In the queue…" if sem.locked() else "✍️ Writing your script…")
    job_dir = config.WORK / f"{uid}_{int(time.time())}"
    try:
        async with sem:
            job_dir.mkdir(parents=True, exist_ok=True)
            await _edit(status, "✍️ Writing your script…")
            script = await write_script(p["business"], p["brief"], p["tone"], bool(p["photos"]), p.get("length", 30))

            await _edit(status, "🎞 Finding visuals…")
            visuals = await fetch_visuals(script["scenes"], p["photos"], job_dir,
                                          script.get("product_look", ""), lambda t: _edit(status, t))

            await _edit(status, "🎙 Recording the voiceover…")
            voice_name = config.VOICES[p["voice"]][1]
            rate = config.TONES[p["tone"]][2]
            audio = [await synth_scene(s["voiceover"], job_dir / f"v{i}.mp3", voice_name, rate)
                     for i, s in enumerate(script["scenes"])]

            await _edit(status, "🎬 Rendering your video (about a minute)…")
            brand = config.PALETTE[int(hashlib.md5(p["business"].encode()).hexdigest(), 16) % len(config.PALETTE)]
            logos = list(config.LOGOS.glob(f"{uid}.*"))
            final = await asyncio.to_thread(
                render_video, job_dir, script["scenes"], visuals, audio, brand, logos[0] if logos else None)

            with open(final, "rb") as fh:
                await app.bot.send_video(chat_id, fh, width=1080, height=1920, supports_streaming=True,
                                         read_timeout=120, write_timeout=120)
        await app.bot.send_message(chat_id, "Suggested caption:\n\n" + script["caption"],
                                   reply_markup=_kb([[("🔁 Make another version", "redo")]]))
        if uid not in config.ADMIN_IDS:
            _count_use(app, uid)
        await status.delete()
    except Exception:
        log.exception("Job failed")
        await _edit(status, "Sorry, something went wrong while making your video. Please try /new again.")
    finally:
        busy.discard(uid)
        shutil.rmtree(job_dir, ignore_errors=True)


async def redo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    params = context.application.bot_data.get("last", {}).get(uid)
    if not params or not all(Path(x).exists() for x in params["photos"]):
        await q.message.reply_text("I no longer have your details. Send /new to start again.")
        return
    if uid not in config.ADMIN_IDS and _used_today(context.application, uid) >= config.DAILY_LIMIT:
        await q.message.reply_text(f"You've used your {config.DAILY_LIMIT} free videos for today. Come back tomorrow!")
        return
    context.application.create_task(run_job(context.application, q.message.chat_id, uid, params))


# ---------- main ----------

async def _post_init(app: Application):
    app.bot_data["sem"] = asyncio.Semaphore(config.MAX_PARALLEL_JOBS)


def main():
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in your .env file (get it from @BotFather).")
    app = Application.builder().token(config.TELEGRAM_TOKEN).post_init(_post_init).build()

    img = filters.PHOTO | filters.Document.IMAGE
    text = filters.TEXT & ~filters.COMMAND
    conv = ConversationHandler(
        entry_points=[CommandHandler(["new", "video"], new)],
        states={
            NAME: [MessageHandler(text, got_name)],
            BRIEF: [MessageHandler(text, got_brief)],
            PHOTOS: [MessageHandler(img, got_photo),
                     CommandHandler(["done", "skip"], ask_tone)],
            TONE: [CallbackQueryHandler(got_tone, pattern="^tone:")],
            VOICE: [CallbackQueryHandler(got_voice, pattern="^voice:")],
            LENGTH: [CallbackQueryHandler(got_length, pattern="^len:")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler(["new", "video"], new)],
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("logo", logo_cmd))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(redo, pattern="^redo$"))
    app.add_handler(MessageHandler(img, logo_image))
    log.info("Bot running")
    app.run_polling()


if __name__ == "__main__":
    main()
