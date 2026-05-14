import os
import re
import time
import json
import hashlib
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta
import pytz
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
DELETE_TIME = 300 

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "*/*"
}

START_TIME = time.time()
bd_tz = pytz.timezone("Asia/Dhaka")

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
    await users_col.update_one({"user_id": user_id}, {"$setOnInsert": {"user_id": user_id, "joined_at": datetime.utcnow(), "is_banned": False}}, upsert=True)

async def get_all_users():
    return [doc["user_id"] async for doc in users_col.find({"is_banned": {"$ne": True}})]

async def is_user_banned(user_id: int):
    user = await users_col.find_one({"user_id": user_id})
    return user.get("is_banned", False) if user else False

async def toggle_ban_user(user_id: int, ban_status: bool):
    result = await users_col.update_one({"user_id": user_id}, {"$set": {"is_banned": ban_status}})
    return result.modified_count > 0

async def add_m3u_source(url: str, target: str):
    await sources_col.update_one({"url": url}, {"$set": {"url": url, "target": target, "added_at": datetime.utcnow()}}, upsert=True)

async def remove_m3u_source(url: str):
    await sources_col.delete_one({"url": url})
    deleted_streams = await posted_col.delete_many({"source_url": url})
    deleted_links = await links_col.delete_many({"source_url": url})
    return deleted_streams.deleted_count, deleted_links.deleted_count

async def get_m3u_sources():
    return [{"url": doc["url"], "target": doc.get("target", "both")} async for doc in sources_col.find({})]

async def save_posted_stream(stream_url: str, title: str, source_url: str, message_id: int, short_id: str, target: str):
    await posted_col.update_one(
        {"title": title, "source_url": source_url}, 
        {"$set": {
            "title": title, "stream_url": stream_url, "source_url": source_url,
            "message_id": message_id, "short_id": short_id, "target": target, "posted_at": datetime.utcnow()
        }}, 
        upsert=True
    )
    await stats_col.update_one({"stat_name": "total_posted"}, {"$inc": {"count": 1}}, upsert=True)

