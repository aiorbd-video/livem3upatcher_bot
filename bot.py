import os
import re
import time
import hashlib
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)
from telegram.error import RetryAfter

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# =========================================================
# PRO LOGGING SETUP
# =========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================================
# CONFIG & VARIABLES
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
MONGO_URI = os.getenv("MONGO_URI")

FORCE_CHANNELS = [x.strip() for x in os.getenv("FORCE_CHANNELS", "").split(",") if x.strip()]
CHECK_TIME = int(os.getenv("CHECK_TIME", "300"))
DELETE_TIME = 300  # ৫ মিনিট

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*"
}

START_TIME = time.time()

# =========================================================
# MONGODB SETUP
# =========================================================
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["all_in_one_reborn_db"]

users_col = db["users"]
sources_col = db["m3u_sources"]
posted_col = db["posted_streams"]
links_col = db["short_links"]
stats_col = db["app_stats"]

# =========================================================
# DATABASE FUNCTIONS
# =========================================================
async def add_user(user_id: int):
    await users_col.update_one(
        {"user_id": user_id}, 
        {"$setOnInsert": {"user_id": user_id, "joined_at": datetime.utcnow(), "is_banned": False}}, 
        upsert=True
    )

async def get_all_users():
    return [doc["user_id"] async for doc in users_col.find({"is_banned": {"$ne": True}})]

async def is_user_banned(user_id: int):
    user = await users_col.find_one({"user_id": user_id})
    return user.get("is_banned", False) if user else False

async def toggle_ban_user(user_id: int, ban_status: bool):
    result = await users_col.update_one({"user_id": user_id}, {"$set": {"is_banned": ban_status}})
    return result.modified_count > 0

async def add_m3u_source(url: str):
    await sources_col.update_one(
        {"url": url}, 
        {"$set": {"url": url, "added_at": datetime.utcnow()}}, 
        upsert=True
    )

async def remove_m3u_source(url: str):
    await sources_col.delete_one({"url": url})
    deleted_streams = await posted_col.delete_many({"source_url": url})
    deleted_links = await links_col.delete_many({"source_url": url})
    return deleted_streams.deleted_count, deleted_links.deleted_count

async def get_m3u_sources():
    return [doc["url"] async for doc in sources_col.find({})]

async def is_stream_posted(stream_url: str):
    return await posted_col.find_one({"stream_url": stream_url}) is not None

async def save_posted_stream(stream_url: str, title: str, source_url: str, message_id: int):
    await posted_col.update_one(
        {"stream_url": stream_url}, 
        {"$set": {
            "title": title, 
            "stream_url": stream_url, 
            "source_url": source_url,
            "message_id": message_id,
            "posted_at": datetime.utcnow()
        }}, 
        upsert=True
    )
    await stats_col.update_one({"stat_name": "total_posted"}, {"$inc": {"count": 1}}, upsert=True)

async def create_short_link(stream_url: str, referer: str, origin: str, source_url: str):
    short_id = hashlib.md5((stream_url + str(time.time())).encode()).hexdigest()[:12]
    await links_col.update_one(
        {"short_id": short_id}, 
        {"$set": {
            "short_id": short_id, 
            "stream_url": stream_url,
            "referer": referer,
            "origin": origin,
            "source_url": source_url,
            "created_at": datetime.utcnow()
        }}, 
        upsert=True
    )
    return short_id

async def get_stream_data(short_id: str):
    return await links_col.find_one({"short_id": short_id})

async def track_click():
    await stats_col.update_one({"stat_name": "total_clicks"}, {"$inc": {"count": 1}}, upsert=True)

async def get_stats():
    posted = await stats_col.find_one({"stat_name": "total_posted"})
    clicks = await stats_col.find_one({"stat_name": "total_clicks"})
    return (posted["count"] if posted else 0), (clicks["count"] if clicks else 0)

