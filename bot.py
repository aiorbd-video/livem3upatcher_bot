import os
import re
import time
import threading
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

# ======================================================
# VARIABLES
# ======================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

FORCE_CHANNELS = os.getenv(
    "FORCE_CHANNELS",
    ""
).split(",")

ADMIN_ID = int(os.getenv("ADMIN_ID"))

CHECK_TIME = 300

LINKS_FILE = "links.txt"
USERS_FILE = "users.txt"
POSTED_FILE = "posted.txt"

# ======================================================
# REQUEST SESSION
# ======================================================

session = requests.Session()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Connection": "keep-alive"
}

# ======================================================
# CREATE FILES
# ======================================================

for file in [
    LINKS_FILE,
    USERS_FILE,
    POSTED_FILE
]:

    if not os.path.exists(file):
        open(file, "w").close()

# ======================================================
# MEMORY
# ======================================================

waiting_add = set()
waiting_delete = set()
waiting_broadcast = set()

# ======================================================
# ADMIN MENU
# ======================================================

admin_keyboard = ReplyKeyboardMarkup(
    [
        ["➕ Add Link", "➖ Delete Link"],
        ["📃 All Links", "👥 Total Users"],
        ["📢 Broadcast", "🔄 Force Check"]
    ],
    resize_keyboard=True
)

# ======================================================
# FILE FUNCTIONS
# ======================================================

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

# ======================================================
# TELEGRAM POST
# ======================================================

def send_channel_post(text):

    try:

        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={
                "chat_id": CHANNEL_ID,
                "text": text,
                "disable_web_page_preview": True
            },
            timeout=30
        )

    except Exception as e:
        print("POST ERROR:", e)

# ======================================================
# PARSE M3U
# ======================================================

def parse_m3u(content):

    results = []

    regex = r'https?:\/\/[^\s"]+\.m3u8[^\s"]*'

    matches = re.findall(regex, content)

    for link in matches:

        link = link.strip()

        if link not in results:
            results.append(link)

    return results

# ======================================================
# FETCH URL
# ======================================================

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

# ======================================================
# FORCE JOIN
# ======================================================

async def check_force_join(update, context):

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
                    text=f"Join {ch}",
                    url=f"https://t.me/{ch.replace('@', '').strip()}"
                )
            ])

        buttons.append([
            InlineKeyboardButton(
                text="✅ Joined",
                callback_data="check_join"
            )
        ])

        keyboard = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(
            "❌ Join All Channels First",
            reply_markup=keyboard
        )

        return False

    return True

# ======================================================
# CALLBACK
# ======================================================

async def button_callback(update, context):

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

# ======================================================
# AUTO CHECKER
# ======================================================

def checker():

    while True:

        try:

            links = get_links()

            posted = get_posted()

            for m3u_url in links:

                print("CHECKING:", m3u_url)

                content = fetch_url(m3u_url)

                if not content:
                    continue

                streams = parse_m3u(content)

                for stream in streams:

                    if stream in posted:
                        continue

                    text = (
                        "🔴 Updated Stream\n\n"
                        f"{stream}"
                    )

                    send_channel_post(text)

                    save_posted(stream)

                    print("POSTED:", stream)

        except Exception as e:
            print("CHECKER ERROR:", e)

        time.sleep(CHECK_TIME)

# ======================================================
# START
# ======================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ok = await check_force_join(
        update,
        context
    )

    if not ok:
        return

    user_id = update.effective_user.id

    save_user(user_id)

    if user_id == ADMIN_ID:

        await update.message.reply_text(
            "✅ Admin Panel",
            reply_markup=admin_keyboard
        )

    else:

        await update.message.reply_text(
            "✅ Bot Access Granted"
        )

# ======================================================
# MESSAGES
# ======================================================

async def messages(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    text = update.message.text.strip()

    save_user(user_id)

    if user_id != ADMIN_ID:
        return

    # ==================================================
    # ADD LINK
    # ==================================================

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
            "✅ M3U Added Successfully"
        )

        return

    # ==================================================
    # DELETE LINK
    # ==================================================

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

    # ==================================================
    # ALL LINKS
    # ==================================================

    if text == "📃 All Links":

        links = get_links()

        if not links:

            await update.message.reply_text(
                "No Links Found"
            )

        else:

            msg = "\n\n".join(links)

            await update.message.reply_text(msg)

        return

    # ==================================================
    # USERS
    # ==================================================

    if text == "👥 Total Users":

        total = len(get_users())

        await update.message.reply_text(
            f"👥 Total Users: {total}"
        )

        return

    # ==================================================
    # BROADCAST
    # ==================================================

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

    # ==================================================
    # FORCE CHECK
    # ==================================================

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

# ======================================================
# MAIN
# ======================================================

def main():

    app = Application.builder().token(
        BOT_TOKEN
    ).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(button_callback)
    )

    app.add_handler(
        MessageHandler(filters.TEXT, messages)
    )

    threading.Thread(
        target=checker,
        daemon=True
    ).start()

    print("BOT RUNNING...")

    app.run_polling()

if __name__ == "__main__":
    main()