async def create_short_link(stream_url: str, referer: str, origin: str, cookie: str, user_agent: str, source_url: str):
    short_id = hashlib.md5((stream_url + str(time.time())).encode()).hexdigest()[:12]
    await links_col.update_one(
        {"short_id": short_id}, 
        {"$set": {
            "short_id": short_id, "stream_url": stream_url, "referer": referer,
            "origin": origin, "cookie": cookie, "user_agent": user_agent,
            "source_url": source_url, "created_at": datetime.utcnow()
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

async def remove_expired_streams(source_url: str, active_stream_urls: list):
    if not active_stream_urls: return 0 
    db_streams = posted_col.find({"source_url": source_url})
    expired_urls = [doc["stream_url"] async for doc in db_streams if doc["stream_url"] not in active_stream_urls]
    if expired_urls:
        await posted_col.delete_many({"stream_url": {"$in": expired_urls}})
        await links_col.delete_many({"stream_url": {"$in": expired_urls}})
    return len(expired_urls)

# =========================================================
# BULLETPROOF BLOCK PARSER
# =========================================================
async def fetch_m3u_content(url: str):
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=30) as response:
                if response.status == 200: return await response.text()
    except Exception as e: logger.error(f"Fetch Error: {e}")
    return None

def parse_m3u_playlist(content: str):
    streams = []
    clean = re.sub(r'#TOTAL-VS-MATCHES:[^\n#]*', '', content)
    clean = re.sub(r'#LAST-UPDATED:[^\n#]*', '', clean)
    blocks = clean.split("#EXTINF")
    
    for idx, block in enumerate(blocks):
        if not block.strip() or block.startswith("#EXTM3U"): continue
            
        stream = {
            "title": f"Live Stream {idx}", "group": "লাইভ টিভি", "logo": "",
            "referer": "", "origin": "", "cookie": "", "user_agent": "", "url": ""
        }
        
        extinf_line = block.strip().splitlines()[0] if block.strip() else ""
        
        g_match = re.search(r'group-title="([^"]+)"', extinf_line, re.IGNORECASE)
        l_match = re.search(r'tvg-logo="([^"]+)"', extinf_line, re.IGNORECASE)
        if g_match: stream["group"] = g_match.group(1).strip()
        if l_match: stream["logo"] = l_match.group(1).strip()
        
        raw_title = ""
        if '"' in extinf_line:
            parts = re.split(r'"\s*,', extinf_line)
            if len(parts) > 1: raw_title = parts[-1].strip()
            else: raw_title = extinf_line.split(",")[-1].strip()
        else: raw_title = extinf_line.split(",", 1)[-1].strip()
            
        if raw_title: stream["title"] = raw_title

        if ref_m := re.search(r'#EXTVLCOPT:http-referrer=([^#\n]+)', block, re.IGNORECASE): stream["referer"] = ref_m.group(1).strip()
        if orig_m := re.search(r'#EXTVLCOPT:http-origin=([^#\n]+)', block, re.IGNORECASE): stream["origin"] = orig_m.group(1).strip()
        if cookie_m := re.search(r'#EXTVLCOPT:http-cookie=([^#\n]+)', block, re.IGNORECASE): stream["cookie"] = cookie_m.group(1).strip()
        if ua_m := re.search(r'#EXTVLCOPT:http-user-agent=([^#\n]+)', block, re.IGNORECASE): stream["user_agent"] = ua_m.group(1).strip()

        json_m = re.search(r'#EXTHTTP:(\{.*?\})', block, re.IGNORECASE)
        if json_m:
            try:
                j_data = {k.lower(): v for k, v in json.loads(json_m.group(1)).items()}
                if "cookie" in j_data: stream["cookie"] = str(j_data["cookie"]).strip()
                if "referer" in j_data: stream["referer"] = str(j_data["referer"]).strip()
                if "origin" in j_data: stream["origin"] = str(j_data["origin"]).strip()
                if "user-agent" in j_data: stream["user_agent"] = str(j_data["user-agent"]).strip()
            except Exception: pass

        block_no_logo = block.replace(stream["logo"], "") if stream["logo"] else block
        if json_m: block_no_logo = block_no_logo.replace(json_m.group(0), "")
        
        urls = re.findall(r'(https?://[^\s#]+)', block_no_logo)
        if urls:
            playback_url = urls[-1].strip()
            if "|" in playback_url:
                parts = playback_url.split("|", 1)
                playback_url, h_part = parts[0].strip(), parts[1]
                if p_ref := re.search(r'Referer=([^&]+)', h_part, re.IGNORECASE): stream["referer"] = p_ref.group(1).strip()
                if p_orig := re.search(r'Origin=([^&]+)', h_part, re.IGNORECASE): stream["origin"] = p_orig.group(1).strip()
                if p_cookie := re.search(r'Cookie=([^&]+)', h_part, re.IGNORECASE): stream["cookie"] = p_cookie.group(1).strip()
                if p_ua := re.search(r'User-Agent=([^&]+)', h_part, re.IGNORECASE): stream["user_agent"] = p_ua.group(1).strip()
                
            stream["url"] = playback_url
            streams.append(stream)
            
    return streams

# =========================================================
# FORCE JOIN SYSTEM
# =========================================================
async def is_user_joined(user_id: int, context: ContextTypes.DEFAULT_TYPE):
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

# =========================================================
# TARGET BASED POSTING SYSTEM
# =========================================================
async def post_to_tg_channel(context, title, category, logo, short_id):
    now_time = datetime.now(bd_tz).strftime("%I:%M %p (%d %b)")
    deep_link = f"https://t.me/{BOT_USERNAME}?start={short_id}"

    text = (
        f"📡 <b>{title}</b>\n\n📂 <b>ক্যাটাগরি:</b> {category}\n🟢 <b>[LIVE] লাইভ স্ট্রিমটি সচল আছে</b>\n\n"
        f"📝 এইচডি কোয়ালিটিতে সরাসরি খেলা উপভোগ করুন।\n\n🔗 <a href='{deep_link}'>সরাসরি দেখতে এখানে ক্লিক করুন</a>\n\n"
        f"🔄 <b>সর্বশেষ আপডেট:</b> <code>{now_time}</code>\n⚡ <i>All In One Reborn | Auto Updated Feed</i>"
    )
    
    msg_id = None
    try:
        await asyncio.sleep(3) 
        if logo and logo.startswith("http"):
            try:
                msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, parse_mode="HTML")
                msg_id = msg.message_id
            except Exception:
                msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
                msg_id = msg.message_id
        else:
            msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, parse_mode="HTML", disable_web_page_preview=True)
            msg_id = msg.message_id
            
        return msg_id

    except RetryAfter as flood_err:
        await asyncio.sleep(flood_err.retry_after)
        return await post_to_tg_channel(context, title, category, logo, short_id)
    except Exception as e: 
        logger.error(f"TG Post Error: {e}")
        return None

