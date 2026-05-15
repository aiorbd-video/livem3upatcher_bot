import os
import re
import time
import json
import asyncio
import logging
import aiohttp
import secrets
import hashlib
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
    HAS_PSUTIL=True
except:
    HAS_PSUTIL=False


# =====================================================
# LOGGING
# =====================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger=logging.getLogger(__name__)


# =====================================================
# ENV
# =====================================================

BOT_TOKEN=os.getenv("BOT_TOKEN")
CHANNEL_ID=os.getenv("CHANNEL_ID")
BOT_USERNAME=os.getenv("BOT_USERNAME")
ADMIN_ID=int(os.getenv("ADMIN_ID","0"))
MONGO_URI=os.getenv("MONGO_URI")

required=[
BOT_TOKEN,
CHANNEL_ID,
BOT_USERNAME,
MONGO_URI
]

for x in required:
    if not x:
        raise Exception(
            "Missing ENV Variable"
        )

FORCE_CHANNELS=[
x.strip()
for x in
os.getenv(
"FORCE_CHANNELS",
""
).split(",")

if x.strip()
]

CHECK_TIME=int(
os.getenv(
"CHECK_TIME",
"300"
)
)

DELETE_TIME=300

HEADERS={

"User-Agent":
"Mozilla/5.0",

"Accept":"*/*"

}

START_TIME=time.time()

bd_tz=pytz.timezone(
"Asia/Dhaka"
)

HTTP_SESSION=None

USER_LIMIT={}

LIMIT_SECONDS=3

SOURCE_FAILS={}

MAX_FAILS=3

admin_state={}


# =====================================================
# DATABASE
# =====================================================

mongo_client=AsyncIOMotorClient(
MONGO_URI
)

db=mongo_client[
"all_in_one_reborn_db"
]

users_col=db["users"]

sources_col=db["m3u_sources"]

posted_col=db["posted_streams"]

links_col=db["short_links"]

stats_col=db["app_stats"]


# =====================================================
# INDEXES
# =====================================================

async def create_indexes():

    await users_col.create_index(
        "user_id"
    )

    await posted_col.create_index(
        [
            ("stream_hash",1)
        ]
    )

    await links_col.create_index(
        "short_id",
        unique=True
    )

    await sources_col.create_index(
        "url",
        unique=True
    )

    await links_col.create_index(
        "created_at",
        expireAfterSeconds=86400
    )


# =====================================================
# HELPERS
# =====================================================

def allow_user(user_id):

    now=time.time()

    if user_id in USER_LIMIT:

        if now-USER_LIMIT[user_id]<LIMIT_SECONDS:

            return False

    USER_LIMIT[user_id]=now

    return True


def make_stream_hash(
stream_url
):

    return hashlib.md5(

    stream_url.encode()

    ).hexdigest()



async def add_user(user_id):

    await users_col.update_one(

    {"user_id":user_id},

    {

    "$setOnInsert":{

    "user_id":user_id,

    "joined_at":
    datetime.utcnow(),

    "is_banned":
    False

    }

    },

    upsert=True

    )


async def get_all_users():

    return [

    doc["user_id"]

    async for doc in

    users_col.find(

    {

    "is_banned":

    {

    "$ne":True

    }

    }

    )

    ]


async def is_user_banned(
user_id
):

    user=await users_col.find_one(

    {

    "user_id":user_id

    }

    )

    return user.get(
    "is_banned",
    False
    ) if user else False


async def unban_user(
user_id
):

    result=await users_col.update_one(

    {

    "user_id":user_id

    },

    {

    "$set":{

    "is_banned":
    False

    }

    }

    )

    return result.modified_count>0



async def create_short_link(

stream_url,
referer,
origin,
cookie,
user_agent,
source_url

):

    short_id=secrets.token_urlsafe(
        8
    )

    await links_col.update_one(

    {

    "short_id":
    short_id

    },

    {

    "$set":{

    "short_id":
    short_id,

    "stream_url":
    stream_url,

    "referer":
    referer,

    "origin":
    origin,

    "cookie":
    cookie,

    "user_agent":
    user_agent,

    "source_url":
    source_url,

    "created_at":
    datetime.utcnow()

    }

    },

    upsert=True

    )

    return short_id


async def get_stream_data(
short_id
):

    return await links_col.find_one(

    {

    "short_id":
    short_id

    }

    )


async def track_click():

    await stats_col.update_one(

    {

    "stat_name":
    "total_clicks"

    },

    {

    "$inc":{
    "count":1
    }

    },

    upsert=True
    )


