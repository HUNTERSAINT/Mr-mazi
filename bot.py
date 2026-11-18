"""Telegram bot: send a product photo, the bot identifies the product and makes an explainer video."""
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
import profiles
from analyze import analyze_product, manual_analysis, summary_text, to_brief
from assemble import render_video
from script_writer import write_script
from visuals import fetch_visuals
from voice import synth_scene

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

NAME, PHOTOS, CONFIRM, FIX, OPTIONS, TONE, VOICE, LENGTH = range(8)

WELCOME = (
    "Hi! Send me a photo of your product and I'll work out what it is, then make a short video "
    "explaining what it is and how it works, ready to post.\n\n"
    "/new – make a video\n"
    "/name – change your business name\n"
    "/logo – save your logo (shown in the corner of every video)\n"
    "/cancel – stop what you're doing\n\n"
    "Videos are vertical (9:16) for TikTok, Reels, Shorts and WhatsApp Status."
)

PHOTO_PROMPT = (
    f"Send a clear photo of the product (up to {config.MAX_PHOTOS} angles), then tap /done.\n"
    "Tip: add a caption with the brand, flavour or price if you want it mentioned."
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


def _over_limit(app: Application, uid: int) -> bool:
    return uid not in config.ADMIN_IDS and _used_today(app, uid) >= config.DAILY_LIMIT


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
    if _over_limit(context.application, uid):
        await update.message.reply_text(f"You've used your {config.DAILY_LIMIT} free videos for today. Come back tomorrow!")
        return ConversationHandler.END
    shutil.rmtree(_uploads(uid), ignore_errors=True)
    context.user_data.clear()
    context.user_data.update(photos=[], hint="")
    business = profiles.get(uid).get("business")
    if not business:
        await update.message.reply_text("First, what's your business name? I'll save it for next time.")
        return NAME
    context.user_data["business"] = business
    await update.message.reply_text(f"Making a video for {business}.\n\n{PHOTO_PROMPT}")
    return PHOTOS


async def got_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    business = update.message.text.strip()[:80]
    profiles.update(update.effective_user.id, business=business)
    context.user_data["business"] = business
    await update.message.reply_text(PHOTO_PROMPT)
    return PHOTOS


async def got_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = context.user_data.setdefault("photos", [])
    saved = await _save_image(update.message, _uploads(update.effective_user.id) / f"p{len(photos)}")
    if not saved:
        await update.message.reply_text("Please send an image.")
        return PHOTOS
    photos.append(str(saved))
    if update.message.caption:
        context.user_data["hint"] = (context.user_data.get("hint", "") + " " + update.message.caption).strip()[:500]
    if len(photos) >= config.MAX_PHOTOS:
        return await analyze_step(update, context)
    await update.message.reply_text(f"Got {len(photos)}. Send another angle or tap /done.")
    return PHOTOS


async def done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("photos"):
        await update.message.reply_text("I need at least one photo of the product first.")
        return PHOTOS
    return await analyze_step(update, context)


async def _show_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE, a: dict | None):
    msg = update.effective_message
    if not a:
        await msg.reply_text("I couldn't identify it from the photo. Tell me what it is in a line or two "
                             "(name, brand, what it does) and I'll build the video from that.")
        return FIX
    context.user_data["analysis"] = a
    await msg.reply_text(summary_text(a), reply_markup=_kb([[("✅ Looks right", "ok"), ("✏️ Correct it", "fix")]]))
    return CONFIRM


async def analyze_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("🔍 Looking at your product…")
    a = await analyze_product(context.user_data["photos"], context.user_data["business"],
                              context.user_data.get("hint", ""))
    return await _show_analysis(update, context, a)


async def got_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "fix":
        await q.edit_message_reply_markup(None)
        await q.message.reply_text("Tell me what the product really is (name, brand, what it does, how it works).")
        return FIX
    await q.edit_message_reply_markup(None)
    return await ask_options(update, context)


