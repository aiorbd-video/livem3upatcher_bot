import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import RetryAfter
import database as db
from config import BOT_USERNAME, CHANNEL_ID, ADMIN_ID, DELETE_TIME, bd_tz, FORCE_CHANNELS, HEADERS
import utils

admin_keyboard = ReplyKeyboardMarkup([
    ["➕ লিংক যুক্ত করুন", "➖ লিংক মুছুন"],
    ["📊 সোর্স লাইভ স্ট্যাটাস", "👥 মোট ইউজার"],
    ["🔁 সব নতুন করে পোস্ট করুন", "🔄 ফোর্স চেক"],
    ["📢 ব্রডকাস্ট", "📊 অ্যানালিটিক্স"],
    ["🚫 ইউজার ব্যান", "✅ আনব্যান"],
    ["⚙️ সিস্টেম স্ট্যাটাস"],
], resize_keyboard=True)

TARGET_MAP = {"Telegram Only": "tg", "Web Only": "web", "Both": "both"}

async def is_user_joined(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    if not FORCE_CHANNELS: return True
    for channel in FORCE_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]: return False
        except Exception: return False
    return True

def get_force_join_keyboard(payload=None):
    buttons = [[InlineKeyboardButton(f"✅ চ্যানেল এ যুক্ত হোন ({ch})", url=f"https://t.me/{ch.replace('@', '')}")] for ch in FORCE_CHANNELS]
    buttons.append([InlineKeyboardButton("🔄 জয়েন করেছি (চেক করুন)", callback_data=f"check_join|{payload}" if payload else "check_join")])
    return InlineKeyboardMarkup(buttons)

async def post_to_tg_channel(context, title, category, logo, short_id):
    now_time = datetime.now(bd_tz).strftime("%I:%M %p (%d %b)")
    deep_link = f"https://t.me/{BOT_USERNAME}?start={short_id}"
    text = f"📡 <b>{title}</b>\n\n📂 <b>ক্যাটাগরি:</b> {category}\n🟢 <b>[LIVE] লাইভ স্ট্রিমটি সচল আছে</b>\n\n🔗 <a href='{deep_link}'>সরাসরি দেখতে এখানে ক্লিক করুন</a>\n\n🔄 <b>সর্বশেষ আপডেট:</b> <code>{now_time}</code>\n⚡ <i>All In One Reborn</i>"
    try:
        await asyncio.sleep(1)
        if logo and logo.startswith("http"):
            msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, parse_mode="HTML")
        else:
            msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
        return msg.message_id
    except RetryAfter as flood_err:
        await asyncio.sleep(flood_err.retry_after)
        return await post_to_tg_channel(context, title, category, logo, short_id)
    except Exception: return None

async def send_stream_message(context, chat_id, data, message_to_edit=None):
    msg_text = f"✅ <b>স্ট্রিম অ্যাক্সেস অনুমোদিত!</b>\n\n🔗 <b>আপনার প্লেব্যাক লিংক:</b>\n<code>{data['stream_url']}</code>\n"
    
    if data.get("referer"):
        msg_text += f"\n🌐 <b>Referer:</b>\n<code>{data['referer']}</code>"
    if data.get("origin"):
        msg_text += f"\n🌍 <b>Origin:</b>\n<code>{data['origin']}</code>"
    if data.get("cookie"):
        msg_text += f"\n🍪 <b>Cookie:</b>\n<code>{data['cookie']}</code>"
    if data.get("user_agent"):
        msg_text += f"\n🛡️ <b>User-Agent:</b>\n<code>{data['user_agent']}</code>"
        
    msg_text += "\n\n<i>This Bot is Developed by Ratul.</i>\n⏳ ৫ মিনিট পর মেসেজটি ডিলিট হয়ে যাবে।"

    if message_to_edit:
        await message_to_edit.edit_text(msg_text, parse_mode="HTML")
        msg_id = message_to_edit.message_id
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
        msg_id = msg.message_id
        
    context.job_queue.run_once(delete_link_message, when=DELETE_TIME, data={"chat_id": chat_id, "message_id": msg_id})

async def delete_link_message(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    try:
        await context.bot.delete_message(chat_id=job_data["chat_id"], message_id=job_data["message_id"])
        await context.bot.send_message(chat_id=job_data["chat_id"], text="⚠️ <b>মেয়াদ উত্তীর্ণ:</b>\nলিংকের মেয়াদ শেষ।", parse_mode="HTML")
    except Exception: pass

async def fetch_m3u_content(url):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=15) as response:
                return await response.text() if response.status == 200 else None
    except Exception: return None

