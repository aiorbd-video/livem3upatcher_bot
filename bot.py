import os
import time
import threading
import requests

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ==========================================
# VARIABLES FROM RAILWAY
# ==========================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")

# comma separated ids/usernames
# example:
# FORCE_CHANNELS=@ch1,@ch2,@ch3

FORCE_CHANNELS = os.getenv(
    "FORCE_CHANNELS",
    ""
).split(",")

# admin telegram numeric id
# example:
# ADMIN_ID=123456789

ADMIN_ID = int(os.getenv("ADMIN_ID"))

CHECK_TIME = 300

LINKS_FILE = "links.txt"
USERS_FILE = "users.txt"

# ==========================================
# CREATE FILES
# ==========================================

for file in [LINKS_FILE, USERS_FILE]:

    if not os.path.exists(file):
        open(file, "w").close()

# ==========================================
# MEMORY
# ==========================================

last_links = set()

waiting_add = set()
waiting_delete = set()
waiting_broadcast = set()

# ==========================================
# ADMIN MENU
# ==========================================

admin_keyboard = ReplyKeyboardMarkup(
    [
        ["➕ Add Link", "➖ Delete Link"],
        ["📃 All Links", "👥 Total Users"],
        ["📢 Broadcast", "🔄 Force Check"]
    ],
    resize_keyboard=True
)

# ==========================================
# FILE SYSTEM
# ==========================================

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

# ==========================================
# M3U PARSER
# ==========================================

def parse_m3u(content):

    found = []

    for line in content.splitlines():

        line = line.strip()

        if ".m3u8" in line:
            found.append(line)

    return found

# ==========================================
# FORCE JOIN CHECK
# ==========================================

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

            if member.status in ["left", "kicked"]:
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

        keyboard = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(
            "❌ Join All Channels First",
            reply_markup=keyboard
        )

        return False

    return True

# ==========================================
# CALLBACK BUTTON
# ==========================================

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

            if member.status in ["left", "kicked"]:
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

# ==========================================
# AUTO CHECKER
# ==========================================

def checker(app):

    global last_links

    while True:

        try:

            all_current = set()

            links = get_links()

            for url in links:

                try:

                    r = requests.get(url, timeout=20)

                    streams = parse_m3u(r.text)

                    all_current.update(streams)

                except:
                    pass

            new_links = all_current - last_links

            for stream in new_links:

                try:

                    app.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=f"🔴 Updated Stream\n\n{stream}"
                    )

                    print("POSTED:", stream)

                except Exception as e:
                    print(e)

            last_links = all_current

        except Exception as e:
            print(e)

        time.sleep(CHECK_TIME)

# ==========================================
# START
# ==========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    ok = await check_force_join(update, context)

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

# ==========================================
# MESSAGES
# ==========================================

async def messages(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    text = update.message.text

    save_user(user_id)

    if user_id != ADMIN_ID:
        return

    # ADD LINK

    if text == "➕ Add Link":

        waiting_add.add(user_id)

        await update.message.reply_text(
            "Send M3U Link"
        )

        return

    if user_id in waiting_add:

        save_link(text)

        waiting_add.remove(user_id)

        await update.message.reply_text(
            "✅ Link Added"
        )

        return

    # DELETE LINK

    if text == "➖ Delete Link":

        waiting_delete.add(user_id)

        await update.message.reply_text(
            "Send Exact Link To Delete"
        )

        return

    if user_id in waiting_delete:

        delete_link(text)

        waiting_delete.remove(user_id)

        await update.message.reply_text(
            "✅ Link Deleted"
        )

        return

    # ALL LINKS

    if text == "📃 All Links":

        links = get_links()

        if not links:

            await update.message.reply_text(
                "No Links"
            )

        else:

            msg = "\n\n".join(links)

            await update.message.reply_text(msg)

        return

    # TOTAL USERS

    if text == "👥 Total Users":

        total = len(get_users())

        await update.message.reply_text(
            f"👥 Total Users: {total}"
        )

        return

    # BROADCAST

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
            f"✅ Broadcast Sent To {sent} Users"
        )

        return

    # FORCE CHECK

    if text == "🔄 Force Check":

        await update.message.reply_text(
            "🔍 Checking..."
        )

        try:

            all_current = set()

            links = get_links()

            for url in links:

                try:

                    r = requests.get(url, timeout=20)

                    streams = parse_m3u(r.text)

                    all_current.update(streams)

                except:
                    pass

            new_links = all_current - last_links

            for stream in new_links:

                try:

                    await context.bot.send_message(
                        chat_id=CHANNEL_ID,
                        text=f"🔴 Updated Stream\n\n{stream}"
                    )

                except:
                    pass

            await update.message.reply_text(
                f"✅ Done\nNew Links: {len(new_links)}"
            )

        except Exception as e:

            await update.message.reply_text(str(e))

# ==========================================
# MAIN
# ==========================================

def main():

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(button_callback)
    )

    app.add_handler(
        MessageHandler(filters.TEXT, messages)
    )

    thread = threading.Thread(
        target=checker,
        args=(app,),
        daemon=True
    )

    thread.start()

    print("BOT RUNNING...")

    app.run_polling()

if __name__ == "__main__":
    main()