async def got_fix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()[:800]
    context.user_data["hint"] = text
    await update.message.reply_text("👍 Got it, checking again…")
    a = await analyze_product(context.user_data["photos"], context.user_data["business"], text)
    return await _show_analysis(update, context, a or manual_analysis(text))


def _usual(uid: int) -> dict | None:
    p = profiles.get(uid)
    if all(k in p for k in ("tone", "voice", "length")) and p["tone"] in config.TONES \
            and p["voice"] in config.VOICES and p["length"] in config.LENGTHS:
        return p
    return None


async def ask_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.effective_message
    usual = _usual(uid)
    if usual:
        label = (f"⚡ Same as last time ({config.TONES[usual['tone']][0]}, "
                 f"{config.VOICES[usual['voice']][0]}, {usual['length']}s)")
        await msg.reply_text("Ready to make the video.", reply_markup=_kb([[(label, "usual")], [("🎛 Change settings", "custom")]]))
        return OPTIONS
    return await ask_tone(update, context)


async def got_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "usual":
        u = _usual(update.effective_user.id)
        if u:
            context.user_data.update(tone=u["tone"], voice=u["voice"], length=u["length"])
            await q.edit_message_text("Great, starting your video. This can take a few minutes.")
            return _launch(update, context, q.message.chat_id)
    return await ask_tone(update, context)


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
    profiles.update(update.effective_user.id, tone=context.user_data["tone"],
                    voice=context.user_data["voice"], length=context.user_data["length"])
    await q.edit_message_text("Great, starting your video. This can take a few minutes.")
    return _launch(update, context, q.message.chat_id)


def _launch(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    ud = context.user_data
    params = {
        "business": ud["business"], "brief": to_brief(ud["analysis"], ud.get("hint", "")),
        "photos": ud["photos"], "tone": ud["tone"], "voice": ud["voice"], "length": ud["length"],
    }
    uid = update.effective_user.id
    context.application.bot_data.setdefault("last", {})[uid] = params
    context.application.create_task(run_job(context.application, chat_id, uid, params))
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Cancelled. Send /new whenever you're ready.")
    return ConversationHandler.END


# ---------- business name and logo ----------

async def name_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_name"] = True
    await update.message.reply_text("What's your business name?")


async def name_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.pop("awaiting_name", False):
        profiles.update(update.effective_user.id, business=update.message.text.strip()[:80])
        await update.message.reply_text("Saved ✅")


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

            await _edit(status, "🎞 Creating the visuals…")
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
        await q.message.reply_text("I no longer have your photos. Send /new to start again.")
        return
    if _over_limit(context.application, uid):
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
    new_cmd = CommandHandler(["new", "video"], new)
    conv = ConversationHandler(
        entry_points=[new_cmd],
        states={
            NAME: [MessageHandler(text, got_name)],
            PHOTOS: [MessageHandler(img, got_photo), CommandHandler("done", done)],
            CONFIRM: [CallbackQueryHandler(got_confirm, pattern="^(ok|fix)$")],
            FIX: [MessageHandler(text, got_fix)],
            OPTIONS: [CallbackQueryHandler(got_options, pattern="^(usual|custom)$")],
            TONE: [CallbackQueryHandler(got_tone, pattern="^tone:")],
            VOICE: [CallbackQueryHandler(got_voice, pattern="^voice:")],
            LENGTH: [CallbackQueryHandler(got_length, pattern="^len:")],
        },
        fallbacks=[CommandHandler("cancel", cancel), new_cmd],
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("logo", logo_cmd))
    app.add_handler(CommandHandler("name", name_cmd))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(redo, pattern="^redo$"))
    app.add_handler(MessageHandler(img, logo_image))
    app.add_handler(MessageHandler(text, name_text), group=1)
    log.info("Bot running")
    app.run_polling()


if __name__ == "__main__":
    main()
