import os
import re
import hashlib
import logging
import asyncio
import aiohttp
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

# ফোর্চ চ্যানেল লিস্ট (কমা দিয়ে আলাদা করা)
FORCE_CHANNELS = [
    x.strip() for x in os.getenv("FORCE_CHANNELS", "").split(",") if x.strip()
]
CHECK_TIME = int(os.getenv("CHECK_TIME", "300"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "*/*"
}

# =========================================================
# MONGODB SETUP (Cloud Enterprise Database)
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
    await users_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id}}, upsert=True)

async def get_all_users():
    return [doc["user_id"] async for doc in users_col.find({})]

async def add_m3u_source(url: str):
    await sources_col.update_one({"url": url}, {"$set": {"url": url}}, upsert=True)

async def remove_m3u_source(url: str):
    await sources_col.delete_one({"url": url})

async def get_m3u_sources():
    return [doc["url"] async for doc in sources_col.find({})]

async def is_stream_posted(stream_url: str):
    result = await posted_col.find_one({"stream_url": stream_url})
    return result is not None

async def save_posted_stream(stream_url: str, title: str):
    await posted_col.update_one(
        {"stream_url": stream_url}, 
        {"$set": {"title": title, "stream_url": stream_url}}, 
        upsert=True
    )
    # স্ট্যাটস আপডেট
    await stats_col.update_one({"stat_name": "total_posted"}, {"$inc": {"count": 1}}, upsert=True)

async def create_short_link(stream_url: str):
    short_id = hashlib.md5(stream_url.encode()).hexdigest()[:12]
    await links_col.update_one(
        {"short_id": short_id}, 
        {"$set": {"short_id": short_id, "stream_url": stream_url}}, 
        upsert=True
    )
    return short_id

async def get_long_link(short_id: str):
    doc = await links_col.find_one({"short_id": short_id})
    return doc["stream_url"] if doc else None

async def track_click():
    await stats_col.update_one({"stat_name": "total_clicks"}, {"$inc": {"count": 1}}, upsert=True)

async def get_stats():
    posted = await stats_col.find_one({"stat_name": "total_posted"})
    clicks = await stats_col.find_one({"stat_name": "total_clicks"})
    return (posted["count"] if posted else 0), (clicks["count"] if clicks else 0)

# =========================================================
# ADMIN MEMORY & KEYBOARD
# =========================================================
admin_state = {}

admin_keyboard = ReplyKeyboardMarkup(
    [
        ["➕ লিংক যুক্ত করুন", "➖ লিংক মুছুন"],
        ["📃 সব লিংক", "👥 মোট ইউজার"],
        ["📢 ব্রডকাস্ট", "📊 অ্যানালিটিক্স"],
        ["🔄 ফোর্স চেক"]
    ],
    resize_keyboard=True
)

# =========================================================
# ASYNC NETWORK & M3U PARSER
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
    current_stream = {}

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            current_stream = {"title": "অজানা স্ট্রিম", "group": "লাইভ টিভি", "logo": ""}
            
            if "," in line:
                current_stream["title"] = line.split(",")[-1].strip()
            
            group_match = re.search(r'group-title="([^"]+)"', line)
            logo_match = re.search(r'tvg-logo="([^"]+)"', line)
            
            if group_match: current_stream["group"] = group_match.group(1)
            if logo_match: current_stream["logo"] = logo_match.group(1)

        elif line.startswith("http") and (".m3u8" in line or ".ts" in line):
            current_stream["url"] = line
            streams.append(current_stream)
            current_stream = {}

    return streams

