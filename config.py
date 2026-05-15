import os
import time
import pytz

# =========================================================
# CONFIG
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
    "Accept": "*/*",
}

START_TIME = time.time()
bd_tz = pytz.timezone("Asia/Dhaka")

required_env = [BOT_TOKEN, CHANNEL_ID, BOT_USERNAME, MONGO_URI]
for item in required_env:
    if not item:
        raise RuntimeError("Missing required environment variable. Please check your .env or server settings.")