async def process_all_sources(context: ContextTypes.DEFAULT_TYPE, status_msg=None, force_repost=False):
    sources = await db.get_m3u_sources()
    stats = {"sources": len(sources), "total_streams": 0, "new_posts": 0, "updated_posts": 0, "failed": 0}
    if force_repost: await db.posted_col.delete_many({})

    for src_data in sources:
        source, target = src_data["url"], src_data["target"]
        content = await fetch_m3u_content(source)
        if not content:
            stats["failed"] += 1
            continue
        
        streams = utils.parse_m3u_playlist(content)
        stats["total_streams"] += len(streams)

        for item in streams:
            if not item.get("url") or not item.get("title"): continue
            stream_hash = utils.make_stream_hash(item["url"])
            existing_post = await db.posted_col.find_one({"stream_hash": stream_hash})

            if existing_post and not force_repost:
                stats["updated_posts"] += 1
                continue

            short_id = await db.create_short_link(item["url"], item["referer"], item["origin"], item["cookie"], item["user_agent"], source, title=item["title"])
            msg_id = None
            if target in ["tg", "both"]:
                msg_id = await post_to_tg_channel(context, item["title"], item["group"], item["logo"], short_id)
            if msg_id or target == "web":
                await db.save_posted_stream(item["url"], item["title"], source, msg_id, short_id, target)
                stats["new_posts"] += 1
            else: stats["failed"] += 1
    return stats

async def auto_checker_job(context: ContextTypes.DEFAULT_TYPE):
    await process_all_sources(context)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await db.is_user_banned(user_id): return await update.message.reply_text("🚫 আপনি নিষিদ্ধ (Banned)!")
    await db.add_user(user_id)
    payload = context.args[0] if context.args else None

    if not await is_user_joined(user_id, context):
        return await update.message.reply_text("❌ স্পন্সর চ্যানেলে যুক্ত হতে হবে।", reply_markup=get_force_join_keyboard(payload), parse_mode="HTML")

    if payload:
        stream_data = await db.get_stream_data(payload)
        if stream_data:
            await db.track_click(stream_data.get("title"))
            await send_stream_message(context, update.effective_chat.id, stream_data)
        else: await update.message.reply_text("❌ লিংকের মেয়াদ শেষ!")
        return

    if user_id == ADMIN_ID: await update.message.reply_text("👑 <b>Enterprise Extra Pro প্যানেল রেডি!</b>", reply_markup=admin_keyboard, parse_mode="HTML")
    else: await update.message.reply_text("✅ <b>স্বাগতম!</b> চ্যানেল থেকে লিংকে ক্লিক করে লাইভ দেখুন।", parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("check_join"):
        if await is_user_joined(query.from_user.id, context):
            payload = query.data.split("|", 1)[1] if "|" in query.data else None
            if payload:
                stream_data = await db.get_stream_data(payload)
                if stream_data: await send_stream_message(context, query.message.chat_id, stream_data, message_to_edit=query.message)
            else: await query.message.edit_text("✅ ভেরিফিকেশন সম্পন্ন!")
        else: await query.message.reply_text("❌ আপনি এখনও সব চ্যানেলে যুক্ত হননি!")

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # --- নতুন সেফটি চেক শুরু ---
    if not update.effective_user or not update.message or not update.message.text:
        return
    # --- সেফটি চেক শেষ ---

    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id != ADMIN_ID: return
    state = utils.admin_state.get(user_id)

    if state == "select_target" and text in TARGET_MAP:
        utils.admin_state[user_id] = f"add_link_{TARGET_MAP[text]}"
        return await update.message.reply_text("🔗 এবার M3U লিংকটি দিন:", reply_markup=admin_keyboard)
    if state and state.startswith("add_link_"):
        await db.add_m3u_source(text, state.split("_", 2)[2])
        await update.message.reply_text("✅ সোর্স সেভ হয়েছে।", reply_markup=admin_keyboard)
        return utils.admin_state.pop(user_id, None)

    if text == "➕ লিংক যুক্ত করুন":
        utils.admin_state[user_id] = "select_target"
        await update.message.reply_text("কোথায় পোস্ট করতে চান নির্বাচন করুন:", reply_markup=ReplyKeyboardMarkup([["Telegram Only", "Web Only", "Both"]], resize_keyboard=True))
    elif text == "👥 মোট ইউজার":
        await update.message.reply_text(f"👥 ইউজার: {len(await db.get_all_users())} জন")
    elif text == "⚙️ সিস্টেম স্ট্যাটাস":
        await update.message.reply_text(utils.get_sys_status(), parse_mode="HTML")
    elif text == "🔄 ফোর্স চেক":
        status_msg = await update.message.reply_text("🔍 স্ক্যানিং শুরু হচ্ছে...")
        stats = await process_all_sources(context, status_msg=status_msg, force_repost=False)
        await status_msg.edit_text(f"✅ স্ক্যান সম্পন্ন! নতুন পোস্ট: {stats['new_posts']}")