# =========================================================
# REAL-TIME SYNC & EXPIRE SYSTEM (No Channel Deletion)
# =========================================================
async def remove_expired_streams(source_url: str, active_stream_urls: list, context: ContextTypes.DEFAULT_TYPE):
    """M3U ফাইল থেকে রিমুভ হওয়া লিংকগুলো শুধু ডাটাবেস থেকে মুছে ফেলবে"""
    if not active_stream_urls:
        return 0  # Safety: If fetch failed or list is empty, avoid mass deletion

    db_streams = posted_col.find({"source_url": source_url})
    expired_urls = []
    
    async for doc in db_streams:
        if doc["stream_url"] not in active_stream_urls:
            expired_urls.append(doc["stream_url"])
            # চ্যানেল থেকে ডিলিট করার কোড রিমুভ করা হয়েছে। পোস্ট চ্যানেলেই থাকবে।

    if expired_urls:
        # Remove only from Databases
        await posted_col.delete_many({"stream_url": {"$in": expired_urls}})
        await links_col.delete_many({"stream_url": {"$in": expired_urls}})
        logger.info(f"Sync: Removed {len(expired_urls)} expired streams from DB for {source_url}")
        
    return len(expired_urls)

# =========================================================
# SMART M3U PARSER
# =========================================================
async def fetch_m3u_content(url: str):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=30) as response:
                if response.status == 200:
                    return await response.text()
    except Exception as e:
        logger.error(f"Fetch Error ({url}): {e}")
    return None

