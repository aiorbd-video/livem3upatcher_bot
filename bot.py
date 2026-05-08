
import os
import re
import hashlib
import logging
import asyncio
import aiohttp
import aiosqlite
import urllib.parse

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
# লগিং সেটআপ (প্রো লেভেল ট্র্যাকিং)
# =========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================================
# ভেরিয়েবলস এবং কনফিগারেশন
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@YourChannel")
BOT_USERNAME = os.getenv("BOT_USERNAME", "YourBotUsername")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

# ফোর্চ চ্যানেল লিস্ট (কমা দিয়ে আলাদা করা)
FORCE_CHANNELS = [
    x.strip() for x in os.getenv("FORCE_CHANNELS", "").split(",") if x.strip()
]
CHECK_TIME = int(os.getenv("CHECK_TIME", "300"))
DB_FILE = "pro_bot_database.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "*/*"
}

# =========================================================
# ডাটাবেস ফাংশন (SQLite Async)
# =========================================================
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS m3u_sources (url TEXT PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS posted_streams (stream_url TEXT PRIMARY KEY, title TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS short_links (short_id TEXT PRIMARY KEY, stream_url TEXT)")
        await db.commit()

async def add_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            return [row[0] async for row in cursor]

async def add_m3u_source(url: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO m3u_sources (url) VALUES (?)", (url,))
        await db.commit()

async def remove_m3u_source(url: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM m3u_sources WHERE url = ?", (url,))
        await db.commit()

async def get_m3u_sources():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT url FROM m3u_sources") as cursor:
            return [row[0] async for row in cursor]

async def is_stream_posted(stream_url: str):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT 1 FROM posted_streams WHERE stream_url = ?", (stream_url,)) as cursor:
            return await cursor.fetchone() is not None

async def save_posted_stream(stream_url: str, title: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO posted_streams (stream_url, title) VALUES (?, ?)", (stream_url, title))
        await db.commit()

async def create_short_link(stream_url: str):
    # ১২ ক্যারেক্টারের ইউনিক হ্যাশ তৈরি করা হলো যা টেলিগ্রামের start প্যারামিটারে ১০০% সেফ
    short_id = hashlib.md5(stream_url.encode()).hexdigest()[:12]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO short_links (short_id, stream_url) VALUES (?, ?)", (short_id, stream_url))
        await db.commit()
    return short_id

async def get_long_link(short_id: str):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT stream_url FROM short_links WHERE short_id = ?", (short_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

# =========================================================
# অ্যাডমিন কীবোর্ড এবং মেমরি
# =========================================================
admin_state = {}

admin_keyboard = ReplyKeyboardMarkup(
    [
        ["➕ লিংক যুক্ত করুন", "➖ লিংক মুছুন"],
        ["📃 সব লিংক", "👥 মোট ইউজার"],
        ["📢 ব্রডকাস্ট", "🔄 ফোর্স চেক"]
    ],
    resize_keyboard=True
)

# =========================================================
# নেটওয়ার্ক এবং পার্সিং
# =========================================================
async def fetch_m3u_content(url: str):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=30) as response:
                if response.status == 200:
                    return await response.text()
    except Exception as e:
        logger.error(f"Fetch Error: {e}")
    return None

def parse_m3u_playlist(content: str):
    streams = []
    lines = content.splitlines()
    current_stream = {}

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            current_stream = {"title": "অজানা স্ট্রিম", "group": "লাইভ টিভি", "logo": ""}
            
            # টাইটেল বের করা
            if "," in line:
                current_stream["title"] = line.split(",")[-1].strip()
            
            # গ্রুপ এবং লোগো বের করা
            group_match = re.search(r'group-title="([^"]+)"', line)
            logo_match = re.search(r'tvg-logo="([^"]+)"', line)
            
            if group_match: current_stream["group"] = group_match.group(1)
            if logo_match: current_stream["logo"] = logo_match.group(1)

        elif line.startswith("http") and (".m3u8" in line or ".ts" in line):
            current_stream["url"] = line
            streams.append(current_stream)
            current_stream = {} # রিসেট

    return streams

# =========================================================
# ফোর্স জয়েন সিস্টেম
# =========================================================
async def is_user_joined(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    for channel in FORCE_CHANNELS:
        try:
            member = await context.bot.get_chat_member(channel, user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            # বট অ্যাডমিন না থাকলে এরর আসতে পারে
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
# চ্যানেল পোস্টিং জব
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
        f"⚡ <i>অটো আপডেটেড ফিড</i>"
    )

    try:
        if logo and logo.startswith("http"):
            await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, parse_mode="HTML")
        else:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
        await asyncio.sleep(1) # টেলিগ্রামের রেট লিমিট এড়াতে
    except Exception as e:
        logger.error(f"Channel Post Error: {e}")

async def auto_checker_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Auto Check Started...")
    sources = await get_m3u_sources()

    for source in sources:
        content = await fetch_m3u_content(source)
        if not content: continue

        streams = parse_m3u_playlist(content)
        for item in streams:
            stream_url = item.get("url")
            if not stream_url: continue

            # স্ট্রিমটি কি আগে পোস্ট করা হয়েছে?
            if await is_stream_posted(stream_url):
                continue

            # নতুন স্ট্রিম হলে পোস্ট করুন এবং সেভ করুন
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
# বটের হ্যান্ডলারসমূহ
# =========================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await add_user(user_id)

    payload = context.args[0] if context.args else None

    # ফোর্স জয়েন চেক
    if not await is_user_joined(user_id, context):
        await update.message.reply_text(
            "❌ <b>অ্যাক্সেস ডিনাইড!</b>\n\nলিংক পেতে বা বট ব্যবহার করতে হলে আপনাকে প্রথমে আমাদের স্পন্সর চ্যানেলগুলোতে যুক্ত হতে হবে।",
            reply_markup=get_force_join_keyboard(payload),
            parse_mode="HTML"
        )
        return

    # ডিপ লিংক (স্ট্রিম অ্যাক্সেস)
    if payload:
        stream_link = await get_long_link(payload)
        if stream_link:
            await update.message.reply_text(
                f"✅ <b>স্ট্রিম অ্যাক্সেস অনুমোদিত!</b>\n\n🔗 <b>আপনার M3U8 লিংক:</b>\n\n<code>{stream_link}</code>\n\n<i>(লিংকটি কপি করে যেকোনো আইপিটিভি বা নেটওয়ার্ক প্লেয়ারে প্লে করুন)</i>",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text("❌ দুঃখিত, এই লিংকটির মেয়াদ শেষ বা এটি ডাটাবেসে নেই।")
        return

    # সাধারণ স্টার্ট
    if user_id == ADMIN_ID:
        await update.message.reply_text("👑 <b>অ্যাডমিন প্যানেলে স্বাগতম!</b>", reply_markup=admin_keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text("✅ <b>বট অ্যাক্সেস সাকসেসফুল!</b> আমাদের চ্যানেলে দেওয়া লিংকে ক্লিক করে ভিডিও দেখুন।", parse_mode="HTML")

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
                    await query.message.edit_text(
                        f"✅ <b>ভেরিফিকেশন সম্পন্ন!</b>\n\n🔗 <b>আপনার M3U8 লিংক:</b>\n\n<code>{stream_link}</code>",
                        parse_mode="HTML"
                    )
                else:
                    await query.message.edit_text("❌ লিংকটি খুঁজে পাওয়া যায়নি।")
            else:
                await query.message.edit_text("✅ ভেরিফিকেশন সম্পন্ন! এখন আপনি বট ব্যবহার করতে পারেন।")
        else:
            await query.message.reply_text("❌ আপনি এখনও সব চ্যানেলে যুক্ত হননি! দয়া করে যুক্ত হয়ে আবার চেষ্টা করুন।")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    await add_user(user_id)

    if user_id != ADMIN_ID:
        return

    # স্টেট ম্যানেজমেন্ট
    state = admin_state.get(user_id)

    if state == "add_link":
        if text.startswith("http"):
            await add_m3u_source(text)
            await update.message.reply_text("✅ নতুন M3U সোর্স সফলভাবে ডাটাবেসে সেভ হয়েছে।")
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
                await asyncio.sleep(0.05) # অ্যান্টি স্প্যাম ডিলিট
            except Exception:
                pass
        await msg.edit_text(f"✅ ব্রডকাস্ট সম্পন্ন! মোট {sent} জনকে মেসেজ পাঠানো হয়েছে।")
        admin_state.pop(user_id, None)
        return

    # মেনু একশন
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

    elif text == "📢 ব্রডকাস্ট":
        admin_state[user_id] = "broadcast"
        await update.message.reply_text("📝 ব্যবহারকারীদের যে মেসেজটি পাঠাতে চান তা লিখুন:")

    elif text == "🔄 ফোর্স চেক":
        await update.message.reply_text("🔍 ব্যাকগ্রাউন্ডে লিংক স্ক্যানিং শুরু হয়েছে...")
        # ফোর্স রান জব
        asyncio.create_task(auto_checker_job(context))

# =========================================================
# মেইন ফাংশন
# =========================================================
def main():
    # ডাটাবেস ইনিশিয়ালাইজ করা
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    # বট অ্যাপ বিল্ডার
    app = Application.builder().token(BOT_TOKEN).build()

    # কমান্ড ও হ্যান্ডলার যুক্ত করা
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # JobQueue দিয়ে অটো চেকার সেট করা
    app.job_queue.run_repeating(auto_checker_job, interval=CHECK_TIME, first=10)

    logger.info("প্রো এন্টারপ্রাইজ বট সফলভাবে চালু হয়েছে...")
    app.run_polling()

if __name__ == "__main__":
    main()
