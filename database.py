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

async def create_indexes():
    """ডেটাবেস ফাস্ট করা এবং পুরোনো এরর করা ইনডেক্স রিমুভ করা"""
    try:
        # পুরোনো এরর করা ইনডেক্স মুছে ফেলা হচ্ছে
        try: await posted_col.drop_index("stream_hash_1")
        except: pass
            
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
    await posted_col.delete_many({"source_url": url})
    await links_col.delete_many({"source_url": url})
    return 0, 0

async def get_m3u_sources():
    return [doc async for doc in sources_col.find({})]

# 🎯 হেডারসহ সেভ করার ফাংশন
async def save_posted_stream(stream_url, title, source_url, message_id, short_id, target="both", logo="", headers=None):
    if headers is None: headers = {}
    doc = {
        "title": title, "stream_url": stream_url, "source_url": source_url,
        "message_id": message_id, "short_id": short_id, "target": target, "logo": logo,
        "referer": headers.get("referer", ""),
        "origin": headers.get("origin", ""),
        "cookie": headers.get("cookie", ""),
        "user_agent": headers.get("user_agent", ""),
        "posted_at": datetime.utcnow()
    }
    await posted_col.update_one({"title": title, "source_url": source_url}, {"$set": doc}, upsert=True)
    await stats_col.update_one({"stat_name": "total_posted"}, {"$inc": {"count": 1}}, upsert=True)

async def create_short_link(stream_url, referer, origin, cookie, user_agent, source_url, title=""):
    short_id = hashlib.md5((stream_url + str(time.time())).encode()).hexdigest()[:12]
    await links_col.update_one(
        {"short_id": short_id}, 
        {"$set": {"short_id": short_id, "stream_url": stream_url, "title": title, "referer": referer, "origin": origin, "cookie": cookie, "user_agent": user_agent, "source_url": source_url, "created_at": datetime.utcnow()}}, 
        upsert=True
    )
    return short_id

async def get_stream_data(short_id): return await links_col.find_one({"short_id": short_id})
async def get_stats():
    posted = await stats_col.find_one({"stat_name": "total_posted"})
    clicks = await stats_col.find_one({"stat_name": "total_clicks"})
    return (posted["count"] if posted else 0), (clicks["count"] if clicks else 0)