# =========================================================
# FORCE JOIN SYSTEM
# =========================================================
async def is_user_joined(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    for channel in FORCE_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            return False
    return True

def get_force_join_keyboard(payload=None):
    buttons = []
    for ch in FORCE_CHANNELS:
        buttons.append([InlineKeyboardButton(f"✅ চ্যানেল এ যুক্ত হোন ({ch})", url=f"https://t.me/{ch.replace('@', '')}")])
    
    cb_data = f"check_join|{payload}" if payload else "check_join"
    buttons.append([InlineKeyboardButton("🔄 জয়েন করেছি (চেক করুন)", callback_data=cb_data)])
    return InlineKeyboardMarkup(buttons)

# =========================================================
# AUTO CHANNEL POSTING JOB
# =========================================================
async def post_to_channel(context: ContextTypes.DEFAULT_TYPE, title, category, logo, stream_url):
    short_id = await create_short_link(stream_url)
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
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
        await asyncio.sleep(1.5) # Anti-Flood
    except Exception as e:
        logger.error(f"Channel Post Error: {e}")

async def auto_checker_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Auto Check Job Started...")
    sources = await get_m3u_sources()

    for source in sources:
        content = await fetch_m3u_content(source)
        if not content: continue

        streams = parse_m3u_playlist(content)
        for item in streams:
            stream_url = item.get("url")
            if not stream_url: continue

            if await is_stream_posted(stream_url):
                continue

            await post_to_channel(
                context, 
                item["title"], 
                item["group"], 
                item["logo"], 
                stream_url
            )
            await save_posted_stream(stream_url, item["title"])
            logger.info(f"Posted New Stream: {item['title']}")

# =========================================================
# BOT HANDLERS
# =========================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await add_user(user_id)

    payload = context.args[0] if context.args else None

    # Force Join Check
    if not await is_user_joined(user_id, context):
        await update.message.reply_text(
            "❌ <b>অ্যাক্সেস ডিনাইড!</b>\n\nলিংক পেতে বা বট ব্যবহার করতে হলে আপনাকে প্রথমে আমাদের স্পন্সর চ্যানেলগুলোতে যুক্ত হতে হবে।",
            reply_markup=get_force_join_keyboard(payload),
            parse_mode="HTML"
        )
        return

    # Deep Link Stream Access
    if payload:
        stream_link = await get_long_link(payload)
        if stream_link:
            await track_click() # Track view stats
            await update.message.reply_text(
                f"✅ <b>স্ট্রিম অ্যাক্সেস অনুমোদিত!</b>\n\n🔗 <b>আপনার M3U8 লিংক:</b>\n\n<code>{stream_link}</code>\n\n<i>(লিংকটি কপি করে All In One Reborn বা অন্য যেকোনো প্লেয়ারে প্লে করুন)</i>",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ দুঃখিত, এই লিংকটির মেয়াদ শেষ বা ডাটাবেসে পাওয়া যায়নি।")
        return

    # Normal Start
    if user_id == ADMIN_ID:
        await update.message.reply_text("👑 <b>অ্যাডমিন প্যানেলে স্বাগতম!</b>\nসিস্টেম অন এবং রেডি।", reply_markup=admin_keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text("✅ <b>All In One Reborn বটে স্বাগতম!</b>\nআমাদের চ্যানেলে দেওয়া লিংকে ক্লিক করে লাইভ ভিডিও উপভোগ করুন।", parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    await query.answer()

    if data.startswith("check_join"):
        payload = data.split("|")[1] if "|" in data else None
        
        if await is_user_joined(user_id, context):
            if payload:
                stream_link = await get_long_link(payload)
                if stream_link:
                    await track_click()
                    await query.message.edit_text(
                        f"✅ <b>ভেরিফিকেশন সম্পন্ন!</b>\n\n🔗 <b>আপনার M3U8 লিংক:</b>\n\n<code>{stream_link}</code>",
                        parse_mode="HTML"
                    )
                else:
                    await query.message.edit_text("❌ লিংকটি খুঁজে পাওয়া যায়নি।")
            else:
                await query.message.edit_text("✅ ভেরিফিকেশন সম্পন্ন! এখন আপনি চ্যানেল থেকে স্ট্রিম দেখতে পারবেন।")
        else:
            await query.message.reply_text("❌ আপনি এখনও সব চ্যানেলে যুক্ত হননি! দয়া করে যুক্ত হয়ে আবার চেষ্টা করুন।")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    await add_user(user_id)

    if user_id != ADMIN_ID:
        return

    state = admin_state.get(user_id)

    # State Actions
    if state == "add_link":
        if text.startswith("http"):
            await add_m3u_source(text)
            await update.message.reply_text("✅ নতুন M3U সোর্স ডাটাবেসে সেভ হয়েছে।")
        else:
            await update.message.reply_text("❌ ভুল ইউআরএল। সঠিক HTTP লিংক দিন।")
        admin_state.pop(user_id, None)
        return

    elif state == "delete_link":
        await remove_m3u_source(text)
        await update.message.reply_text("✅ লিংকটি ডাটাবেস থেকে মুছে ফেলা হয়েছে।")
        admin_state.pop(user_id, None)
        return

    elif state == "broadcast":
        users = await get_all_users()
        sent = 0
        msg = await update.message.reply_text(f"🚀 ব্রডকাস্ট শুরু হচ্ছে... ({len(users)} ইউজার)")
        for uid in users:
            try:
                await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
                await asyncio.sleep(0.05) # Safe Telegram API limit
            except Exception:
                pass
        await msg.edit_text(f"✅ ব্রডকাস্ট সম্পন্ন! মোট {sent} জনকে মেসেজ পাঠানো হয়েছে।")
        admin_state.pop(user_id, None)
        return

    # Menus
    if text == "➕ লিংক যুক্ত করুন":
        admin_state[user_id] = "add_link"
        await update.message.reply_text("🔗 অনুগ্রহ করে নতুন M3U বা M3U8 প্লেলিস্টের ইউআরএল দিন:")

    elif text == "➖ লিংক মুছুন":
        admin_state[user_id] = "delete_link"
        await update.message.reply_text("🗑 ডাটাবেস থেকে মুছতে হুবহু লিংকটি দিন:")

    elif text == "📃 সব লিংক":
        sources = await get_m3u_sources()
        if sources:
            source_text = "\n\n".join([f"🔹 {s}" for s in sources])
            await update.message.reply_text(f"📂 <b>আপনার সমস্ত সোর্স:</b>\n\n{source_text}", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ ডাটাবেসে কোনো লিংক নেই।")

    elif text == "👥 মোট ইউজার":
        users = await get_all_users()
        await update.message.reply_text(f"👥 <b>বটের মোট রেজিস্টার্ড ইউজার:</b> {len(users)} জন", parse_mode="HTML")

    elif text == "📊 অ্যানালিটিক্স":
        total_posted, total_clicks = await get_stats()
        await update.message.reply_text(
            f"📊 <b>Xtream Analytics</b>\n\n"
            f"📺 <b>মোট পোস্ট করা স্ট্রিম:</b> <code>{total_posted}</code>\n"
            f"🖱 <b>ইউজারদের মোট লিংক ক্লিক:</b> <code>{total_clicks}</code>",
            parse_mode="HTML"
        )

    elif text == "📢 ব্রডকাস্ট":
        admin_state[user_id] = "broadcast"
        await update.message.reply_text("📝 ব্যবহারকারীদের যে মেসেজটি পাঠাতে চান তা লিখুন:")

    elif text == "🔄 ফোর্স চেক":
        await update.message.reply_text("🔍 ব্যাকগ্রাউন্ডে লিংক স্ক্যানিং শুরু হয়েছে...")
        asyncio.create_task(auto_checker_job(context))

# =========================================================
# MAIN EXECUTION
# =========================================================
def main():
    if not MONGO_URI:
        logger.error("MONGO_URI ভেরিয়েবল সেট করা হয়নি! ডাটাবেস কানেক্ট করতে ব্যর্থ।")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Background Job
    app.job_queue.run_repeating(auto_checker_job, interval=CHECK_TIME, first=10)

    logger.info("All In One Reborn - Enterprise Bot is RUNNING with MongoDB...")
    app.run_polling()

if __name__ == "__main__":
    main()
