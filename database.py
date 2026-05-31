import hashlib
import time
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["all_in_one_reborn_db"]

users_col = db["users"]
sources_col = db["m3u_sources"]
posted_col = db["posted_streams"]
links_col = db["short_links"]
stats_col = db["app_stats"]

# 🎯 ফিক্স: এই ফাংশনটি না থাকার কারণেই আপনার বট ক্র্যাশ করছিল
async def create_indexes():
    """ডেটাবেস ফাস্ট করার জন্য এবং ক্র্যাশ এড়ানোর জন্য ইনডেক্স তৈরি করা"""
    try:
        await users_col.create_index("user_id", unique=True)
        await links_col.create_index("short_id", unique=True)
    except Exception as e:
        print(f"Index setup warning: {e}")

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

async def add_m3u_source(url: str, target: str = "both"):
    await sources_col.update_one({"url": url}, {"$set": {"url": url, "target": target, "added_at": datetime.utcnow()}}, upsert=True)

async def remove_m3u_source(url: str):
    await sources_col.delete_one({"url": url})
    deleted_streams = await posted_col.delete_many({"source_url": url})
    deleted_links = await links_col.delete_many({"source_url": url})
    return deleted_streams.deleted_count, deleted_links.deleted_count

async def get_m3u_sources():
    return [doc async for doc in sources_col.find({})]

async def save_posted_stream(stream_url: str, title: str, source_url: str, message_id: int, short_id: str, target: str = "both", logo: str = ""):
    await posted_col.update_one(
        {"title": title, "source_url": source_url}, 
        {"$set": {
            "title": title, "stream_url": stream_url, "source_url": source_url,
            "message_id": message_id, "short_id": short_id, "target": target, "logo": logo, "posted_at": datetime.utcnow()
        }}, 
        upsert=True
    )
    await stats_col.update_one({"stat_name": "total_posted"}, {"$inc": {"count": 1}}, upsert=True)

async def create_short_link(stream_url: str, referer: str, origin: str, cookie: str, user_agent: str, source_url: str, title: str = ""):
    short_id = hashlib.md5((stream_url + str(time.time())).encode()).hexdigest()[:12]
    await links_col.update_one(
        {"short_id": short_id}, 
        {"$set": {
            "short_id": short_id, "stream_url": stream_url, "title": title, "referer": referer,
            "origin": origin, "cookie": cookie, "user_agent": user_agent,
            "source_url": source_url, "created_at": datetime.utcnow()
        }}, 
        upsert=True
    )
    return short_id

async def get_stream_data(short_id: str):
    return await links_col.find_one({"short_id": short_id})

async def get_existing_post(title: str):
    return await posted_col.find_one({"title": title})

async def track_click(title=None):
    await stats_col.update_one({"stat_name": "total_clicks"}, {"$inc": {"count": 1}}, upsert=True)

async def get_stats():
    posted = await stats_col.find_one({"stat_name": "total_posted"})
    clicks = await stats_col.find_one({"stat_name": "total_clicks"})
    return (posted["count"] if posted else 0), (clicks["count"] if clicks else 0)

async def get_top_stream():
    return "Not enough data yet"

async def remove_expired_streams(source_url: str, active_stream_urls: list):
    if not active_stream_urls: return 0 
    db_streams = posted_col.find({"source_url": source_url})
    expired_urls = [doc["stream_url"] async for doc in db_streams if doc["stream_url"] not in active_stream_urls]
    if expired_urls:
        await posted_col.delete_many({"stream_url": {"$in": expired_urls}})
        await links_col.delete_many({"stream_url": {"$in": expired_urls}})
    return len(expired_urls)