async def process_all_sources(context: ContextTypes.DEFAULT_TYPE, status_msg=None, force_repost=False):
    sources = await get_m3u_sources()
    stats = {"sources": len(sources), "total_streams": 0, "new_posts": 0, "updated_posts": 0, "removed": 0, "failed": 0, "errors": []}
    
    if force_repost:
        await posted_col.delete_many({})
    
    for idx, src_data in enumerate(sources, 1):
        source = src_data["url"]
        target = src_data["target"]
        
        if status_msg:
            try: await status_msg.edit_text(f"🔄 <b>সোর্স স্ক্যানিং চলছে... ({idx}/{len(sources)})</b>\n🔗 <code>{source}</code>\n🎯 <b>Target:</b> {target.upper()}", parse_mode="HTML")
            except Exception: pass

        content = await fetch_m3u_content(source)
        if not content:
            stats["errors"].append(f"লিংক ফেচ ব্যর্থ: {source}")
            stats["failed"] += 1
            continue

        streams = parse_m3u_playlist(content)
        stats["total_streams"] += len(streams)
        active_urls = []

        for item in streams:
            stream_url, title = item.get("url"), item.get("title")
            if not stream_url or not title: continue
            
            active_urls.append(stream_url)
            existing_post = await posted_col.find_one({"title": title, "source_url": source})

            if existing_post and not force_repost:
                msg_id = existing_post.get("message_id")
                short_id = existing_post.get("short_id")
                
                await posted_col.update_one({"_id": existing_post["_id"]}, {"$set": {"stream_url": stream_url, "target": target, "posted_at": datetime.utcnow()}})
                await links_col.update_one(
                    {"short_id": short_id},
                    {"$set": {
                        "stream_url": stream_url, "referer": item["referer"], "origin": item["origin"],
                        "cookie": item["cookie"], "user_agent": item["user_agent"], "updated_at": datetime.utcnow()
                    }}
                )

                if msg_id and target in ["tg", "both"]:
                    now_time = datetime.now(bd_tz).strftime("%I:%M %p (%d %b)")
                    deep_link = f"https://t.me/{BOT_USERNAME}?start={short_id}"
                    updated_text = (
                        f"📡 <b>{title}</b>\n\n📂 <b>ক্যাটাগরি:</b> {item['group']}\n🟢 <b>[LIVE] লাইভ স্ট্রিমটি সচল আছে</b>\n\n"
                        f"📝 এইচডি কোয়ালিটিতে সরাসরি খেলা উপভোগ করুন।\n\n🔗 <a href='{deep_link}'>সরাসরি দেখতে এখানে ক্লিক করুন</a>\n\n"
                        f"🔄 <b>সর্বশেষ আপডেট:</b> <code>{now_time}</code>\n⚡ <i>All In One Reborn | Auto Updated Feed</i>"
                    )
                    try:
                        if item["logo"] and item["logo"].startswith("http"):
                            await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=msg_id, caption=updated_text, parse_mode="HTML")
                        else:
                            await context.bot.edit_message_text(chat_id=CHANNEL_ID, message_id=msg_id, text=updated_text, parse_mode="HTML", disable_web_page_preview=True)
                    except Exception: pass
                stats["updated_posts"] += 1
                continue  

            short_id = await create_short_link(stream_url, item["referer"], item["origin"], item["cookie"], item["user_agent"], source)
            msg_id = None
            
            if target in ["tg", "both"]:
                msg_id = await post_to_tg_channel(context, item["title"], item["group"], item["logo"], short_id)
                
            if msg_id or target == "web":
                await save_posted_stream(stream_url, item["title"], source, msg_id, short_id, target)
                stats["new_posts"] += 1
            else: 
                stats["failed"] += 1

        if len(active_urls) > 0: stats["removed"] += await remove_expired_streams(source, active_urls)
    return stats