async def get_stats():

    posted=await stats_col.find_one(
        {
        "stat_name":
        "total_posted"
        }
    )

    clicks=await stats_col.find_one(
        {
        "stat_name":
        "total_clicks"
        }
    )

    return (
    posted["count"]
    if posted else 0,

    clicks["count"]
    if clicks else 0
    )


async def fetch_m3u_content(
url
):

    global HTTP_SESSION

    try:

        async with HTTP_SESSION.get(
            url,
            timeout=30
        ) as response:

            if response.status==200:

                SOURCE_FAILS[
                url
                ]=0

                return await response.text()

    except Exception as e:

        logger.error(e)

        SOURCE_FAILS[url]=(
        SOURCE_FAILS.get(
        url,
        0
        )+1
        )

    return None


async def on_startup(app):

    global HTTP_SESSION

    HTTP_SESSION=aiohttp.ClientSession(
        headers=HEADERS
    )

    await create_indexes()

    logger.info(
    "BOT STARTED"
    )


async def on_shutdown(app):

    global HTTP_SESSION

    if HTTP_SESSION:

        await HTTP_SESSION.close()
        # =====================================================
# PART 2
# FORCE JOIN + PARSER + HANDLERS + MAIN
# =====================================================

POST_QUEUE=asyncio.Queue()

admin_keyboard=ReplyKeyboardMarkup(
[
["➕ লিংক যুক্ত করুন","➖ লিংক মুছুন"],
["📊 সোর্স লাইভ স্ট্যাটাস","👥 মোট ইউজার"],
["🔁 সব নতুন করে পোস্ট করুন","🔄 ফোর্স চেক"],
["📢 ব্রডকাস্ট","📊 অ্যানালিটিক্স"],
["🚫 ইউজার ব্যান","✅ আনব্যান"],
["⚙️ সিস্টেম স্ট্যাটাস"]
],
resize_keyboard=True
)


# =====================================================
# DB EXTRA
# =====================================================

async def toggle_ban_user(
user_id,
ban_status
):

    result=await users_col.update_one(

    {
    "user_id":
    user_id
    },

    {

    "$set":{

    "is_banned":
    ban_status

    }

    }

    )

    return result.modified_count>0


async def add_m3u_source(
url,
target
):

    await sources_col.update_one(

    {

    "url":
    url

    },

    {

    "$set":{

    "url":
    url,

    "target":
    target,

    "added_at":
    datetime.utcnow()

    }

    },

    upsert=True
    )


async def get_m3u_sources():

    return [

    {

    "url":
    doc["url"],

    "target":
    doc.get(
    "target",
    "both"
    )

    }

    async for doc in

    sources_col.find({})

    ]


# =====================================================
# FORCE JOIN
# =====================================================

async def is_user_joined(
user_id,
context
):

    for channel in FORCE_CHANNELS:

        try:

            member=await context.bot.get_chat_member(
                channel,
                user_id
            )

            if member.status in [
            "left",
            "kicked"
            ]:

                return False

        except:

            return False

    return True



def get_force_join_keyboard(
payload=None
):

    buttons=[]

    for ch in FORCE_CHANNELS:

        buttons.append(

        [

        InlineKeyboardButton(

        f"✅ Join {ch}",

        url=f"https://t.me/{ch.replace('@','')}"

        )

        ]

        )

    buttons.append(

    [

    InlineKeyboardButton(

    "🔄 Joined",

    callback_data="check_join"

    )

    ]

    )

    return InlineKeyboardMarkup(
    buttons
    )


# =====================================================
# M3U PARSER
# =====================================================

def parse_m3u_playlist(
content
):

    streams=[]

    pattern=r'(#EXTINF:.*?)(https?://[^\\n]+)'

    matches=re.findall(
        pattern,
        content,
        re.DOTALL
    )

    for i in matches:

        extinf=i[0]

        url=i[1]

        title=f"Live {len(streams)+1}"

        group="লাইভ"

        g=re.search(
        r'group-title="([^"]+)"',
        extinf
        )

        if g:
            group=g.group(1)

        title_match=extinf.split(",")

        if len(title_match)>1:

            title=title_match[-1].strip()

        streams.append(

        {

        "title":
        title,

        "group":
        group,

        "url":
        url,

        "logo":"",
        "referer":"",
        "origin":"",
        "cookie":"",
        "user_agent":""

        }

        )

    return streams


# =====================================================
# TG POST
# =====================================================

