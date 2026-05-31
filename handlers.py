import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.error import RetryAfter
import database as db
from config import BOT_USERNAME, CHANNEL_ID, ADMIN_ID, DELETE_TIME, bd_tz, FORCE_CHANNELS, HEADERS
import utils

# 🎯 নতুন: কাস্টম ম্যাচ ও M3U লিংক সেভ করার জন্য টেম্পোরারি স্টোরেজ
custom_match_data = {}
m3u_add_data = {} 

admin_keyboard = ReplyKeyboardMarkup([
    ["➕ লিংক যুক্ত করুন", "➖ লিংক মুছুন"],
    ["➕ কাস্টম ম্যাচ"],
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

def get_post_content(title, category, short_id):
    now_time = datetime.now(bd_tz).strftime("%I:%M %p (%d %b)")
    text = (
        f"📡 <b>{title}</b>\n\n"
        f"📂 <b>ক্যাটাগরি:</b> {category}\n"
        f"🟢 <b>[LIVE] লাইভ স্ট্রিমটি সচল আছে</b>\n\n"
        f"📝 এইচডি কোয়ালিটিতে সরাসরি খেলা উপভোগ করুন।\n\n"
        f"🔄 <b>সর্বশেষ আপডেট:</b> <code>{now_time}</code>\n"
        f"⚡ <i>All In One Reborn</i>"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 সরাসরি দেখুন (VipXtream Web)", url=f"https://vipxtream.vercel.app/")],
        [InlineKeyboardButton("📺 প্লেয়ারে দেখুন (Telegram)", url=f"https://t.me/{BOT_USERNAME}?start={short_id}")]
    ])
    return text, markup

async def post_to_tg_channel(context, title, category, logo, short_id):
    text, markup = get_post_content(title, category, short_id)
    try:
        await asyncio.sleep(2)
        if logo and logo.startswith("http"):
            try:
                msg = await context.bot.send_photo(chat_id=CHANNEL_ID, photo=logo, caption=text, reply_markup=markup, parse_mode="HTML")
                return msg.message_id
            except Exception: pass
            
        msg = await context.bot.send_message(chat_id=CHANNEL_ID, text=text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
        return msg.message_id
    except RetryAfter as flood_err:
        await asyncio.sleep(flood_err.retry_after)
        return await post_to_tg_channel(context, title, category, logo, short_id)
    except Exception: 
        return None

async def edit_tg_channel_post(context, title, category, short_id, message_id):
    text, markup = get_post_content(title, category, short_id)
    try:
        await asyncio.sleep(1)
        try:
            await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=message_id, caption=text, reply_markup=markup, parse_mode="HTML")
        except Exception:
            await context.bot.edit_message_text(chat_id=CHANNEL_ID, message_id=message_id, text=text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
    except RetryAfter as flood_err:
        await asyncio.sleep(flood_err.retry_after)
        await edit_tg_channel_post(context, title, category, short_id, message_id)
    except Exception:
        pass

async def send_stream_message(context, chat_id, data, message_to_edit=None):
    msg_text = f"✅ <b>স্ট্রিম অ্যাক্সেস অনুমোদিত!</b>\n\n🔗 <b>আপনার প্লেব্যাক লিংক:</b>\n<code>{data['stream_url']}</code>\n"
    if data.get("referer"): msg_text += f"\n🌐 <b>Referer:</b>\n<code>{data['referer']}</code>"
    if data.get("origin"): msg_text += f"\n🌍 <b>Origin:</b>\n<code>{data['origin']}</code>"
    if data.get("cookie"): msg_text += f"\n🍪 <b>Cookie:</b>\n<code>{data['cookie']}</code>"
    if data.get("user_agent"): msg_text += f"\n🛡️ <b>User-Agent:</b>\n<code>{data['user_agent']}</code>"
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
        await context.bot.send_message(chat_id=job_data["chat_id"], text="⚠️ <b>মেয়াদ উত্তীর্ণ:</b>\nলিংকের মেয়াদ শেষ। চ্যানেল থেকে পুনরায় ক্লিক করুন।", parse_mode="HTML")
    except Exception: pass

async def process_all_sources(context: ContextTypes.DEFAULT_TYPE, status_msg=None, force_repost=False):
    sources = await db.get_m3u_sources()
    stats = {"sources": len(sources), "total_streams": 0, "new_posts": 0, "updated_posts": 0, "failed": 0, "ended": 0}
    
    if force_repost: 
        await db.posted_col.delete_many({})

    for idx, src_data in enumerate(sources, 1):
        source = src_data.get("url") if isinstance(src_data, dict) else src_data
        target = src_data.get("target", "both") if isinstance(src_data, dict) else "both"
        proxy_url = src_data.get("proxy_url", "") if isinstance(src_data, dict) else "" # 🎯 প্রক্সি ডাটাবেস থেকে রিড করা হচ্ছে
        
        if status_msg:
            try: await status_msg.edit_text(f"🔄 <b>সোর্স স্ক্যানিং চলছে... ({idx}/{len(sources)})</b>\n🔗 <code>{source}</code>\n⏳ ডেটা ডাউনলোড হচ্ছে...", parse_mode="HTML", disable_web_page_preview=True)
            except Exception: pass

        content = await utils.fetch_m3u_content(source)
        if not content:
            stats["failed"] += 1
            continue
            
        if status_msg:
            try: await status_msg.edit_text(f"🔄 <b>সোর্স স্ক্যানিং চলছে... ({idx}/{len(sources)})</b>\n🔗 <code>{source}</code>\n✅ ডেটা ডাউনলোড সম্পন্ন! পার্সিং হচ্ছে...", parse_mode="HTML", disable_web_page_preview=True)
            except Exception: pass
        
        streams = await asyncio.to_thread(utils.parse_m3u_playlist, content)
        total_s = len(streams)
        stats["total_streams"] += total_s
        active_titles = []

        for s_idx, item in enumerate(streams, 1):
            if not item.get("url") or not item.get("title"): continue
            title = item["title"]
            active_titles.append(title)
            
            headers = {
                "referer": item.get("referer", ""),
                "origin": item.get("origin", ""),
                "cookie": item.get("cookie", ""),
                "user_agent": item.get("user_agent", "")
            }
            
            if status_msg and force_repost:
                if s_idx % 5 == 0 or s_idx == total_s:
                    try: await status_msg.edit_text(f"⚠️ <b>পোস্টিং চলছে...</b>\n📂 সোর্স: {idx}/{len(sources)}\n📺 প্রসেস: <b>{s_idx}/{total_s}</b>", parse_mode="HTML", disable_web_page_preview=True)
                    except RetryAfter as e: await asyncio.sleep(e.retry_after)
                    except Exception: pass

            existing_post = await db.posted_col.find_one({"title": title, "source_url": source})

            if existing_post and not force_repost:
                msg_id = existing_post.get("message_id")
                short_id_exist = existing_post.get("short_id")
                
                await db.posted_col.update_one({"_id": existing_post["_id"]}, {"$set": {"stream_url": item["url"], "posted_at": datetime.utcnow()}})
                if short_id_exist:
                    await db.links_col.update_one(
                        {"short_id": short_id_exist}, 
                        {"$set": {"stream_url": item["url"], "referer": headers["referer"], "origin": headers["origin"], "cookie": headers["cookie"], "user_agent": headers["user_agent"], "proxy_url": proxy_url, "updated_at": datetime.utcnow()}}
                    )

                if target in ["tg", "both"] and msg_id:
                    await edit_tg_channel_post(context, title, item.get("group", "লাইভ টিভি"), short_id_exist, msg_id)
                
                # 🎯 ডাটাবেসে সেভ করা হচ্ছে
                await db.save_posted_stream(item["url"], title, source, msg_id, short_id_exist, target, item.get("logo", ""), headers=headers, proxy_url=proxy_url)
                stats["updated_posts"] += 1
                continue

            short_id = await db.create_short_link(item["url"], headers["referer"], headers["origin"], headers["cookie"], headers["user_agent"], source, title=title, proxy_url=proxy_url)
            msg_id = None
            if target in ["tg", "both"]: 
                msg_id = await post_to_tg_channel(context, title, item.get("group", "লাইভ টিভি"), item.get("logo", ""), short_id)
            
            if msg_id or target == "web":
                await db.save_posted_stream(item["url"], title, source, msg_id, short_id, target, item.get("logo", ""), headers=headers, proxy_url=proxy_url)
                stats["new_posts"] += 1
            else: 
                stats["failed"] += 1
        
        if not force_repost:
            cursor = db.posted_col.find({"source_url": source})
            async for exp in cursor:
                if exp["title"] not in active_titles:
                    msg_id = exp.get("message_id")
                    if msg_id and target in ["tg", "both"]:
                        ended_text = f"🚫 <b>স্ট্রিম সমাপ্ত (Stream Ended)</b>\n\n📡 <b>{exp.get('title', 'Unknown')}</b>\n\n🔴 <i>এই লাইভ স্ট্রিমটি এখন আর সচল নেই। পরবর্তী খেলার জন্য চ্যানেলে চোখ রাখুন।</i>\n⚡ <i>All In One Reborn</i>"
                        try: await context.bot.edit_message_caption(chat_id=CHANNEL_ID, message_id=msg_id, caption=ended_text, parse_mode="HTML")
                        except Exception:
                            try: await context.bot.edit_message_text(chat_id=CHANNEL_ID, message_id=msg_id, text=ended_text, parse_mode="HTML", disable_web_page_preview=True)
                            except Exception: pass
                    await db.posted_col.delete_one({"_id": exp["_id"]})
                    if exp.get("short_id"): await db.links_col.delete_one({"short_id": exp["short_id"]})
                    stats["ended"] += 1

    return stats

async def auto_checker_job(context: ContextTypes.DEFAULT_TYPE): await process_all_sources(context)

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
            await db.track_click()
            await send_stream_message(context, update.effective_chat.id, stream_data)
        else: await update.message.reply_text("❌ লিংকের মেয়াদ শেষ বা স্ট্রিমটি আর সচল নেই!")
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
    if not update.effective_user or not update.message or not update.message.text: return
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    if user_id != ADMIN_ID: return
    state = utils.admin_state.get(user_id)

    if text == "❌ বাতিল করুন" or (state and text == "❌ বাতিল করুন"):
        utils.admin_state.pop(user_id, None)
        custom_match_data.pop(user_id, None)
        m3u_add_data.pop(user_id, None)
        return await update.message.reply_text("❌ প্রক্রিয়া বাতিল করা হয়েছে।", reply_markup=admin_keyboard)

    # ==========================================
    # 🌟 কাস্টম ম্যাচ প্রসেসিং (DASH/HLS/DRM/Time/Proxy)
    # ==========================================
    if text == "➕ কাস্টম ম্যাচ":
        utils.admin_state[user_id] = "cm_title"
        custom_match_data[user_id] = {}
        return await update.message.reply_text("📝 কাস্টম ম্যাচের টাইটেল দিন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_title":
        custom_match_data[user_id]["title"] = text
        utils.admin_state[user_id] = "cm_type"
        return await update.message.reply_text("⚙️ স্ট্রিম টাইপ নির্বাচন করুন:", reply_markup=ReplyKeyboardMarkup([["HLS (m3u8)", "DASH (mpd)"], ["❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_type":
        if text not in ["HLS (m3u8)", "DASH (mpd)"]: return await update.message.reply_text("সঠিক অপশন নির্বাচন করুন।")
        custom_match_data[user_id]["type"] = "dash" if "DASH" in text else "hls"
        utils.admin_state[user_id] = "cm_url"
        return await update.message.reply_text("🔗 ভিডিও URL (m3u8/mpd) দিন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_url":
        custom_match_data[user_id]["url"] = text
        if custom_match_data[user_id]["type"] == "dash":
            utils.admin_state[user_id] = "cm_drm"
            return await update.message.reply_text("🔐 Clearkey DRM দিন (Format: KeyID:Key)\nঅথবা না থাকলে N লিখুন:", reply_markup=ReplyKeyboardMarkup([["N", "❌ বাতিল করুন"]], resize_keyboard=True))
        else:
            utils.admin_state[user_id] = "cm_time"
            return await update.message.reply_text("⏱️ স্টার্ট ও এন্ড টাইম দিন (Format: 08:00 PM - 10:00 PM)\nঅথবা না থাকলে N লিখুন:", reply_markup=ReplyKeyboardMarkup([["N", "❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_drm":
        drm = "" if text.upper() == "N" else text
        if drm and ":" in drm:
            custom_match_data[user_id]["drm_key_id"] = drm.split(":")[0].strip()
            custom_match_data[user_id]["drm_key"] = drm.split(":", 1)[1].strip()
        utils.admin_state[user_id] = "cm_time"
        return await update.message.reply_text("⏱️ স্টার্ট ও এন্ড টাইম দিন (Format: 08:00 PM - 10:00 PM)\nঅথবা না থাকলে N লিখুন:", reply_markup=ReplyKeyboardMarkup([["N", "❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_time":
        time_str = "" if text.upper() == "N" else text
        if "-" in time_str:
            custom_match_data[user_id]["start_time"] = time_str.split("-")[0].strip()
            custom_match_data[user_id]["end_time"] = time_str.split("-")[1].strip()
        else:
            custom_match_data[user_id]["start_time"] = time_str
            custom_match_data[user_id]["end_time"] = ""
        utils.admin_state[user_id] = "cm_logo"
        return await update.message.reply_text("🖼️ লোগোর লিংক দিন (না থাকলে N লিখুন):", reply_markup=ReplyKeyboardMarkup([["N", "❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_logo":
        custom_match_data[user_id]["logo"] = "" if text.upper() == "N" else text
        utils.admin_state[user_id] = "cm_proxy"
        # 🎯 প্রক্সি চাওয়া হচ্ছে
        return await update.message.reply_text("🌐 কাস্টম প্রক্সি URL দিন (যেমন: https://ratul...workers.dev/?url=)\nপ্রক্সি না লাগলে N লিখুন:", reply_markup=ReplyKeyboardMarkup([["N", "❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_proxy":
        custom_match_data[user_id]["proxy_url"] = "" if text.upper() == "N" else text
        utils.admin_state[user_id] = "cm_target"
        return await update.message.reply_text("কোথায় পোস্ট করতে চান?", reply_markup=ReplyKeyboardMarkup([["Telegram Only", "Web Only", "Both"], ["❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "cm_target":
        if text not in TARGET_MAP: return await update.message.reply_text("সঠিক অপশন নির্বাচন করুন।")
        target = TARGET_MAP[text]
        data = custom_match_data[user_id]

        short_id = await db.create_short_link(
            stream_url=data["url"], referer="", origin="", cookie="", user_agent="", source_url="Custom Match",
            title=data["title"], logo=data.get("logo", ""), stream_type=data.get("type", "hls"),
            drm_key_id=data.get("drm_key_id", ""), drm_key=data.get("drm_key", ""),
            start_time=data.get("start_time", ""), end_time=data.get("end_time", ""),
            proxy_url=data.get("proxy_url", "")
        )

        msg_id = None
        if target in ["tg", "both"]:
            msg_id = await post_to_tg_channel(context, data["title"], "Custom Live Match", data.get("logo", ""), short_id)

        await db.save_posted_stream(
            stream_url=data["url"], title=data["title"], source_url="Custom Match", message_id=msg_id,
            short_id=short_id, target=target, logo=data.get("logo", ""), headers={},
            stream_type=data.get("type", "hls"), drm_key_id=data.get("drm_key_id", ""),
            drm_key=data.get("drm_key", ""), start_time=data.get("start_time", ""), end_time=data.get("end_time", ""),
            proxy_url=data.get("proxy_url", "")
        )

        utils.admin_state.pop(user_id, None)
        custom_match_data.pop(user_id, None)
        return await update.message.reply_text(f"✅ কাস্টম ম্যাচ সফলভাবে {text} এ পোস্ট করা হয়েছে!", reply_markup=admin_keyboard)

    # ==========================================
    # 🌟 প্লেলিস্ট / M3U লিংক যুক্ত করা (Proxy সহ)
    # ==========================================
    if text == "➕ লিংক যুক্ত করুন":
        utils.admin_state[user_id] = "m3u_target"
        m3u_add_data[user_id] = {}
        return await update.message.reply_text("কোথায় পোস্ট করতে চান নির্বাচন করুন:", reply_markup=ReplyKeyboardMarkup([["Telegram Only", "Web Only", "Both"], ["❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "m3u_target":
        if text in TARGET_MAP:
            m3u_add_data[user_id]["target"] = TARGET_MAP[text]
            utils.admin_state[user_id] = "m3u_url"
            return await update.message.reply_text("🔗 এবার M3U লিংকটি দিন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))
        return await update.message.reply_text("❌ সঠিক অপশন নির্বাচন করুন।")

    elif state == "m3u_url":
        m3u_add_data[user_id]["url"] = text
        utils.admin_state[user_id] = "m3u_proxy"
        # 🎯 প্রক্সি চাওয়া হচ্ছে
        return await update.message.reply_text("🌐 এই প্লেলিস্টের জন্য প্রক্সি URL দিন (যেমন: https://ratul...workers.dev/?url=)\nনা লাগলে N লিখুন:", reply_markup=ReplyKeyboardMarkup([["N", "❌ বাতিল করুন"]], resize_keyboard=True))

    elif state == "m3u_proxy":
        proxy = "" if text.upper() == "N" else text
        target = m3u_add_data[user_id]["target"]
        url = m3u_add_data[user_id]["url"]
        
        await db.add_m3u_source(url, target, proxy)
        await update.message.reply_text("✅ সোর্স সেভ হয়েছে।", reply_markup=admin_keyboard)
        
        utils.admin_state.pop(user_id, None)
        m3u_add_data.pop(user_id, None)
        return

    # ==========================================
    # আগের সব বেসিক কমান্ড
    # ==========================================
    if state == "delete_link":
        try: s, l = await db.remove_m3u_source(text)
        except ValueError: await db.remove_m3u_source(text); s, l = 0, 0
        await update.message.reply_text("✅ সোর্স ডেটা মুছেছে।", reply_markup=admin_keyboard)
        return utils.admin_state.pop(user_id, None)

    if state == "ban_user":
        try: await db.toggle_ban_user(int(text), True); await update.message.reply_text("✅ ইউজার ব্যানড।", reply_markup=admin_keyboard)
        except ValueError: await update.message.reply_text("❌ ভুল আইডি।")
        return utils.admin_state.pop(user_id, None)

    if state == "unban_user":
        try:
            ok = await db.toggle_ban_user(int(text), False)
            await update.message.reply_text("✅ ইউজার আনব্যানড।" if ok else "❌ পাওয়া যায়নি।", reply_markup=admin_keyboard)
        except ValueError: await update.message.reply_text("❌ ভুল আইডি।")
        return utils.admin_state.pop(user_id, None)

    if state == "broadcast":
        users = await db.get_all_users()
        sent = 0
        msg = await update.message.reply_text("🚀 ব্রডকাস্ট হচ্ছে...")
        for i in range(0, len(users), 30):
            chunk = users[i:i+30]
            tasks = [context.bot.send_message(chat_id=uid, text=text) for uid in chunk]
            res = await asyncio.gather(*tasks, return_exceptions=True)
            sent += sum(1 for x in res if not isinstance(x, Exception))
            await asyncio.sleep(1)
        await msg.edit_text(f"✅ ব্রডকাস্ট সফল: {sent} জন।")
        return utils.admin_state.pop(user_id, None)

    elif text == "➖ লিংক মুছুন":
        utils.admin_state[user_id] = "delete_link"
        await update.message.reply_text("🗑 লিংক দিন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))
        
    elif text == "📊 সোর্স লাইভ স্ট্যাটাস":
        sources = await db.get_m3u_sources()
        if not sources: return await update.message.reply_text("❌ ডাটাবেসে কোনো লিংক নেই।")
        status_text = "📊 <b>লাইভ সোর্স স্ট্যাটাস</b>\n\n"
        for idx, src_data in enumerate(sources, 1):
            src = src_data["url"] if isinstance(src_data, dict) else src_data
            count = await db.posted_col.count_documents({"source_url": src})
            status_text += f"🔹 <b>সোর্স {idx}:</b>\n🔗 <code>{src}</code>\n🟢 <b>লাইভ পোস্ট:</b> <code>{count}</code> টি\n\n"
        await update.message.reply_text(status_text, parse_mode="HTML", disable_web_page_preview=True)
        
    elif text == "👥 মোট ইউজার": await update.message.reply_text(f"👥 মোট ইউজার: {len(await db.get_all_users())} জন")
    
    elif text == "📊 অ্যানালিটিক্স":
        p, c = await db.get_stats(); users = len(await db.get_all_users())
        await update.message.reply_text(f"📊 <b>Enterprise Analytics</b>\n\n👥 Users: <code>{users}</code>\n📺 Posts: <code>{p}</code>\n🖱 Clicks: <code>{c}</code>", parse_mode="HTML")
        
    elif text == "📢 ব্রডকাস্ট": utils.admin_state[user_id] = "broadcast"; await update.message.reply_text("📝 মেসেজ লিখুন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))
    elif text == "🚫 ইউজার ব্যান": utils.admin_state[user_id] = "ban_user"; await update.message.reply_text("🚫 User ID দিন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))
    elif text == "✅ আনব্যান": utils.admin_state[user_id] = "unban_user"; await update.message.reply_text("✅ User ID দিন:", reply_markup=ReplyKeyboardMarkup([["❌ বাতিল করুন"]], resize_keyboard=True))
    elif text == "⚙️ সিস্টেম স্ট্যাটাস": await update.message.reply_text(utils.get_sys_status(), parse_mode="HTML")
        
    elif text == "🔄 ফোর্স চেক":
        status_msg = await update.message.reply_text("🔍 স্ক্যানিং শুরু হচ্ছে...")
        stats = await process_all_sources(context, status_msg=status_msg, force_repost=False)
        await status_msg.edit_text(f"✅ স্ক্যান সম্পন্ন!\n\n🆕 নতুন পোস্ট: {stats['new_posts']}\n🔄 আপডেট: {stats['updated_posts']}\n🚫 সমাপ্ত স্ট্রিম: {stats['ended']}")
        
    elif text == "🔁 সব নতুন করে পোস্ট করুন":
        status_msg = await update.message.reply_text("⚠️ <b>প্রসেস শুরু হচ্ছে, দয়া করে অপেক্ষা করুন...</b>", parse_mode="HTML")
        stats = await process_all_sources(context, status_msg=status_msg, force_repost=True)
        await status_msg.edit_text(f"✅ <b>ফোর্স রিপোস্ট সম্পন্ন!</b>\n\n📊 <b>রিপোর্ট:</b>\n🔗 সোর্স: <code>{stats['sources']}</code>\n📺 মোট স্ট্রিম: <code>{stats['total_streams']}</code>\n🆕 চ্যানেলে/ওয়েবে নতুন পোস্ট: <code>{stats['new_posts']}</code>\n❌ ব্যর্থ: <code>{stats['failed']}</code>", parse_mode="HTML")