async def auto_checker_job(context: ContextTypes.DEFAULT_TYPE): await process_all_sources(context)

async def delete_link_message(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    try:
        await context.bot.delete_message(chat_id=job_data["chat_id"], message_id=job_data["message_id"])
        await context.bot.send_message(chat_id=job_data["chat_id"], text="⚠️ <b>মেয়াদ উত্তীর্ণ:</b>\nলিংকের মেয়াদ শেষ। চ্যানেল থেকে আবার লিংকে ক্লিক করুন。", parse_mode="HTML")
    except Exception: pass

# =========================================================
# BOT HANDLERS & ADMIN MENU
# =========================================================
admin_state = {}

admin_keyboard = ReplyKeyboardMarkup([
    ["➕ লিংক যুক্ত করুন", "➖ লিংক মুছুন"], 
    ["📊 সোর্স লাইভ স্ট্যাটাস", "👥 মোট ইউজার"], 
    ["🔁 সব নতুন করে পোস্ট করুন", "🔄 ফোর্স চেক"], 
    ["📢 ব্রডকাস্ট", "📊 অ্যানালিটিক্স"], 
    ["🚫 ইউজার ব্যান", "⚙️ সিস্টেম স্ট্যাটাস"]
], resize_keyboard=True)

TARGET_MAP = {"Telegram Only": "tg", "Web Only": "web", "Both": "both"}

def get_sys_status():
    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    if HAS_PSUTIL: return f"⏱ <b>Uptime:</b> {uptime}\n💽 <b>RAM:</b> {psutil.virtual_memory().percent}%\n⚙️ <b>CPU:</b> {psutil.cpu_percent()}%"
    return f"⏱ <b>Uptime:</b> {uptime}\n⚠️ <i>Install 'psutil'</i>"

async def send_stream_message(context, chat_id, data, message_to_edit=None):
    msg_text = f"✅ <b>স্ট্রিম অ্যাক্সেস অনুমোদিত!</b>\n\n🔗 <b>আপনার প্লেব্যাক লিংক:</b>\n<code>{data['stream_url']}</code>\n"
    if data.get("referer"): msg_text += f"\n🌐 <b>Referer:</b>\n<code>{data['referer']}</code>"
    if data.get("origin"): msg_text += f"\n🌍 <b>Origin:</b>\n<code>{data['origin']}</code>"
    if data.get("cookie"): msg_text += f"\n🍪 <b>Cookie:</b>\n<code>{data['cookie']}</code>"
    if data.get("user_agent"): msg_text += f"\n🛡️ <b>User-Agent:</b>\n<code>{data['user_agent']}</code>"
    msg_text += "\n\n<i>(যেকোনো কাস্টম প্লেয়ার বা NS Player-এ চলবে)। This Bot is Developed by Ratul.</i>\n\n⏳ ৫ মিনিট পর মেসেজটি ডিলিট হয়ে যাবে।"

    if message_to_edit: await message_to_edit.edit_text(msg_text, parse_mode="HTML")
    else: msg = await context.bot.send_message(chat_id=chat_id, text=msg_text, parse_mode="HTML")
    context.job_queue.run_once(delete_link_message, when=DELETE_TIME, data={"chat_id": chat_id, "message_id": msg.message_id})

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, chat_id = update.effective_user.id, update.effective_chat.id
    if await is_user_banned(user_id): return await update.message.reply_text("🚫 আপনি নিষিদ্ধ (Banned)!")
    await add_user(user_id)
    payload = context.args[0] if context.args else None

    if not await is_user_joined(user_id, context):
        return await update.message.reply_text("❌ <b>অ্যাক্সেস ডিনাইড!</b>\nস্পন্সর চ্যানেলে যুক্ত হতে হবে।", reply_markup=get_force_join_keyboard(payload), parse_mode="HTML")

    if payload:
        stream_data = await get_stream_data(payload)
        if stream_data: await track_click(); await send_stream_message(context, chat_id, stream_data)
        else: await update.message.reply_text("❌ <b>লিংকটির মেয়াদ শেষ!</b> চ্যানেল থেকে নতুন লিংক সংগ্রহ করুন。", parse_mode="HTML")
        return

    if user_id == ADMIN_ID: await update.message.reply_text("👑 <b>Enterprise Extra Pro প্যানেল রেডি!</b>", reply_markup=admin_keyboard, parse_mode="HTML")
    else: await update.message.reply_text("✅ <b>স্বাগতম!</b> চ্যানেল থেকে লিংকে ক্লিক করে লাইভ দেখুন।", parse_mode="HTML")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; user_id, chat_id = query.from_user.id, query.message.chat_id
    await query.answer()
    if query.data.startswith("check_join"):
        if await is_user_joined(user_id, context):
            payload = query.data.split("|")[1] if "|" in query.data else None
            if payload:
                stream_data = await get_stream_data(payload)
                if stream_data: await track_click(); await send_stream_message(context, chat_id, stream_data, message_to_edit=query.message)
                else: await query.message.edit_text("❌ <b>লিংকটির মেয়াদ শেষ!</b> চ্যানেল থেকে নতুন লিংক সংগ্রহ করুন。", parse_mode="HTML")
            else: await query.message.edit_text("✅ ভেরিফিকেশন সম্পন্ন! স্ট্রিম দেখুন।")
        else: await query.message.reply_text("❌ আপনি এখনও সব চ্যানেলে যুক্ত হননি!")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, text = update.effective_user.id, update.message.text.strip()
    if await is_user_banned(user_id): return
    await add_user(user_id)
    if user_id != ADMIN_ID: return
    state = admin_state.get(user_id)

    if state == "select_target":
        if text == "❌ বাতিল করুন":
            admin_state.pop(user_id, None)
            await update.message.reply_text("❌ লিংক যুক্ত করা বাতিল হয়েছে।", reply_markup=admin_keyboard)
            return
            
        if text in TARGET_MAP:
            admin_state[user_id] = f"add_link_{TARGET_MAP[text]}"
            await update.message.reply_text("🔗 এবার M3U লিংকটি দিন:", reply_markup=admin_keyboard)
        else:
            await update.message.reply_text("❌ সঠিক অপশন নির্বাচন করুন।")
        return

    elif state and state.startswith("add_link_"):
        target_val = state.split("_")[2]
        if text.startswith("http"): 
            await add_m3u_source(text, target_val)
            await update.message.reply_text(f"✅ সোর্স সেভ হয়েছে। (Target: {target_val.upper()})")
        else: 
            await update.message.reply_text("❌ ভুল ইউআরএল।")
        admin_state.pop(user_id, None); return
        
    elif state == "delete_link":
        s, l = await remove_m3u_source(text); await update.message.reply_text(f"✅ সোর্স ও তার {s}+{l} ডেটা মুছেছে।")
        admin_state.pop(user_id, None); return
    elif state == "ban_user":
        try: await toggle_ban_user(int(text), True); await update.message.reply_text("✅ ইউজার ব্যানড।")
        except ValueError: await update.message.reply_text("❌ ভুল আইডি।")
        admin_state.pop(user_id, None); return
    elif state == "broadcast":
        users, sent = await get_all_users(), 0
        msg = await update.message.reply_text("🚀 ব্রডকাস্ট হচ্ছে...")
        for uid in users:
            try: await context.bot.send_message(chat_id=uid, text=text); sent += 1; await asyncio.sleep(0.05)
            except Exception: pass
        await msg.edit_text(f"✅ ব্রডকাস্ট সফল: {sent} জন।"); admin_state.pop(user_id, None); return

    if text == "➕ লিংক যুক্ত করুন": 
        admin_state[user_id] = "select_target"
        target_kb = ReplyKeyboardMarkup([["Telegram Only", "Web Only", "Both"], ["❌ বাতিল করুন"]], resize_keyboard=True)
        await update.message.reply_text("কোথায় পোস্ট করতে চান নির্বাচন করুন:", reply_markup=target_kb)
        
    elif text == "➖ লিংক মুছুন": admin_state[user_id] = "delete_link"; await update.message.reply_text("🗑 লিংক দিন:")
    
    elif text == "📊 সোর্স লাইভ স্ট্যাটাস":
        sources = await get_m3u_sources()
        if not sources:
            return await update.message.reply_text("❌ ডাটাবেসে কোনো লিংক নেই।")
            
        status_text = "📊 <b>লাইভ সোর্স ট্র্যাকিং (Enterprise Extra Pro)</b>\n\n"
        for idx, src_data in enumerate(sources, 1):
            src = src_data["url"]
            tgt = src_data["target"].upper()
            count = await posted_col.count_documents({"source_url": src})
            status_text += f"🔹 <b>সোর্স {idx} ({tgt}):</b>\n🔗 <code>{src}</code>\n🟢 <b>লাইভ পোস্ট আছে:</b> <code>{count}</code> টি\n\n"
            
        await update.message.reply_text(status_text, parse_mode="HTML", disable_web_page_preview=True)

    elif text == "👥 মোট ইউজার": await update.message.reply_text(f"👥 ইউজার: {len(await get_all_users())} জন")
    elif text == "📊 অ্যানালিটিক্স":
        p, c = await get_stats(); await update.message.reply_text(f"📊 <b>পোস্ট:</b> {p}\n🖱 <b>ক্লিক:</b> {c}", parse_mode="HTML")
    elif text == "📢 ব্রডকাস্ট": admin_state[user_id] = "broadcast"; await update.message.reply_text("📝 মেসেজ লিখুন:")
    elif text == "⚙️ সিস্টেম স্ট্যাটাস": await update.message.reply_text(get_sys_status(), parse_mode="HTML")
    elif text == "🚫 ইউজার ব্যান": admin_state[user_id] = "ban_user"; await update.message.reply_text("🚫 User ID দিন:")
    
    elif text == "🔁 সব নতুন করে পোস্ট করুন":
        status_msg = await update.message.reply_text("⚠️ <b>ডাটাবেস রিসেট করে সব নতুন করে পোস্ট করা হচ্ছে...</b>\nএটি সোর্স অনুযায়ী একটু সময় নিতে পারে।", parse_mode="HTML")
        stats = await process_all_sources(context, status_msg=status_msg, force_repost=True)
        await status_msg.edit_text(
            f"✅ <b>ফোর্স রিপোস্ট সম্পন্ন!</b>\n\n📊 <b>রিপোর্ট:</b>\n"
            f"🔗 সোর্স: <code>{stats['sources']}</code>\n📺 মোট স্ট্রিম: <code>{stats['total_streams']}</code>\n"
            f"🆕 সফল পোস্ট/সেভ: <code>{stats['new_posts']}</code>\n"
            f"❌ ব্যর্থ: <code>{stats['failed']}</code>", parse_mode="HTML"
        )

    elif text == "🔄 ফোর্স চেক":
        status_msg = await update.message.reply_text("🔍 <b>স্ক্যানিং শুরু হচ্ছে...</b>", parse_mode="HTML")
        stats = await process_all_sources(context, status_msg=status_msg, force_repost=False)
        await status_msg.edit_text(
            f"✅ <b>স্ক্যান সম্পন্ন!</b>\n\n📊 <b>রিপোর্ট:</b>\n"
            f"🔗 সোর্স: <code>{stats['sources']}</code>\n📺 স্ট্রিম: <code>{stats['total_streams']}</code>\n"
            f"🆕 নতুন পোস্ট/সেভ: <code>{stats['new_posts']}</code>\n🔄 সাইলেন্ট আপডেট: <code>{stats['updated_posts']}</code>\n"
            f"❌ ব্যর্থ: <code>{stats['failed']}</code>", parse_mode="HTML"
        )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.job_queue.run_repeating(auto_checker_job, interval=CHECK_TIME, first=10)
    logger.info("Enterprise Extra Pro Bot RUNNING (With TG/Web Filter)...")
    app.run_polling()

if __name__ == "__main__": main()
