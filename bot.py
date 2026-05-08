import os
import re
import urllib.parse
import logging
import asyncio
import aiohttp
import aiosqlite

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
# LOGGING SETUP (Enterprise Standard)
# =========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================================
# VARIABLES
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_id")
BOT_USERNAME = os.getenv("BOT_USERNAME", "your_bot_username")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
FORCE_CHANNELS = os.getenv("FORCE_CHANNELS", "").split(",")
CHECK_TIME = 300
DB_FILE = "enterprise_bot.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

# =========================================================
# DATABASE FUNCTIONS (SQLite)
# =========================================================
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS links (url TEXT PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS posted (url TEXT PRIMARY KEY, title TEXT, clicks INTEGER DEFAULT 0)")
        await db.commit()

async def save_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        await db.commit()

async def get_all_users():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def save_link(link: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO links (url) VALUES (?)", (link,))
        await db.commit()

async def get_all_links():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT url FROM links") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def delete_link(link: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM links WHERE url = ?", (link,))
        await db.commit()

async def is_posted(url: str):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT 1 FROM posted WHERE url = ?", (url,)) as cursor:
            return await cursor.fetchone() is not None

async def save_posted(url: str, title: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR IGNORE INTO posted (url, title, clicks) VALUES (?, ?, 0)", (url, title))
        await db.commit()

async def track_click(url: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE posted SET clicks = clicks + 1 WHERE url = ?", (url,))
        await db.commit()

async def get_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT SUM(clicks), COUNT(url) FROM posted") as cursor:
            row = await cursor.fetchone()
            total_clicks = row[0] if row[0] else 0
            total_streams = row[1] if row[1] else 0
            return total_streams, total_clicks

# =========================================================
# MEMORY & MENUS
# =========================================================
waiting_states = {}

admin_keyboard = ReplyKeyboardMarkup(
    [
        ["➕ Add Link", "➖ Delete Link"],
        ["📃 All Links", "👥 Total Users"],
        ["📢 Broadcast", "📊 Stats"],
        ["🔄 Force Check"]
    ],
    resize_keyboard=True
)

# =========================================================
# ASYNC NETWORK FETCH
# =========================================================
async def fetch_url(url: str):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=30, allow_redirects=True) as response:
                if response.status == 200:
                    return await response.text()
    except Exception as e:
        logger.error(f"Fetch Error ({url}): {e}")
    return None

# =========================================================
# PARSE M3U (Optimized)
# =========================================================
def parse_m3u(content: str):
    channels = []
    lines = content.splitlines()
    current = {}

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF"):
            current = {}
            logo_match = re.search(r'tvg-logo="([^"]+)"', line)
            group_match = re.search(r'group-title="([^"]+)"', line)
            
            if logo_match: current["logo"] = logo_match.group(1)
            if group_match: current["group"] = group_match.group(1)
            if "," in line: current["title"] = line.split(",")[-1].strip()

        elif ".m3u8" in line or line.startswith("http"):
            current["url"] = line.strip()
            if "title" in current:
                channels.append(current)
    return channels

# =========================================================
# FORCE JOIN CHECK
# =========================================================
async def check_force_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    not_joined = []

    for channel in FORCE_CHANNELS:
        if not channel.strip(): continue
        try:
            member = await context.bot.get_chat_member(channel.strip(), user_id)
            if member.status in ["left", "kicked"]:
                not_joined.append(channel.strip())
        except Exception:
            not_joined.append(channel.strip())

    if not_joined:
        buttons = [[InlineKeyboardButton(f"Join {ch}", url=f"https://t.me/{ch.replace('@', '')}")] for ch in not_joined]
        buttons.append([InlineKeyboardButton("✅ Checked, I Joined", callback_data="check_join")])
        await update.message.reply_text("❌ আপনাকে আগে আমাদের চ্যানেলগুলোতে যুক্ত হতে হবে:", reply_markup=InlineKeyboardMarkup(buttons))
        return False
    return True

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_join":
        # Check again
        user_id = query.from_user.id
        all_joined = True
        for channel in FORCE_CHANNELS:
            if not channel.strip(): continue
            try:
                member = await context.bot.get_chat_member(channel.strip(), user_id)
                if member.status in ["left", "kicked"]: all_joined = False
            except:
                all_joined = False
        
        if all_joined:
            await query.message.reply_text("✅ Access Granted. Send /start again.")
        else:
            await query.answer("Join all channels first!", show_alert=True)

# =========================================================
# AUTO M3U8 CHECKER JOB
# =========================================================
async def check_m3u_job(context: ContextTypes.DEFAULT_TYPE):
    links = await get_all_links()
    
    for m3u_url in links:
        logger.info(f"Checking Link: {m3u_url}")
        content = await fetch_url(m3u_url)
        if not content: continue

        streams = parse_m3u(content)
        for item in streams:
            stream_url = item.get("url")
            if not stream_url: continue

            if await is_posted(stream_url): continue

            title = item.get("title", "Unknown Stream")
            category = item.get("group", "Live TV")
            logo = item.get("logo", "")

            encoded_url = urllib.parse.quote(stream_url)
            deep_link = f"https://t.me/{BOT_USERNAME}?start={encoded_url}"

            # Smart AI-style clean caption
            text = (
                f"📡 **{title}**\n\n"
                f"📂 Category: {category}\n"
                f"🔥 High-Speed Live Stream Updated\n"
                f"⚡ Auto Verified | No Buffering\n\n"
                f"⚠️ Watch exclusively via our secure bot."
            )

            keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("▶ WATCH LIVE NOW", url=deep_link)]])

            try:
                if logo:
                    await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, reply_markup=keyboard, parse_mode="Markdown")
                else:
                    await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=keyboard, parse_mode="Markdown", disable_web_page_preview=True)
                
                await save_posted(stream_url, title)
                logger.info(f"Posted: {title}")
                await asyncio.sleep(1) # Prevent FloodWait
            except Exception as e:
                logger.error(f"Post Error ({title}): {e}")

