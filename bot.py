import os
import re
import time
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

ADMIN_ID = int(
    os.getenv("ADMIN_ID")
)

FORCE_CHANNELS = os.getenv(
    "FORCE_CHANNELS",
    ""
).split(",")

CHECK_TIME = 300

LINKS_FILE = "links.txt"
USERS_FILE = "users.txt"
POSTED_FILE = "posted.txt"

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
# CREATE FILES
# =========================================================

for file in [
    LINKS_FILE,
    USERS_FILE,
    POSTED_FILE
]:

    if not os.path.exists(file):
        open(file, "w").close()

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

def get_posted():

    with open(POSTED_FILE, "r") as f:
        return set(f.read().splitlines())

def save_posted(link):

    posted = get_posted()

    if link not in posted:

        with open(POSTED_FILE, "a") as f:
            f.write(link + "\n")

def save_user(user_id):

    user_id = str(user_id)

    with open(USERS_FILE, "r") as f:
        users = f.read().splitlines()

    if user_id not in users:

        with open(USERS_FILE, "a") as f:
            f.write(user_id + "\n")

def get_users():

    with open(USERS_FILE, "r") as f:
        return f.read().splitlines()

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

        # EXTINF
        if line.startswith("#EXTINF"):

            current = {}

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

            # title
            if "," in line:

                title = line.split(",")[-1].strip()

                current["title"] = title

        # STREAM URL
        elif ".m3u8" in line:

            current["url"] = line.strip()

            channels.append(current)

    return channels

# =========================================================
# SEND CHANNEL POST
# =========================================================

def send_post(
    title,
    category,
    logo,
    stream_url
):

    encoded = urllib.parse.quote(
        stream_url
    )

    deep_link = (
        f"https://t.me/"
        f"{BOT_USERNAME}"
        f"?start={encoded}"
    )

    text = f"""
📡 {title}

📂 Category: {category}

🔥 Live Stream Updated

📝 Watch live streaming in HD quality.

⚠️ Access only via bot.

⚡ Auto Updated IPTV Feed
"""

    buttons = {
        "inline_keyboard": [
            [
                {
                    "text": "▶ WATCH NOW",
                    "url": deep_link
                }
            ]
        ]
    }

    try:

        # SEND PHOTO
        if logo:

            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={
                    "chat_id": CHANNEL_ID,
                    "photo": logo,
                    "caption": text,
                    "reply_markup": str(
                        buttons
                    ).replace("'", '"')
                },
                timeout=30
            )

        # SEND MESSAGE
        else:

            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                data={
                    "chat_id": CHANNEL_ID,
                    "text": text,
                    "reply_markup": str(
                        buttons
                    ).replace("'", '"'),
                    "disable_web_page_preview": True
                },
                timeout=30
            )

    except Exception as e:
        print("POST ERROR:", e)

# =========================================================
# FORCE JOIN CHECK
# =========================================================

async def check_force_join(
    update,
    context
):

    user_id = update.effective_user.id

    not_joined = []

    for channel in FORCE_CHANNELS:

        if not channel:
            continue

        try:

            member = await context.bot.get_chat_member(
                channel.strip(),
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
                    url=f"https://t.me/{ch.replace('@', '').strip()}"
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

    user_id = query.from_user.id

    not_joined = []

    for channel in FORCE_CHANNELS:

        if not channel:
            continue

        try:

            member = await context.bot.get_chat_member(
                channel.strip(),
                user_id
            )

            if member.status in [
                "left",
                "kicked"
            ]:
                not_joined.append(channel)

        except:
            not_joined.append(channel)

    if not not_joined:

        await query.message.reply_text(
            "✅ Access Granted"
        )

    else:

        await query.answer(
            "Join all channels first",
            show_alert=True
        )

# =========================================================
# AUTO CHECKER
# =========================================================

def checker():

    while True:

        try:

            links = get_links()

            posted = get_posted()

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

                    if stream_url in posted:
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

                    send_post(
                        title,
                        category,
                        logo,
                        stream_url
                    )

                    save_posted(
                        stream_url
                    )

                    print(
                        "POSTED:",
                        title
                    )

        except Exception as e:
            print(
                "CHECKER ERROR:",
                e
            )

        time.sleep(CHECK_TIME)

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

    args = context.args

    # STREAM ACCESS
    if args:

        stream_link = urllib.parse.unquote(
            args[0]
        )

        await update.message.reply_text(
            f"""
✅ Stream Access Granted

🔗 M3U8 Stream:

{stream_link}
"""
        )

        return

    # ADMIN
    if user_id == ADMIN_ID:

        await update.message.reply_text(
            "✅ Admin Panel",
            reply_markup=admin_keyboard
        )

    # USER
    else:

        await update.message.reply_text(
            "✅ Bot Access Granted"
        )

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

        waiting_add.add(
            user_id
        )

        await update.message.reply_text(
            "Send M3U URL"
        )

        return

    if user_id in waiting_add:

        save_link(text)

        waiting_add.remove(
            user_id
        )

        await update.message.reply_text(
            "✅ M3U Added"
        )

        return

    # =====================================================
    # DELETE LINK
    # =====================================================

    if text == "➖ Delete Link":

        waiting_delete.add(
            user_id
        )

        await update.message.reply_text(
            "Send Exact M3U URL"
        )

        return

    if user_id in waiting_delete:

        delete_link(text)

        waiting_delete.remove(
            user_id
        )

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

            msg = "\n\n".join(
                links
            )

            await update.message.reply_text(
                msg
            )

        return

    # =====================================================
    # USERS
    # =====================================================

    if text == "👥 Total Users":

        total = len(
            get_users()
        )

        await update.message.reply_text(
            f"👥 Total Users: {total}"
        )

        return

    # =====================================================
    # BROADCAST
    # =====================================================

    if text == "📢 Broadcast":

        waiting_broadcast.add(
            user_id
        )

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

        waiting_broadcast.remove(
            user_id
        )

        await update.message.reply_text(
            f"✅ Broadcast Sent: {sent}"
        )

        return

    # =====================================================
    # FORCE CHECK
    # =====================================================

    if text == "🔄 Force Check":

        await update.message.reply_text(
            "🔍 Checking..."
        )

        threading.Thread(
            target=checker,
            daemon=True
        ).start()

        await update.message.reply_text(
            "✅ Started"
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
