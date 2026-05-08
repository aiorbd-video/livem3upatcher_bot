import os
import re
import time
import json
import base64
import threading
import urllib.parse
import requests

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
# VARIABLES
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
BOT_USERNAME = os.getenv("BOT_USERNAME")

ADMIN_ID = int(os.getenv("ADMIN_ID"))

FORCE_CHANNELS = os.getenv(
    "FORCE_CHANNELS",
    ""
).split(",")

CHECK_TIME = int(
    os.getenv("CHECK_TIME", "300")
)

# =========================================================
# FILES
# =========================================================

LINKS_FILE = "links.txt"
USERS_FILE = "users.txt"
POSTED_FILE = "posted.json"

# =========================================================
# CREATE FILES
# =========================================================

for file in [
    LINKS_FILE,
    USERS_FILE
]:
    if not os.path.exists(file):
        open(file, "w").close()

if not os.path.exists(POSTED_FILE):
    with open(POSTED_FILE, "w") as f:
        json.dump({}, f)

# =========================================================
# REQUEST SESSION
# =========================================================

session = requests.Session()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive"
}

# =========================================================
# MEMORY
# =========================================================

waiting_add = set()
waiting_delete = set()
waiting_broadcast = set()

# =========================================================
# ADMIN MENU
# =========================================================

admin_keyboard = ReplyKeyboardMarkup(
    [
        ["➕ Add Link", "➖ Delete Link"],
        ["📃 All Links", "👥 Total Users"],
        ["📢 Broadcast", "🔄 Force Check"]
    ],
    resize_keyboard=True
)

# =========================================================
# FILE FUNCTIONS
# =========================================================

def get_users():

    with open(USERS_FILE, "r") as f:
        return f.read().splitlines()

def save_user(user_id):

    user_id = str(user_id)

    users = get_users()

    if user_id not in users:

        with open(USERS_FILE, "a") as f:
            f.write(user_id + "\n")

def get_links():

    with open(LINKS_FILE, "r") as f:

        return [
            x.strip()
            for x in f.readlines()
            if x.strip()
        ]

def save_link(link):

    links = get_links()

    if link not in links:

        with open(LINKS_FILE, "a") as f:
            f.write(link + "\n")

def delete_link(link):

    links = get_links()

    if link in links:

        links.remove(link)

        with open(LINKS_FILE, "w") as f:

            for l in links:
                f.write(l + "\n")

# =========================================================
# POSTED DATABASE
# =========================================================

def get_posted():

    with open(POSTED_FILE, "r") as f:
        return json.load(f)

def save_posted(data):

    with open(POSTED_FILE, "w") as f:
        json.dump(data, f, indent=4)

# =========================================================
# FETCH URL
# =========================================================

def fetch_url(url):

    try:

        response = session.get(
            url,
            headers=HEADERS,
            timeout=30,
            allow_redirects=True
        )

        if response.status_code == 200:
            return response.text

    except Exception as e:
        print("FETCH ERROR:", e)

    return None

# =========================================================
# PARSE M3U
# =========================================================

def parse_m3u(content):

    channels = []

    lines = content.splitlines()

    current = {}

    for line in lines:

        line = line.strip()

        if line.startswith("#EXTINF"):

            current = {}

            # title
            if "," in line:
                current["title"] = line.split(",")[-1].strip()

            # logo
            logo_match = re.search(
                r'tvg-logo="([^"]+)"',
                line
            )

            if logo_match:
                current["logo"] = logo_match.group(1)

            # group
            group_match = re.search(
                r'group-title="([^"]+)"',
                line
            )

            if group_match:
                current["group"] = group_match.group(1)

        elif ".m3u8" in line:

            current["url"] = line.strip()

            channels.append(current)

    return channels

# =========================================================
# ENCODE LINK
# =========================================================

def encode_link(link):

    encoded = base64.urlsafe_b64encode(
        link.encode()
    ).decode()

    return encoded

def decode_link(encoded):

    decoded = base64.urlsafe_b64decode(
        encoded.encode()
    ).decode()

    return decoded

# =========================================================
# SEND POST
# =========================================================

def send_post(
    title,
    category,
    logo,
    stream_url
):

    encoded = encode_link(stream_url)

    deep_link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start={encoded}"
    )

    text = f"""
📡 <b>{title}</b>

📂 <b>Category:</b> {category}

🔥 <b>Live Stream Updated</b>

📝 HD live streaming available.

🔗 <a href="{deep_link}">WATCH STREAM</a>

⚡ Auto Updated IPTV Feed
"""

    try:

        # PHOTO POST
        if logo:

            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": CHANNEL_ID,
                    "photo": logo,
                    "caption": text,
                    "parse_mode": "HTML"
                },
                timeout=30
            )

        # TEXT POST
        else:

            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": CHANNEL_ID,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True
                },
                timeout=30
            )

    except Exception as e:
        print("POST ERROR:", e)

# =========================================================
# FORCE JOIN
# =========================================================

async def check_force_join(
    update,
    context
):

    user_id = update.effective_user.id

    not_joined = []

    for channel in FORCE_CHANNELS:

        channel = channel.strip()

        if not channel:
            continue

        try:

            member = await context.bot.get_chat_member(
                channel,
                user_id
            )

            if member.status in [
                "left",
                "kicked"
            ]:
                not_joined.append(channel)

        except:
            not_joined.append(channel)

    if not_joined:

        buttons = []

        for ch in not_joined:

            buttons.append([
                InlineKeyboardButton(
                    f"Join {ch}",
                    url=f"https://t.me/{ch.replace('@', '')}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                "✅ Joined",
                callback_data="check_join"
            )
        ])

        keyboard = InlineKeyboardMarkup(
            buttons
        )

        await update.message.reply_text(
            "❌ Join All Channels First",
            reply_markup=keyboard
        )

        return False

    return True