async def post_to_tg_channel(

bot,
title,
category,
logo,
short_id

):

    deep_link=(
    f"https://t.me/"
    f"{BOT_USERNAME}"
    f"?start={short_id}"
    )

    text=(

    f"📡 <b>{title}</b>\n\n"

    f"📂 {category}\n\n"

    f"<a href='{deep_link}'>"

    f"▶️ দেখতে ক্লিক করুন"

    f"</a>"

    )

    try:

        if logo:

            msg=await bot.send_photo(

            chat_id=CHANNEL_ID,

            photo=logo,

            caption=text,

            parse_mode="HTML"

            )

        else:

            msg=await bot.send_message(

            chat_id=CHANNEL_ID,

            text=text,

            parse_mode="HTML"

            )

        return msg.message_id

    except Exception as e:

        logger.error(e)

        return None


# =====================================================
# QUEUE
# =====================================================

async def post_worker(bot):

    while True:

        item=await POST_QUEUE.get()

        try:

            result=await post_to_tg_channel(

                bot,

                item["title"],

                item["group"],

                item["logo"],

                item["short_id"]

            )

            item[
            "future"
            ].set_result(
            result
            )

        except Exception:

            item[
            "future"
            ].set_result(
            None
            )

        POST_QUEUE.task_done()


async def queue_post(
bot,
title,
group,
logo,
short_id
):

    future=asyncio.Future()

    await POST_QUEUE.put(

    {

    "title":title,
    "group":group,
    "logo":logo,
    "short_id":short_id,
    "future":future

    }

    )

    return await future


# =====================================================
# PROCESS SOURCES
# =====================================================

async def process_all_sources(
context
):

    sources=await get_m3u_sources()

    for src in sources:

        content=await fetch_m3u_content(
            src["url"]
        )

        if not content:
            continue

        streams=parse_m3u_playlist(
            content
        )

        for item in streams:

            stream_url=item["url"]

            stream_hash=make_stream_hash(
            stream_url
            )

            existing=await posted_col.find_one(

            {
            "stream_hash":
            stream_hash
            }

            )

            if existing:
                continue

            short_id=await create_short_link(

            stream_url,
            "",
            "",
            "",
            "",
            src["url"]

            )

            msg_id=await queue_post(

            context.bot,

            item["title"],

            item["group"],

            item["logo"],

            short_id

            )

            await posted_col.insert_one(

            {

            "stream_hash":
            stream_hash,

            "stream_url":
            stream_url,

            "title":
            item["title"],

            "message_id":
            msg_id

            }

            )


async def auto_checker_job(
context
):

    await process_all_sources(
        context
    )


# =====================================================
# START
# =====================================================

async def start_command(
update,
context
):

    user_id=update.effective_user.id

    if not allow_user(
        user_id
    ):
        return

    if await is_user_banned(
        user_id
    ):

        return await update.message.reply_text(
        "🚫 Banned"
        )

    await add_user(user_id)

    if not await is_user_joined(
        user_id,
        context
    ):

        return await update.message.reply_text(

        "Join channels first",

        reply_markup=
        get_force_join_keyboard()

        )

    await update.message.reply_text(
    "✅ Welcome"
    )


# =====================================================
# BUTTON
# =====================================================

async def button_handler(
update,
context
):

    query=update.callback_query

    await query.answer()

    if query.data=="check_join":

        ok=await is_user_joined(

        query.from_user.id,

        context

        )

        if ok:

            await query.message.edit_text(
            "✅ Verified"
            )


# =====================================================
# MESSAGE
# =====================================================

async def message_handler(
update,
context
):

    user_id=update.effective_user.id

    if user_id!=ADMIN_ID:
        return

    text=update.message.text


    if text=="👥 মোট ইউজার":

        total=len(
        await get_all_users()
        )

        await update.message.reply_text(

        f"Users:{total}"

        )


# =====================================================
# MAIN
# =====================================================

def main():

    app=(

    Application.builder()

    .token(BOT_TOKEN)

    .post_init(
    on_startup
    )

    .post_shutdown(
    on_shutdown
    )

    .build()

    )


    app.add_handler(

    CommandHandler(
    "start",
    start_command
    )

    )

    app.add_handler(

    CallbackQueryHandler(
    button_handler
    )

    )

    app.add_handler(

    MessageHandler(

    filters.TEXT &
    ~filters.COMMAND,

    message_handler

    )

    )


    app.job_queue.run_repeating(

    auto_checker_job,

    interval=CHECK_TIME,

    first=10

    )


    for _ in range(5):

        asyncio.get_event_loop().create_task(

        post_worker(
        app.bot
        )

        )


    logger.info(
    "BOT RUNNING"
    )

    app.run_polling()


if __name__=="__main__":

    main()