# =========================================================
# COMMANDS & HANDLERS
# =========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_force_join(update, context): return

    user_id = update.effective_user.id
    await save_user(user_id)
    args = context.args

    # User clicked a stream deep-link
    if args:
        stream_link = urllib.parse.unquote(args[0])
        await track_click(stream_link) # Track the view in DB
        
        await update.message.reply_text(
            f"✅ **Stream Authorized**\n\n🔗 Your M3U8 Link:\n`{stream_link}`\n\n*Copy the link and play it in your favorite network player.*",
            parse_mode="Markdown"
        )
        return

    # Normal Start
    if user_id == ADMIN_ID:
        await update.message.reply_text("👑 **Admin Dashboard Connected**\nManage your IPTV network efficiently.", reply_markup=admin_keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text("✅ **Bot Access Granted**\nWatch live TV streams automatically from our channels.", parse_mode="Markdown")

async def messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user_id != ADMIN_ID: return

    state = waiting_states.get(user_id)

    # State Management Responses
    if state == "add_link":
        if text.startswith("http"):
            await save_link(text)
            await update.message.reply_text("✅ M3U Source Successfully Added.")
        else:
            await update.message.reply_text("❌ Invalid URL. Operation Cancelled.")
        waiting_states.pop(user_id, None)
        return

    elif state == "delete_link":
        await delete_link(text)
        await update.message.reply_text("✅ Link removed from database.")
        waiting_states.pop(user_id, None)
        return

    elif state == "broadcast":
        users = await get_all_users()
        sent = 0
        await update.message.reply_text(f"🚀 Broadcasting to {len(users)} users...")
        for user in users:
            try:
                await context.bot.send_message(chat_id=user, text=text)
                sent += 1
                await asyncio.sleep(0.05) # Safe limit
            except Exception: pass
        
        await update.message.reply_text(f"✅ Broadcast Completed. Delivered to {sent} users.")
        waiting_states.pop(user_id, None)
        return

    # Menu Commands
    if text == "➕ Add Link":
        waiting_states[user_id] = "add_link"
        await update.message.reply_text("🔗 Send the exact M3U/M3U8 URL to monitor:")

    elif text == "➖ Delete Link":
        waiting_states[user_id] = "delete_link"
        await update.message.reply_text("🗑 Send the exact M3U URL to delete:")

    elif text == "📃 All Links":
        links = await get_all_links()
        msg = "\n\n".join(links) if links else "No Active Links Found."
        await update.message.reply_text(f"📂 **Active M3U Sources:**\n\n{msg}", parse_mode="Markdown")

    elif text == "👥 Total Users":
        users = await get_all_users()
        await update.message.reply_text(f"👥 **Total Registered Users:** {len(users)}", parse_mode="Markdown")

    elif text == "📢 Broadcast":
        waiting_states[user_id] = "broadcast"
        await update.message.reply_text("📝 Send your broadcast message:")
        
    elif text == "📊 Stats":
        total_streams, total_clicks = await get_stats()
        await update.message.reply_text(
            f"📊 **Enterprise Analytics**\n\n"
            f"📺 Total Streams Posted: `{total_streams}`\n"
            f"🖱 Total Stream Clicks/Views: `{total_clicks}`",
            parse_mode="Markdown"
        )

    elif text == "🔄 Force Check":
        await update.message.reply_text("🔍 Forcing Background Check...")
        asyncio.create_task(check_m3u_job(context))
        await update.message.reply_text("✅ Check initiated in background.")

# =========================================================
# MAIN INITIALIZATION
# =========================================================
def main():
    # DB initialization loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())

    app = Application.builder().token(BOT_TOKEN).build()

    # Job Queue Checker
    job_queue = app.job_queue
    job_queue.run_repeating(check_m3u_job, interval=CHECK_TIME, first=10)

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages))

    logger.info("ENTERPRISE BOT IS RUNNING...")
    app.run_polling()

if __name__ == "__main__":
    main()