# =========================================================
# CALLBACK
# =========================================================

async def button_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    await query.message.reply_text(
        "✅ Verification Complete"
    )

# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    ok = await check_force_join(
        update,
        context
    )

    if not ok:
        return

    user_id = update.effective_user.id

    save_user(user_id)

    # =====================================================
    # STREAM ACCESS
    # =====================================================

    if context.args:

        try:

            encoded = context.args[0]

            stream_link = decode_link(
                encoded
            )

            await update.message.reply_text(
                f"""
✅ Stream Access Granted

🔗 M3U8 STREAM:

{stream_link}
"""
            )

            return

        except Exception as e:

            await update.message.reply_text(
                f"ERROR:\n{e}"
            )

            return

    # =====================================================
    # ADMIN
    # =====================================================

    if user_id == ADMIN_ID:

        await update.message.reply_text(
            "✅ Admin Panel",
            reply_markup=admin_keyboard
        )

    # =====================================================
    # USER
    # =====================================================

    else:

        await update.message.reply_text(
            "✅ Bot Access Granted"
        )

# =========================================================
# AUTO CHECKER
# =========================================================

def checker():

    while True:

        try:

            posted = get_posted()

            links = get_links()

            for m3u_url in links:

                print(
                    "CHECKING:",
                    m3u_url
                )

                content = fetch_url(
                    m3u_url
                )

                if not content:
                    continue

                streams = parse_m3u(
                    content
                )

                for item in streams:

                    stream_url = item.get(
                        "url"
                    )

                    if not stream_url:
                        continue

                    title = item.get(
                        "title",
                        "Unknown Stream"
                    )

                    category = item.get(
                        "group",
                        "Live TV"
                    )

                    logo = item.get(
                        "logo",
                        ""
                    )

                    old = posted.get(title)

                    # NEW STREAM
                    if not old:

                        send_post(
                            title,
                            category,
                            logo,
                            stream_url
                        )

                        posted[title] = stream_url

                        save_posted(posted)

                        print(
                            "NEW:",
                            title
                        )

                    # UPDATED STREAM
                    elif old != stream_url:

                        send_post(
                            title,
                            category,
                            logo,
                            stream_url
                        )

                        posted[title] = stream_url

                        save_posted(posted)

                        print(
                            "UPDATED:",
                            title
                        )

        except Exception as e:
            print(
                "CHECKER ERROR:",
                e
            )

        time.sleep(CHECK_TIME)

# =========================================================
# MESSAGE HANDLER
# =========================================================

async def messages(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    text = update.message.text.strip()

    save_user(user_id)

    if user_id != ADMIN_ID:
        return

    # =====================================================
    # ADD LINK
    # =====================================================

    if text == "➕ Add Link":

        waiting_add.add(user_id)

        await update.message.reply_text(
            "Send M3U URL"
        )

        return

    if user_id in waiting_add:

        save_link(text)

        waiting_add.remove(user_id)

        await update.message.reply_text(
            "✅ M3U Added"
        )

        return

    # =====================================================
    # DELETE LINK
    # =====================================================

    if text == "➖ Delete Link":

        waiting_delete.add(user_id)

        await update.message.reply_text(
            "Send Exact M3U URL"
        )

        return

    if user_id in waiting_delete:

        delete_link(text)

        waiting_delete.remove(user_id)

        await update.message.reply_text(
            "✅ Link Deleted"
        )

        return

    # =====================================================
    # ALL LINKS
    # =====================================================

    if text == "📃 All Links":

        links = get_links()

        if not links:

            await update.message.reply_text(
                "No Links Found"
            )

        else:

            msg = "\n\n".join(links)

            await update.message.reply_text(
                msg
            )

        return

    # =====================================================
    # USERS
    # =====================================================

    if text == "👥 Total Users":

        total = len(get_users())

        await update.message.reply_text(
            f"👥 Total Users: {total}"
        )

        return

    # =====================================================
    # BROADCAST
    # =====================================================

    if text == "📢 Broadcast":

        waiting_broadcast.add(user_id)

        await update.message.reply_text(
            "Send Broadcast Message"
        )

        return

    if user_id in waiting_broadcast:

        users = get_users()

        sent = 0

        for user in users:

            try:

                await context.bot.send_message(
                    chat_id=int(user),
                    text=text
                )

                sent += 1

            except:
                pass

        waiting_broadcast.remove(user_id)

        await update.message.reply_text(
            f"✅ Broadcast Sent: {sent}"
        )

        return

    # =====================================================
    # FORCE CHECK
    # =====================================================

    if text == "🔄 Force Check":

        threading.Thread(
            target=checker,
            daemon=True
        ).start()

        await update.message.reply_text(
            "✅ Force Check Started"
        )

# =========================================================
# MAIN
# =========================================================

def main():

    app = Application.builder().token(
        BOT_TOKEN
    ).build()

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button_callback
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT,
            messages
        )
    )

    threading.Thread(
        target=checker,
        daemon=True
    ).start()

    print("BOT RUNNING...")

    app.run_polling()

if __name__ == "__main__":
    main()