def parse_m3u_playlist(content: str):
    streams = []
    lines = content.splitlines()
    current_stream = {"title": "অজানা স্ট্রিম", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": ""}

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            current_stream = {"title": "অজানা স্ট্রিম", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": ""}
            if "," in line: current_stream["title"] = line.split(",")[-1].strip()
            group_match = re.search(r'group-title="([^"]+)"', line)
            logo_match = re.search(r'tvg-logo="([^"]+)"', line)
            if group_match: current_stream["group"] = group_match.group(1)
            if logo_match: current_stream["logo"] = logo_match.group(1)

        elif line.startswith("#EXTVLCOPT:http-referrer="):
            current_stream["referer"] = line.split("=")[1].strip()
        elif line.startswith("#EXTVLCOPT:http-origin="):
            current_stream["origin"] = line.split("=")[1].strip()

        elif line.startswith("http") and (".m3u8" in line or ".ts" in line):
            raw_url = line
            if "|" in raw_url:
                parts = raw_url.split("|")
                raw_url, headers_part = parts[0], parts[1]
                ref_match = re.search(r'Referer=([^&]+)', headers_part)
                orig_match = re.search(r'Origin=([^&]+)', headers_part)
                if ref_match: current_stream["referer"] = ref_match.group(1)
                if orig_match: current_stream["origin"] = orig_match.group(1)

            current_stream["url"] = raw_url
            streams.append(current_stream)
            current_stream = {"title": "অজানা স্ট্রিম", "group": "লাইভ টিভি", "logo": "", "referer": "", "origin": ""}

    return streams

# =========================================================
# FORCE JOIN SYSTEM
# =========================================================
async def is_user_joined(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    for channel in FORCE_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]: return False
        except Exception:
            return False
    return True

def get_force_join_keyboard(payload=None):
    buttons = [[InlineKeyboardButton(f"✅ চ্যানেল এ যুক্ত হোন ({ch})", url=f"https://t.me/{ch.replace('@', '')}")] for ch in FORCE_CHANNELS]
    buttons.append([InlineKeyboardButton("🔄 জয়েন করেছি (চেক করুন)", callback_data=f"check_join|{payload}" if payload else "check_join")])
    return InlineKeyboardMarkup(buttons)

# =========================================================
# JOBS: AUTO POSTING & SYNC
# =========================================================
async def post_to_channel(context, title, category, logo, stream_url, referer, origin, source_url):
    short_id = await create_short_link(stream_url, referer, origin, source_url)
    deep_link = f"https://t.me/{BOT_USERNAME}?start={short_id}"

    text = (
        f"📡 <b>{title}</b>\n\n"
        f"📂 <b>ক্যাটাগরি:</b> {category}\n"
        f"🔥 <b>নতুন লাইভ স্ট্রিম আপডেট হয়েছে</b>\n\n"
        f"📝 এইচডি কোয়ালিটিতে সরাসরি খেলা উপভোগ করুন।\n\n"
        f"🔗 <a href='{deep_link}'>সরাসরি দেখতে এখানে ক্লিক করুন</a>\n\n"
        f"⚡ <i>All In One Reborn | Auto Updated Feed</i>"
    )
    try:
        if logo and logo.startswith("http"):
            msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, parse_mode="HTML")
        else:
            msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
        await asyncio.sleep(2)
        return msg.message_id
    except Exception as e:
        logger.error(f"Channel Post Error: {e}")
        return None

async def auto_checker_job(context: ContextTypes.DEFAULT_TYPE):
    sources = await get_m3u_sources()
    for source in sources:
        content = await fetch_m3u_content(source)
        if not content: continue

        streams = parse_m3u_playlist(content)
        active_urls = []

        # 1. Post New Streams
        for item in streams:
            stream_url = item.get("url")
            if not stream_url: continue
            
            active_urls.append(stream_url)

            if await is_stream_posted(stream_url): 
                continue

            msg_id = await post_to_channel(context, item["title"], item["group"], item["logo"], stream_url, item["referer"], item["origin"], source)
            if msg_id:
                await save_posted_stream(stream_url, item["title"], source, msg_id)

        # 2. Sync & Expire Removed Streams
        if len(active_urls) > 0:
            await remove_expired_streams(source, active_urls, context)

async def delete_link_message(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    try:
        await context.bot.delete_message(chat_id=job_data["chat_id"], message_id=job_data["message_id"])
        warning_text = "⚠️ <b>মেয়াদ উত্তীর্ণ:</b>\nসিকিউরিটির জন্য আপনার লিংকের মেয়াদ শেষ হয়ে গেছে। ভিডিও পুনরায় দেখতে চাইলে চ্যানেল থেকে আবার লিংকে ক্লিক করুন।"
        await context.bot.send_message(chat_id=job_data["chat_id"], text=warning_text, parse_mode="HTML")
    except Exception:
        pass

# =========================================================
# BOT HANDLERS & ADMIN MENU
# =========================================================
admin_state = {}
admin_keyboard = ReplyKeyboardMarkup([
    ["➕ লিংক যুক্ত করুন", "➖ লিংক মুছুন"],
    ["📃 সব লিংক", "👥 মোট ইউজার"],
    ["📢 ব্রডকাস্ট", "📊 অ্যানালিটিক্স"],
    ["🚫 ইউজার ব্যান", "⚙️ সিস্টেম স্ট্যাটাস"],
    ["🔄 ফোর্স চেক"]
], resize_keyboard=True)

def get_sys_status():
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    if HAS_PSUTIL:
        ram = psutil.virtual_memory().percent
        cpu = psutil.cpu_percent()
        return f"⏱ <b>Uptime:</b> {uptime}\n💽 <b>RAM Usage:</b> {ram}%\n⚙️ <b>CPU Usage:</b> {cpu}%"
    return f"⏱ <b>Uptime:</b> {uptime}\n⚠️ <i>Install 'psutil' for RAM/CPU stats.</i>"

async def send_stream_message(context, chat_id, data, message_to_edit=None):
    msg_text = f"✅ <b>স্ট্রিম অ্যাক্সেস অনুমোদিত!</b>\n\n🔗 <b>আপনার প্লেব্যাক লিংক:</b>\n<code>{data['stream_url']}</code>\n"
    if data.get("referer"): msg_text += f"\n🌐 <b>Referer:</b>\n<code>{data['referer']}</code>"
    if data.get("origin"): msg_text += f"\n🌍 <b>Origin:</b>\n<code>{data['origin']}</code>"
        
    msg_text += "\n\n<i>(যেকোনো কাস্টম প্লেয়ার বা NS Player অ্যাপে এটি প্লে করতে পারবেন)। This Bot is Developed by Ratul.</i>\n\n⏳ <b>বিঃদ্রঃ</b> ৫ মিনিট পর মেসেজটি ডিলিট হয়ে যাবে।"

    if message_to_edit:
        msg = await message_to_edit.edit_text(msg_text, parse_mode="HTML")
    else:
        msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")

    context.job_queue.run_once(delete_link_message, when=DELETE_TIME, data={"chat_id": chat_id, "message_id": msg.message_id})

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if await is_user_banned(user_id):
        return await update.message.reply_text("🚫 আপনি এই বট ব্যবহারের জন্য নিষিদ্ধ (Banned)!")

    await add_user(user_id)
    payload = context.args[0] if context.args else None

    if not await is_user_joined(user_id, context):
        await update.message.reply_text(
            "❌ <b>অ্যাক্সেস ডিনাইড!</b>\nলিংক পেতে বা বট ব্যবহার করতে হলে আপনাকে স্পন্সর চ্যানেলে যুক্ত হতে হবে।",
            reply_markup=get_force_join_keyboard(payload), parse_mode="HTML"
        )
        return

    if payload:
        stream_data = await get_stream_data(payload)
        if stream_data:
            await track_click() 
            await send_stream_message(context, chat_id, stream_data)
        else:
            # লিংক ডাটাবেসে না থাকলে নতুন মেসেজ
            await update.message.reply_text(
                "❌ <b>এই লিংকটির মেয়াদ শেষ (Expired)!</b>\n\nদয়া করে চ্যানেল থেকে নতুন আপডেট হওয়া লিংকে ক্লিক করে সংগ্রহ করুন।", 
                parse_mode="HTML"
            )
        return

    if user_id == ADMIN_ID:
        await update.message.reply_text("👑 <b>Enterprise প্যানেলে স্বাগতম!</b>", reply_markup=admin_keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text("✅ <b>All In One Reborn বটে স্বাগতম!</b>\nআমাদের চ্যানেলে দেওয়া লিংকে ক্লিক করে লাইভ ভিডিও উপভোগ করুন।", parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id, chat_id = query.from_user.id, query.message.chat_id
    data = query.data
    await query.answer()

    if data.startswith("check_join"):
        if await is_user_joined(user_id, context):
            payload = data.split("|")[1] if "|" in data else None
            if payload:
                stream_data = await get_stream_data(payload)
                if stream_data:
                    await track_click()
                    await send_stream_message(context, chat_id, stream_data, message_to_edit=query.message)
                else:
                    # লিংক ডাটাবেসে না থাকলে নতুন মেসেজ
                    await query.message.edit_text(
                        "❌ <b>এই লিংকটির মেয়াদ শেষ (Expired)!</b>\n\nদয়া করে চ্যানেল থেকে নতুন আপডেট হওয়া লিংকে ক্লিক করে সংগ্রহ করুন।", 
                        parse_mode="HTML"
                    )
            else:
                await query.message.edit_text("✅ ভেরিফিকেশন সম্পন্ন! চ্যানেল থেকে স্ট্রিম দেখুন।")
        else:
            await query.message.reply_text("❌ আপনি এখনও সব চ্যানেলে যুক্ত হননি!")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if await is_user_banned(user_id): return
    await add_user(user_id)

    if user_id != ADMIN_ID: return

    state = admin_state.get(user_id)

    if state == "add_link":
        if text.startswith("http"):
            await add_m3u_source(text)
            await update.message.reply_text("✅ নতুন M3U সোর্স সফলভাবে সেভ হয়েছে।")
        else:
            await update.message.reply_text("❌ ভুল ইউআরএল। সঠিক HTTP লিংক দিন।")
        admin_state.pop(user_id, None)
        return

    elif state == "delete_link":
        deleted_streams, deleted_links = await remove_m3u_source(text)
        await update.message.reply_text(f"✅ <b>সোর্স মুছে ফেলা হয়েছে!</b>\n\n🗑 <b>Cleanup Report:</b>\n• মুছে ফেলা পোস্টেড স্ট্রিম: {deleted_streams}\n• মুছে ফেলা শর্ট লিংক: {deleted_links}", parse_mode="HTML")
        admin_state.pop(user_id, None)
        return

    elif state == "ban_user":
        try:
            target_id = int(text)
            await toggle_ban_user(target_id, True)
            await update.message.reply_text(f"✅ ইউজার {target_id} কে ব্যান করা হয়েছে।")
        except ValueError:
            await update.message.reply_text("❌ ভুল ইউজার আইডি।")
        admin_state.pop(user_id, None)
        return

    elif state == "broadcast":
        users = await get_all_users()
        sent, failed = 0, 0
        msg = await update.message.reply_text(f"🚀 ব্রডকাস্ট শুরু হচ্ছে... ({len(users)} ইউজার)")
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
                await asyncio.sleep(0.05)
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except Exception:
                failed += 1
        await msg.edit_text(f"✅ ব্রডকাস্ট সম্পন্ন!\nসফল: {sent}\nব্যর্থ: {failed}")
        admin_state.pop(user_id, None)
        return

    if text == "➕ লিংক যুক্ত করুন":
        admin_state[user_id] = "add_link"
        await update.message.reply_text("🔗 অনুগ্রহ করে নতুন M3U/M3U8 ইউআরএল দিন:")

    elif text == "➖ লিংক মুছুন":
        admin_state[user_id] = "delete_link"
        await update.message.reply_text("🗑 মুছতে চাইলে হুবহু লিংকটি দিন (এর সকল ডেটা মুছে যাবে):")

    elif text == "📃 সব লিংক":
        sources = await get_m3u_sources()
        source_text = "\n\n".join([f"🔹 {s}" for s in sources]) if sources else "❌ ডাটাবেসে কোনো লিংক নেই।"
        await update.message.reply_text(f"📂 <b>আপনার সমস্ত সোর্স:</b>\n\n{source_text}", parse_mode="HTML")

    elif text == "👥 মোট ইউজার":
        users = await get_all_users()
        await update.message.reply_text(f"👥 <b>অ্যাক্টিভ ইউজার:</b> {len(users)} জন", parse_mode="HTML")

    elif text == "📊 অ্যানালিটিক্স":
        total_posted, total_clicks = await get_stats()
        await update.message.reply_text(f"📊 <b>Enterprise Analytics</b>\n\n📺 <b>পোস্ট করা স্ট্রিম:</b> <code>{total_posted}</code>\n🖱 <b>মোট ক্লিক:</b> <code>{total_clicks}</code>", parse_mode="HTML")

    elif text == "📢 ব্রডকাস্ট":
        admin_state[user_id] = "broadcast"
        await update.message.reply_text("📝 ব্রডকাস্ট মেসেজটি লিখুন:")

    elif text == "⚙️ সিস্টেম স্ট্যাটাস":
        await update.message.reply_text(f"🖥 <b>সার্ভার স্ট্যাটাস:</b>\n\n{get_sys_status()}", parse_mode="HTML")

    elif text == "🚫 ইউজার ব্যান":
        admin_state[user_id] = "ban_user"
        await update.message.reply_text("🚫 যাকে ব্যান করতে চান তার User ID দিন:")

    elif text == "🔄 ফোর্স চেক":
        await update.message.reply_text("🔍 ব্যাকগ্রাউন্ডে স্ক্যানিং এবং ডেড লিংক ক্লিনিং শুরু হয়েছে...")
        asyncio.create_task(auto_checker_job(context))

# =========================================================
# MAIN EXECUTION
# =========================================================
def main():
    if not MONGO_URI:
        logger.error("MONGO_URI Error! Database connection failed.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Auto Jobs
    app.job_queue.run_repeating(auto_checker_job, interval=CHECK_TIME, first=10)

    logger.info("Enterprise Bot is RUNNING with Real-time Sync & DB-only Expire...")
    app.run_polling()

if __name__ == "__main__":
    main()
