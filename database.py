import secrets
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure
from config import MONGO_URI
from utils import make_stream_hash

mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["all_in_one_reborn_db"]

users_col = db["users"]
sources_col = db["m3u_sources"]
posted_col = db["posted_streams"]
links_col = db["short_links"]
stats_col = db["app_stats"]

async def create_indexes():
    await users_col.create_index("user_id")
    await users_col.create_index("is_banned")
    await sources_col.create_index("url", unique=True)
    
    # ইনডেক্স কনফ্লিক্ট হ্যান্ডলিং
    try:
        await posted_col.create_index("stream_hash", unique=True)
    except OperationFailure:
        await posted_col.drop_index("stream_hash_1")
        await posted_col.create_index("stream_hash", unique=True)
        
    await posted_col.create_index("source_url")
    await posted_col.create_index([("title", 1), ("source_url", 1)])
    
    try:
        await links_col.create_index("short_id", unique=True)
    except OperationFailure:
        await links_col.drop_index("short_id_1")
        await links_col.create_index("short_id", unique=True)
        
    await links_col.create_index("source_url")
    await links_col.create_index("created_at", expireAfterSeconds=86400)
    
    try:
        await stats_col.create_index("stat_name", unique=True)
    except OperationFailure:
        await stats_col.drop_index("stat_name_1")
        await stats_col.create_index("stat_name", unique=True)

async def add_user(user_id: int):
    await users_col.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {"user_id": user_id, "joined_at": datetime.utcnow(), "is_banned": False}},
        upsert=True,
    )

async def get_all_users():
    return [doc["user_id"] async for doc in users_col.find({"is_banned": {"$ne": True}})]

async def is_user_banned(user_id: int):
    user = await users_col.find_one({"user_id": user_id})
    return user.get("is_banned", False) if user else False

async def toggle_ban_user(user_id: int, ban_status: bool):
    result = await users_col.update_one({"user_id": user_id}, {"$set": {"is_banned": ban_status}})
    return result.modified_count > 0

async def add_m3u_source(url: str, target: str):
    await sources_col.update_one(
        {"url": url},
        {"$set": {"url": url, "target": target, "added_at": datetime.utcnow()}},
        upsert=True,
    )

async def remove_m3u_source(url: str):
    await sources_col.delete_one({"url": url})
    deleted_streams = await posted_col.delete_many({"source_url": url})
    deleted_links = await links_col.delete_many({"source_url": url})
    return deleted_streams.deleted_count, deleted_links.deleted_count

async def get_m3u_sources():
    return [{"url": doc["url"], "target": doc.get("target", "both")} async for doc in sources_col.find({})]

async def save_posted_stream(stream_url: str, title: str, source_url: str, message_id: int, short_id: str, target: str):
    stream_hash = make_stream_hash(stream_url)
    result = await posted_col.update_one(
        {"stream_hash": stream_hash},
        {
            "$set": {
                "title": title, "stream_hash": stream_hash, "stream_url": stream_url,
                "source_url": source_url, "message_id": message_id, "short_id": short_id,
                "target": target, "posted_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    if result.upserted_id is not None:
        await stats_col.update_one({"stat_name": "total_posted"}, {"$inc": {"count": 1}}, upsert=True)

async def create_short_link(stream_url, referer, origin, cookie, user_agent, source_url, title=""):
    short_id = secrets.token_urlsafe(8)
    await links_col.update_one(
        {"short_id": short_id},
        {
            "$set": {
                "short_id": short_id, "stream_url": stream_url, "title": title, "referer": referer,
                "origin": origin, "cookie": cookie, "user_agent": user_agent, "source_url": source_url,
                "created_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    return short_id

async def get_stream_data(short_id: str):
    return await links_col.find_one({"short_id": short_id})

async def track_click(stream_name: str | None = None):
    await stats_col.update_one({"stat_name": "total_clicks"}, {"$inc": {"count": 1}}, upsert=True)
    if stream_name:
        await stats_col.update_one({"stat_name": f"stream::{stream_name}"}, {"$inc": {"count": 1}}, upsert=True)

async def get_stats():
    posted = await stats_col.find_one({"stat_name": "total_posted"})
    clicks = await stats_col.find_one({"stat_name": "total_clicks"})
    return (posted["count"] if posted else 0), (clicks["count"] if clicks else 0)

async def get_top_stream():
    doc = await stats_col.find({"stat_name": {"$regex": r"^stream::"}}).sort("count", -1).limit(1).to_list(length=1)
    return doc[0]["stat_name"].replace("stream::", "") if doc else "No Data"
