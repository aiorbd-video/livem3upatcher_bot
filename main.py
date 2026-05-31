import logging
import asyncio
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import BOT_TOKEN, CHECK_TIME
from handlers import start_command, button_handler, admin_message_handler, auto_checker_job
import database as db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def drop_old_db_indexes():
    """বট স্টার্ট হওয়ার সময় সবার আগে পুরোনো এরর করা ইনডেক্স রিমুভ করবে"""
    try:
        # পুরোনো stream_hash_1 রুলস থাকলে মুছে দেবে
        await db.posted_col.drop_index("stream_hash_1")
        logger.info("✅ Old Duplicate Index (stream_hash_1) removed successfully!")
    except Exception as e:
        logger.info("ℹ️ Index already clean or not found.")

    # নতুন দরকারি ইনডেক্স তৈরি করবে
    try:
        await db.users_col.create_index("user_id", unique=True)
        await db.links_col.create_index("short_id", unique=True)
    except Exception:
        pass

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing! Please set it in environment variables.")
        return

    # 🎯 ফিক্স: বট রান হওয়ার আগেই ডেটাবেস ক্লিন করার কাজ শুরু করা
    loop = asyncio.get_event_loop()
    loop.run_until_complete(drop_old_db_indexes())

    app = Application.builder().token(BOT_TOKEN).build()

    # ==========================================
    # 🟢 Handlers (বটের ব্রেইন)
    # ==========================================
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_message_handler))

    # ==========================================
    # 🔄 Auto Background Checker
    # ==========================================
    app.job_queue.run_repeating(auto_checker_job, interval=CHECK_TIME, first=10)

    logger.info("Enterprise Extra Pro Bot is RUNNING Perfectly...")
    app.run_polling()

if __name__ == "__main__":
    main()
